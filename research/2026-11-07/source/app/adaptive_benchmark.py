#!/usr/bin/env python3
"""Офлайн-сравнение обычных стратегий на конечном учебном наборе."""

from localization import print
import localization as loc

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import shlex

import adaptive_contract as public
import adaptive_adapter as adapter
import input_search as shared
import reprocase as app

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "adaptive_cases.json"
FIXTURE_SHA256 = "310dd00fb8ae0dfb225a3e12ee4b1a0e169025c14245941f77cbfed3b117d0c0"
SOURCE_FILES = ("adaptive_benchmark.py", "adaptive_contract.py", "adaptive_cases.json", "input_search.py", "reprocase.py")
FILES = ("adaptive-results.json", "adaptive-report.html", "adaptive-public.json", "adaptive-truth.json")
LABELS = {"fixed_order": "Фиксированный порядок", "c02_first": "Сначала c02, затем порядок",
          "minimax_boundary": "Деление возможных порогов и границы"}


def hashes():
    return {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in SOURCE_FILES}


def load_cases():
    raw = FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise public.Error("Изменился заранее зафиксированный набор; требуется новая версия и методика.")
    data = json.loads(raw, object_pairs_hook=app.unique_object, parse_constant=app.reject_constant)
    cases = {}
    for row in data["rows"]:
        for variant in data["variants"]:
            cases[f"sample_{len(cases)+1:02}"] = dict(row, variant=variant)
    return cases


def calculate(x, threshold, variant):
    """Три написанные вручную реализации. Дефекты намеренные, не реальные находки."""
    if variant == "fixed":
        return int(x >= threshold)
    if variant == "late":
        return int(x > threshold)  # Неверное исключение точки границы.
    if variant == "early":
        return int(x >= threshold - 1)  # Неверный сдвиг границы на единицу.
    raise public.Error("Неизвестная реализация.")


def observe(case_id, x):
    cases = load_cases()
    if type(case_id) is not str or case_id not in cases or type(x) is not int or x not in public.VALUES:
        raise public.Error("Неизвестный случай или вход вне плана.")
    case = cases[case_id]
    actual = calculate(x, case["threshold"], case["variant"])
    # Эталон — вручную записанная таблица, не вызов исправной функции или того же сравнения.
    expected = case["expected"][x - 1]
    return {"inputs": {"x": x}, "actual": actual, "expected": expected, "passed": actual == expected}


def run_case(case_id, selector=public.minimax_boundary, items=None):
    items = public.validate_candidates(public.candidates() if items is None else items)
    initial = observe(case_id, public.INITIAL_X)
    result = {"initial": initial, "initial_runs": 1, "probe_attempts": 0, "contract_checks": 1,
              "history": [], "decisions": [], "status": "INSUFFICIENT_DATA", "proof": None, "stop_reason": None}
    if not initial["passed"]:
        result.update(status="REPRODUCED", proof=initial, stop_reason="INITIAL_MISMATCH")
        return result
    for _ in range(public.MAX_TRIALS + 1):
        context = public.make_context(initial, result["history"], items)
        try:
            action = public.validate_action(selector(copy.deepcopy(context)), context)
        except (TypeError, ValueError):
            result["decisions"].append({"accepted": False})
            result["stop_reason"] = "REJECTED_ACTION"
            break
        except Exception:
            result["decisions"].append({"accepted": False})
            result["stop_reason"] = "SELECTOR_ERROR"
            break
        result["decisions"].append({"accepted": True, "action": action})
        if action["action"] == "finish":
            result["stop_reason"] = "BUDGET_EXHAUSTED" if result["probe_attempts"] == public.MAX_TRIALS else "SELECTOR_STOPPED"
            break
        candidate = next(c for c in items if c["id"] == action["candidate_id"])
        observation = observe(case_id, candidate["inputs"]["x"])
        result["probe_attempts"] += 1
        result["contract_checks"] += 1
        result["history"].append(dict(observation, candidate_id=candidate["id"]))
        if not observation["passed"]:
            result.update(status="REPRODUCED", proof=observation, stop_reason="NEW_MISMATCH")
            break
    return result


def measure(runs, cases):
    m = {"samples": len(runs), "faulty": 0, "fixed": 0, "found_faults": 0, "missed_faults": 0,
         "false_alarms": 0, "fixed_insufficient": 0, "initial_faults": 0, "new_faults": 0,
         "initial_runs": 0, "probe_attempts": 0, "contract_checks": 0, "protocol_errors": 0}
    for identifier, result in runs.items():
        faulty = cases[identifier]["variant"] != "fixed"
        found = result["status"] == "REPRODUCED"
        m["faulty" if faulty else "fixed"] += 1
        m["found_faults"] += faulty and found
        m["missed_faults"] += faulty and not found
        m["false_alarms"] += not faulty and found
        m["fixed_insufficient"] += not faulty and not found
        m["initial_faults"] += faulty and found and result["stop_reason"] == "INITIAL_MISMATCH"
        m["new_faults"] += faulty and found and result["stop_reason"] == "NEW_MISMATCH"
        for k in ("initial_runs", "probe_attempts", "contract_checks"):
            m[k] += result[k]
        m["protocol_errors"] += result["stop_reason"] in ("REJECTED_ACTION", "SELECTOR_ERROR")
    m["found_fraction_all_faults"] = m["found_faults"] / m["faulty"]
    return m


def make_artifact(case_id, proof):
    public.validate_observation(proof)
    if proof["passed"] or observe(case_id, proof["inputs"]["x"]) != proof:
        raise public.Error("Расхождение не доказано.")
    return {"version": public.VERSION, "case_id": case_id, "source_sha256": hashes(), "proof": copy.deepcopy(proof)}


def replay_data(data):
    try:
        if type(data) is not dict or set(data) != {"version", "case_id", "source_sha256", "proof"}:
            raise public.Error("Лишние или отсутствующие поля свидетельства.")
        if data["version"] != public.VERSION or data["source_sha256"] != hashes():
            raise public.Error("Версия или исходники изменились.")
        canonical = make_artifact(data["case_id"], data["proof"])
        if app.json_text(data) != app.json_text(canonical):
            raise public.Error("Свидетельство не совпадает с фактическим результатом.")
        return {"status": "REPRODUCED", "proof": canonical["proof"]}
    except (ValueError, TypeError, KeyError, RecursionError):
        raise public.Error("Свидетельство не прошло строгую локальную проверку.") from None


def replay_file(path):
    with shared.local_path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise public.Error("Свидетельство превышает 65536 байт.")
    return replay_data(json.loads(raw, object_pairs_hook=app.unique_object, parse_constant=app.reject_constant))


def synthetic_run(case_id, responder=None):
    session = adapter.OfflineSession(adapter.SyntheticTransport(responder))
    try:
        result = run_case(case_id, session.request_action)
        return {"origin": "SYNTHETIC", "result": result, "accounting": session.accounting(), "events": session.events}
    finally:
        session.stopped = True


def evaluate():
    cases = load_cases()
    methods = {}
    for name, selector in public.STRATEGIES.items():
        runs = {identifier: run_case(identifier, selector) for identifier in cases}
        methods[name] = {"metrics": measure(runs, cases), "runs": runs}
    synthetic = {identifier: synthetic_run(identifier) for identifier in cases}
    return {"version": public.VERSION, "mode": "OFFLINE", "real_api_calls": 0,
            "model": {"status": "NOT_EVALUATED", "metrics": None, "tokens": None, "cost": None},
            "methods": methods, "source_sha256": hashes(), "max_trials": public.MAX_TRIALS,
            "synthetic": {"origin": "SYNTHETIC", "is_model_measurement": False,
                          "matching_routes": sum(r["result"] == methods["minimax_boundary"]["runs"][k] for k,r in synthetic.items()),
                          "responses": sum(r["accounting"]["attempted_requests"] for r in synthetic.values()), "runs": synthetic}}


@loc.localized_html
def render_report(report):
    body = ("<h1>Выбор следующего входа по наблюдениям</h1><aside>Учебные задачи с намеренными ошибками. "
            "Этот новый набор выполнен офлайн. Настоящая модель на нём НЕ ПРОВЕРЕНА.</aside>"
            "<p>Гипотеза: сужение возможного положения границы по наблюдениям позволяет выбирать пробы "
            "полезнее фиксированного порядка. Проверяется обычный алгоритм, а не преимущество ИИ.</p>"
            "<p>Одна операция, семь возможных настроек порога, три реализации: 14 ошибочных и 7 исправных. "
            "Исходный x=4; затем не более трёх проб из восьми входов. Порог и вариант не передаются диагностике. "
            "Эталон берётся из отдельной вручную записанной таблицы после выполнения операции.</p>"
            "<table><tr><th>Метод</th><th>Найдено / все дефекты</th><th>Видно исходно</th><th>Новые находки</th>"
            "<th>Пропуски</th><th>Ложные тревоги</th><th>Новые пробы</th><th>Все вызовы / сравнения</th></tr>")
    for name, method in report["methods"].items():
        m = method["metrics"]
        body += f"<tr><td>{LABELS[name]}</td><td>{m['found_faults']} / {m['faulty']}</td><td>{m['initial_faults']}</td><td>{m['new_faults']}</td><td>{m['missed_faults']}</td><td>{m['false_alarms']}</td><td>{m['probe_attempts']}</td><td>{m['contract_checks']}</td></tr>"
    body += ("</table><p>Все исправные исходы без расхождения означают «недостаточно данных». Ложные тревоги здесь "
             "относятся к проверяющему контуру: заявление селектора не считается доказательством.</p>"
             "<p>Сильный метод использует монотонность: выбирает пробу с наименьшей худшей оставшейся группой порогов; "
             "при равенстве проверяет соседей границы. Ему доступны только те же наблюдения, кандидаты и бюджет. "
             "Названия и порядок кандидатов не используются для ранжирования.</p>"
             "<p>Пороги и варианты исчерпывают небольшой заранее закреплённый набор, а не отбираются по успеху метода. "
             "Это открытый учебный набор, не независимый закрытый тест. Результат не заменяет проверку реальных регрессий "
             "и не доказывает превосходство над Hypothesis или fuzzing.</p>"
             f"<p>Искусственный транспорт: {report['synthetic']['matching_routes']} / 21 совпавших маршрутов, "
             f"{report['synthetic']['responses']} искусственных ответов; реальных API-вызовов 0. "
             "Токены, стоимость и качество настоящей модели не измерены.</p>"
             "<p><a href='adaptive-results.json'>Все исходы и решения</a> · <a href='adaptive-public.json'>Публичные наблюдения</a> · "
             "<a href='adaptive-truth.json'>Только для оценщика, не отправлять модели</a></p>")
    for name, method in report["methods"].items():
        body += "<h2>" + LABELS[name] + "</h2>"
        for identifier, result in method["runs"].items():
            body += "<details><summary>" + identifier + " — " + ("расхождение доказано" if result["proof"] else "недостаточно данных") + "</summary><pre>" + html.escape(app.json_text(result)) + "</pre></details>"
    body += "<p>Контрольная сумма не подтверждает авторство и не защищает от согласованной переписи содержимого и хеша.</p>"
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>ReproCase — офлайн-сравнение стратегий</title><style>body{font:17px/1.5 system-ui;max-width:1150px;margin:30px auto;padding:15px}"
            "table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
            "aside{background:#fff0cf;padding:16px}</style>" + body + "</html>\n")


def write_evaluation(directory=ROOT / "outputs"):
    directory = shared.local_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = evaluate()
    proofs = set()
    for method in report["methods"].values():
        for identifier, result in method["runs"].items():
            if result["proof"]:
                artifact = make_artifact(identifier, result["proof"])
                folder = directory / "adaptive-reproducers"
                folder.mkdir(exist_ok=True)
                path = folder / (shared.digest(artifact)[:16] + ".json")
                path.write_text(app.json_text(artifact), encoding="utf-8")
                replay_file(path)
                proofs.add(path.name)
                result["replay_command"] = "python3 -B " + shlex.quote(str(ROOT / "adaptive_benchmark.py")) + " replay --artifact " + shlex.quote(str(path))
    report["distinct_replayed_proofs"] = len(proofs)
    # Контекст формируется до пробы, не из приватной метки или таблицы целиком.
    public_rows = {}
    for identifier in load_cases():
        initial = observe(identifier, public.INITIAL_X)
        view = {"initial": initial, "context": public.make_context(initial, []) if initial["passed"] else None}
        # Даже служебные ID и порядок внутренних случаев не входят в публичный экспорт.
        public_rows[shared.digest(view)] = view
    data = {"adaptive-results.json": report, "adaptive-public.json": public_rows,
            "adaptive-truth.json": {"warning": "Приватные параметры и метки оценщика. Не передавать диагностике.", "cases": load_cases()}}
    for name, value in data.items():
        (directory / name).write_text(app.json_text(value), encoding="utf-8")
    (directory / "adaptive-report.html").write_text(render_report(report), encoding="utf-8")
    return report


def main(argv=None):
    argv = loc.cli_args(argv)
    parser = loc.ArgumentParser(description="Только офлайн-сравнение обычных стратегий; сеть и ключи не используются.")
    sub = parser.add_subparsers(dest="command")
    replay = sub.add_parser("replay")
    replay.add_argument("--artifact", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            print(app.json_text(replay_file(args.artifact)))
            return 0
        report = write_evaluation()
        for name, method in report["methods"].items():
            m = method["metrics"]
            print(f"{LABELS[name]}: найдено {m['found_faults']}/{m['faulty']}; ложных {m['false_alarms']}; новых проб {m['probe_attempts']}.")
        print("Реальных API-вызовов 0. Новая серия с настоящей моделью НЕ ПРОВЕРЕНА.")
        print(ROOT / "outputs/adaptive-report.html")
        return 0
    except (ValueError, OSError) as error:
        print("Проверка остановлена: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
