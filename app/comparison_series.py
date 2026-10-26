"""Пять заранее выбранных существующих случаев. Без нового набора или произвольных заданий."""

import localization as loc

import copy
import hashlib
import html
import json
import shlex

import input_search as search
import input_search_adapter as adapter
import live_search as live
import nebius_client as client
import reprocase as app

CASES = (("time_fixed", "units", "fixed"), ("seed_buggy", "seed", "buggy"),
         ("seed_fixed", "seed", "fixed"), ("step_buggy", "step", "buggy"), ("step_fixed", "step", "fixed"))
MAX_TOTAL_REQUESTS = 20
MAX_TOTAL_BYTES = 320000
HISTORY_FILE = "outputs/live-search-olj93z6v/result.json"
LABELS = {"time_fixed": "Метры и миллиметры — исправный", "seed_buggy": "Генератор — ошибочный",
          "seed_fixed": "Генератор — исправный", "step_buggy": "Шаг расчёта — ошибочный", "step_fixed": "Шаг расчёта — исправный"}
STATES = {"RUNNING": "выполняется", "COMPLETED": "завершено", "CANCELLED": "отменено", "ERROR": "ошибка",
          "NOT_RUN": "не запускалось", "PLAN_CHANGED": "остановлено: изменился план"}


def source_hashes():
    names = (*search.SOURCE_FILES, "comparison_series.py", "live_search.py", "input_search_adapter.py",
             "benchmark_adapter.py", "nebius_client.py", *(f"examples/search_{name}_ambiguous.json" for name in ("units", "seed", "step")))
    return {name: hashlib.sha256((search.ROOT / name).read_bytes()).hexdigest() for name in names}


def load_plan():
    cases = [{"id": identifier, "variant": variant,
              "case": app.load_case(search.ROOT / f"examples/search_{name}_ambiguous.json")} for identifier, name, variant in CASES]
    return {"schema_version": 1, "cases": cases, "source_sha256": source_hashes(),
            "model": live.MODEL, "endpoint": live.HOST,
            "limits": {"sessions": 5, "trials_per_session": 3, "requests_per_session": 4, "requests_total": 20,
                       "completion_tokens_per_request": 1024, "completion_tokens_total": 20480,
                       "request_bytes": 16000, "request_bytes_total": 320000}}


def consent_text(plan):
    lines = ["Сравнение пяти существующих учебных случаев через Nebius", f"Модель: {live.MODEL}; адрес: {live.HOST}"]
    for row in plan["cases"]:
        lines.append(LABELS[row["id"]] + ": " + app.json_text(row["case"]["inputs"]))
    lines += ["Поля текущей формы не используются: запускаются только перечисленные пять случаев.",
              "В сервис уйдут правило, числа, наблюдение, четыре разрешённых кандидата и результаты проверок. Вариант реализации, контрольный перебор и метки оценщика не отправляются.",
              "На случай: до 3 проб и 4 запросов. Всего: до 20 запросов, 20480 токенов ответа (включая reasoning), 320000 байт тел; на запрос до 1024 токенов ответа и 16000 байт.",
              "Первая ошибка или отмена останавливает всю серию. Автоматических повторов нет. Ключ скрыт, только в памяти. Отмена не доказывает отмену отправленного запроса или отсутствие списания.",
              "Ориентир $0.15, не денежный лимит. Расчёт $0.114432 предполагает по 16000 входных токенов на запрос, $0.30 вход / $0.90 выход за миллион, без кеша. Байт не равен токену, счёт поставщика может отличаться.",
              "Прежний ошибочный travel_time не повторяется и учитывается только отдельно как история. На этом открытом наборе c02 сразу выявляет все три учебные ошибки; серия не докажет общего преимущества ИИ.",
              "Разрешить отправку этих пяти случаев в указанных пределах?"]
    return "\n\n".join(lines)


class Budget:
    def __init__(self):
        self.requests = 0
        self.bytes = 0

    def reserve(self):
        if self.requests >= MAX_TOTAL_REQUESTS:
            raise client.ClientError("Общий предел 20 запросов исчерпан; отправки больше нет.")
        self.requests += 1

    def add_bytes(self, count):
        if count > live.MAX_REQUEST_BYTES or self.bytes + count > MAX_TOTAL_BYTES:
            raise client.ClientError("Превышен общий предел сериализованных байт.")
        self.bytes += count


class SeriesSession(live.LiveSession):
    def __init__(self, cancelled, budget):
        super().__init__(cancelled)
        self.budget = budget

    def _before_send(self):
        super()._before_send()
        self.budget.reserve()

    def _send(self, body, api_key):
        self.budget.add_bytes(len(body))
        return super()._send(body, api_key)


def historical_reference():
    path = search.ROOT / HISTORY_FILE
    if not path.is_file():
        return {"status": "UNAVAILABLE", "included_in_new_metrics": False}
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        return {"status": "HISTORICAL", "file": HISTORY_FILE, "sha256": hashlib.sha256(raw).hexdigest(),
                "model_probes": data["result"]["probe_attempts"], "baseline_probes": 2,
                "tokens": data["accounting"]["tokens"], "included_in_new_metrics": False}
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "UNAVAILABLE", "included_in_new_metrics": False}


def metrics(rows, method):
    result = {"planned_faulty": 2, "planned_fixed": 3, "started_faulty": 0, "started_fixed": 0,
              "found_faults": 0, "unconfirmed_started_faults": 0, "false_alarms": 0, "probe_attempts": 0, "unknown_probe_counts": 0}
    for row in rows:
        entry = row.get(method)
        if entry is None:
            continue
        observed = entry.get("result") if method == "model" else entry
        faulty = row["variant"] == "buggy"
        found = bool(observed and observed["status"] == "REPRODUCED")
        result["started_faulty" if faulty else "started_fixed"] += 1
        result["found_faults"] += faulty and found
        result["unconfirmed_started_faults"] += faulty and not found
        result["false_alarms"] += not faulty and found
        result["probe_attempts"] += observed["probe_attempts"] if observed else 0
        result["unknown_probe_counts"] += observed is None
    result["unrun_faulty"] = 2 - result["started_faulty"]
    result["unrun_fixed"] = 3 - result["started_fixed"]
    return result


def refresh(record):
    events = [dict(event, series_case_id=row["id"]) for row in record["rows"] if row.get("model") for event in row["model"]["events"]]
    attempted = [event for event in events if event["network_attempted"]]
    tokens = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [(event.get("usage") or {}).get(name) for event in attempted]
        tokens[name] = sum(values) if values and all(value is not None for value in values) else None
    record.update(events=events, accounting={"network_attempts": len(attempted), "tokens": tokens},
                  cost=live.estimate_cost(events), metrics={"model": metrics(record["rows"], "model"), "baseline": metrics(record["rows"], "baseline")})
    record["origin"] = "REAL_API" if attempted else "LOCAL_NO_REQUEST"
    m = record["metrics"]["model"]
    shown_tokens = {name: value if value is not None else "неизвестно" for name, value in tokens.items()}
    text = ["Серия завершена." if record["state"] == "COMPLETED" else "Состояние серии: " + STATES[record["state"]],
            f"Найдено новых внесённых ошибок: {m['found_faults']} из 2; не подтверждено среди запущенных: {m['unconfirmed_started_faults']}; не запускалось ошибочных: {m['unrun_faulty']}.",
            f"Ложных находок: {m['false_alarms']}; запущено исправных: {m['started_fixed']} из 3.",
            f"Запросов: {len(attempted)} из 20. Токены: вход {shown_tokens['prompt_tokens']}, выход {shown_tokens['completion_tokens']}."]
    for row in record["rows"]:
        model = (row.get("model") or {}).get("result")
        base = row.get("baseline")
        model_probes = model["probe_attempts"] if model else "неизвестно" if row.get("model") else "не запускалась"
        finding = ("; расхождение доказано" if model["status"] == "REPRODUCED" else "; данных недостаточно") if model else ""
        text.append(LABELS[row["id"]] + ": " + STATES[row["state"]] + finding + "; пробы модели " + str(model_probes)
                    + ", перебора " + str(base["probe_attempts"] if base else "не запускался") + ".")
    cost = record["cost"]["estimate_usd_range"]
    text += [f"Оценка по известному usage: ${cost[0]:.8f}–${cost[1]:.8f}." if cost else "Полная стоимость неизвестна, нулевое списание не утверждается.",
             "Незапущенные случаи не считаются успехом или пропуском. Отказы и отмена сохранены. Меньшее число проб после отказа не означает преимущество.",
             "Исторический travel_time (2 пробы у модели и перебора) не включён в новые метрики. Открытый малый набор, не статистическое доказательство пользы ИИ. c02 сразу выявляет все три известных дефекта."]
    record["summary_ru"] = "\n\n".join(text)


def save_report(record, directory):
    directory = search.local_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    refresh(record)
    # Сначала журнал: даже ошибка повторения не должна уничтожить результаты запроса.
    (directory / "result.json").write_text(app.json_text(record), encoding="utf-8")
    for row in record["rows"]:
        result = (row.get("model") or {}).get("result")
        if result and result["status"] == "REPRODUCED":
            path, _ = search.save_reproducer(result, row["variant"], directory / row["id"] / "reproducers")
            row["local_replay"] = search.replay_file(path)
            row["replay_command"] = "python3 -B " + shlex.quote(str(search.ROOT / "input_search.py")) + " replay --artifact " + shlex.quote(str(path))
    (directory / "result.json").write_text(app.json_text(record), encoding="utf-8")
    body = "<!doctype html><html lang='ru'><meta charset='utf-8'><title>ReproCase — пять сравнений</title><style>body{font:18px/1.5 system-ui;max-width:1050px;margin:30px auto;padding:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Пять существующих учебных случаев</h1>"
    body += "<p>Источник: " + html.escape(record["origin"]) + ". Варианты показаны человеку, но не передавались модели.</p><pre>" + html.escape(record["summary_ru"]) + "</pre>"
    for row in record["rows"]:
        body += "<details><summary>" + html.escape(LABELS[row["id"]]) + "</summary><pre>" + html.escape(app.json_text(row)) + "</pre></details>"
    body += "<p><a href='result.json'>Полный журнал, план, история и метрики</a></p></html>"
    (directory / "report.html").write_text(loc.html_text(body), encoding="utf-8")
    return directory / "report.html"


class SeriesJob(live.LiveJob):
    directory_prefix = "comparison-five-"

    def __init__(self, output_dir=None, session_factory=SeriesSession):
        super().__init__(output_dir, session_factory)
        self.progress_text = "Подготовка пяти сравнений."

    def _validate_payload(self, plan):
        if app.json_text(plan) != app.json_text(load_plan()):
            raise client.ClientError("План изменён; нужно заново просмотреть и подтвердить пять случаев.")
        return plan

    def _work(self, plan, key, directory):
        budget = Budget()
        record = {"kind": "FIVE_CASE_COMPARISON", "model": live.MODEL, "endpoint": live.HOST,
                  "plan": plan, "history": historical_reference(), "state": "RUNNING", "cancel_requested": False,
                  "rows": [dict(row, state="NOT_RUN", baseline=None, model=None) for row in plan["cases"]]}
        save_error = None
        try:
            save_report(record, directory)  # До первого запроса проверяется возможность сохранения.
            for index, row in enumerate(record["rows"], 1):
                if self.cancelled.is_set():
                    record["state"] = "CANCELLED"
                    break
                if source_hashes() != plan["source_sha256"]:
                    record["state"] = "PLAN_CHANGED"
                    break
                self.progress_text = f"Сравнение {index} из 5: {LABELS[row['id']]}. Запросов начато: {budget.requests} из 20."
                row["baseline"] = search.run_search(row["case"], row["variant"])
                row["state"] = "RUNNING"
                save_report(record, directory)
                session = self.session_factory(self.cancelled, budget)
                try:
                    row["model"] = adapter.run_case(row["case"], row["variant"], session, key)
                except Exception:
                    row["model"] = {"result": None, "events": copy.deepcopy(session.events), "accounting": session.accounting(),
                                    "failure": "Внутренняя ошибка; повтор и следующие сеансы не выполняются."}
                row["model"] = live._redact(row["model"], key)
                for event in row["model"]["events"]:
                    event.pop("response_text", None)
                result = row["model"].get("result")
                failed = not result or result["stop_reason"] in ("REJECTED_ACTION", "SELECTOR_ERROR", "OPERATION_ERROR")
                row["state"] = "CANCELLED" if self.cancelled.is_set() else "ERROR" if failed else "COMPLETED"
                if row["state"] != "COMPLETED":
                    record["state"] = row["state"]
                save_report(record, directory)
                if record["state"] != "RUNNING":
                    break
            else:
                record["state"] = "COMPLETED"
        except Exception:
            record["state"] = "ERROR"
            for row in record["rows"]:
                if row["state"] == "RUNNING":
                    row["state"] = "ERROR"
            save_error = "Серия остановлена: ошибка подготовки или сохранения. Дальнейших запросов нет; результат доступен в памяти."
        finally:
            key = None
            record["cancel_requested"] = self.cancelled.is_set()
            record["budget_used"] = {"reserved_requests": budget.requests, "serialized_bytes": budget.bytes}
            refresh(record)
            try:
                path = save_report(record, directory)
            except Exception:
                path = None
                save_error = "Отчёт сохранён не полностью. Можно повторить только сохранение, без API."
            self.results.put({"record": record, "directory": str(directory), "report": str(path) if path else None, "save_error": save_error})
