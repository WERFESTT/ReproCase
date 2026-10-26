#!/usr/bin/env python3
"""Соединение ограниченного поиска с клиентом. CLI — только искусственные ответы."""

import argparse
import copy
import hashlib
import html
import json
import math

import benchmark_adapter as transport
import benchmark_diagnostics as diagnostic
import input_search as search
import nebius_client as client
import reprocase as app
from reference import RELATIVE_TOLERANCE


FUNCTION_NAME = "input_search_action"
MAX_MODEL_REQUESTS = search.MAX_TRIALS + 1
FILES = ("input-search-adapter-synthetic.json", "input-search-adapter-synthetic.html")


def _validate_check(check, view):
    """Только структура уже полученного сравнения; расчёт здесь не запускается."""
    if (type(check) is not dict
            or set(check) != {"actual", "expected", "passed", "absolute_error", "allowed_error"}
            or check["passed"] is not True):
        raise client.ClientError("Продолжить можно только после корректной проверки без расхождения.")
    for key in ("actual", "expected"):
        diagnostic.validate_view(dict(view, observation=check[key]))
    if check["actual"] != view["observation"]:
        raise client.ClientError("Сравнение не относится к наблюдению.")
    allowed = check["allowed_error"]
    tolerance = view["check"].get("absolute_tolerance_s", view["check"].get("absolute_tolerance", 0))
    if view["operation"] != "seeded_sequence":
        tolerance = max(tolerance, abs(check["expected"]) * RELATIVE_TOLERANCE)
    if type(allowed) not in (int, float) or not math.isfinite(allowed) or allowed != tolerance:
        raise client.ClientError("Сравнение меняет допуск.")
    error = check["absolute_error"]
    if view["operation"] == "seeded_sequence":
        valid = error is None and check["actual"] == check["expected"]
    else:
        valid = (type(error) in (int, float) and math.isfinite(error) and 0 <= error <= allowed
                 and error == abs(check["actual"] - check["expected"]))
    if not valid:
        raise client.ClientError("Некорректное отклонение в истории сравнения.")


def validate_context(context):
    """Те же поля, кандидаты и бюджет, что у обычного селектора; без оценщика."""
    try:
        if type(context) is not dict:
            raise client.ClientError("Нужен публичный контекст подбора входа.")
        initial = diagnostic.validate_view(context["initial"])
        _validate_check(context["initial_check"], initial)
        plan, plan_hash = search.load_plan()
        candidates = plan["candidates"][initial["operation"]]
        history = context["history"]
        if type(history) is not list or len(history) > search.MAX_TRIALS:
            raise client.ClientError("Некорректное число проб в истории.")
        by_id = {item["id"]: item["inputs"] for item in candidates}
        seen = set()
        for trial in history:
            if (type(trial) is not dict
                    or set(trial) != {"candidate_id", "inputs", "status", "check", "error"}
                    or trial["status"] != "INSUFFICIENT_DATA" or trial["error"] is not None):
                raise client.ClientError("Нельзя продолжать после расхождения или ошибки операции.")
            identifier = trial["candidate_id"]
            if (type(identifier) is not str or identifier not in by_id or identifier in seen
                    or trial["inputs"] != by_id[identifier] or trial["inputs"] == initial["inputs"]):
                raise client.ClientError("История содержит повторный или неизвестный вход.")
            seen.add(identifier)
            view = dict(initial, inputs=trial["inputs"], observation=trial["check"]["actual"])
            diagnostic.validate_view(view)
            _validate_check(trial["check"], view)
        result = {"initial": initial, "initial_check": context["initial_check"],
                  "attempts": history, "plan_sha256": plan_hash}
        canonical = search.model_context(result, candidates)
        # Сериализация различает true и 1, в отличие от равенства словарей Python.
        if app.json_text(context) != app.json_text(canonical):
            raise client.ClientError("Контекст меняет разрешённые поля, план, инструкцию или бюджет.")
        return canonical
    except client.ClientError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise client.ClientError("Контекст подбора не прошёл локальную проверку.") from None


def build_request(context, model=client.DEFAULT_MODEL, *, max_completion_tokens=None, max_request_bytes=None):
    context = validate_context(context)
    public = {key: value for key, value in context.items() if key not in ("instruction", "action_schema")}
    messages = [
        {"role": "system", "content": context["instruction"]},
        {"role": "user", "content": json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ]
    return client.build_function_request(messages, context["action_schema"], FUNCTION_NAME, model,
                                         max_completion_tokens=max_completion_tokens, max_request_bytes=max_request_bytes)


def artificial_response(action, model=client.DEFAULT_MODEL, usage=None):
    """Искусственный конверт известного формата, не ответ настоящей модели."""
    response = transport.artificial_response(action, model, usage)
    response["id"] = "SYNTHETIC-input-search-response"
    response["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = FUNCTION_NAME
    return response


class SyntheticTransport(transport.SyntheticTransport):
    """Имитирует выбор обычным правилом, используя только тело подготовленного запроса."""

    def __call__(self, body):
        self.calls += 1
        request = json.loads(body)
        context = json.loads(request["messages"][1]["content"])
        context["instruction"] = request["messages"][0]["content"]
        context["action_schema"] = request["tools"][0]["function"]["parameters"]
        context = validate_context(context)
        result = (artificial_response(search.fixed_order_selector(context), request["model"])
                  if self.responder is None else self.responder(copy.deepcopy(context)))
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8")
        return json.dumps(result, ensure_ascii=False).encode("utf-8")


class SearchSession(transport.DiagnosticSession):
    """Тот же ограниченный транспорт и учёт расхода; новая закрытая схема действий."""

    function_name = FUNCTION_NAME
    transport_type = SyntheticTransport

    def _validate_context(self, context):
        return validate_context(context)

    def _build_request(self, context):
        return build_request(context, self.model)

    def _validate_action(self, arguments, context):
        try:
            return search.validate_action(arguments, context)
        except search.SearchError:
            raise client.ClientError("Выбор входа не прошёл проверку; новая проба не выполнялась.") from None

    def _continuity(self, context):
        identity = search.digest({key: context[key] for key in ("initial", "initial_check", "plan_sha256", "candidates")})
        history = context["history"]
        if self._sample_digest is None:
            if history:
                raise client.ClientError("Сеанс начинается без готовой истории проб.")
            self._sample_digest = identity
            return
        previous = self._last_context["history"]
        if (identity != self._sample_digest or self._last_action["action"] != "try_input"
                or len(history) != len(previous) + 1 or history[:-1] != previous
                or history[-1]["candidate_id"] != self._last_action["candidate_id"]):
            raise client.ClientError("История не продолжает разрешённую пробу; повтор или смена задачи запрещены.")


def run_case(case, variant, session, api_key=None):
    """Локальный исполнитель получает вариант; селектор — только публичный контекст."""
    try:
        result = search.run_search(case, variant, lambda context: session.request_action(context, api_key))
        return {"origin": session.origin, "result": result, "accounting": session.accounting(),
                "events": copy.deepcopy(session.events)}
    finally:
        session.stopped = True


def run_synthetic_suite():
    """Те же 48 запусков, без новых заданий и без оценки способности настоящей модели."""
    fixtures = json.loads((search.ROOT / "benchmark_fixtures.json").read_text())["variations"]
    runs, matches, replayed = [], 0, 0
    for item in fixtures:
        case = search.make_case(item["operation"], item["inputs"], item["check"])
        for variant in ("buggy", "fixed"):
            record = run_case(case, variant, SearchSession(synthetic_transport=SyntheticTransport()))
            record["matches_baseline"] = record["result"] == search.run_search(case, variant)
            matches += record["matches_baseline"]
            record["replay_verified"] = False
            if record["result"]["status"] == "REPRODUCED":
                artifact = search.make_artifact(record["result"], variant)
                record["replay_verified"] = search.replay_data(artifact)["status"] == "REPRODUCED"
                replayed += record["replay_verified"]
            runs.append(record)
    return {
        "schema_version": 1, "origin": "SYNTHETIC", "model_evaluation": "NOT_EVALUATED",
        "warning": "Ответы искусственные и повторяют обычное правило. Проверяется соединение клиента с поиском, не качество NVIDIA.",
        "model_metrics": None, "real_api_calls": 0, "samples": len(runs), "matching_baseline_routes": matches,
        "synthetic_responses": sum(run["accounting"]["attempted_requests"] for run in runs),
        "probe_attempts": sum(run["result"]["probe_attempts"] for run in runs),
        "initial_mismatches": sum(run["result"]["initial_status"] == "REPRODUCED" for run in runs),
        "newly_reproduced": sum(run["result"]["initial_status"] != "REPRODUCED" and run["result"]["status"] == "REPRODUCED" for run in runs),
        "unresolved_results": sum(run["result"]["status"] == "INSUFFICIENT_DATA" for run in runs),
        "replayed_proofs": replayed, "max_trials": search.MAX_TRIALS, "max_requests": MAX_MODEL_REQUESTS,
        "runs": runs,
        "source_sha256": {name: hashlib.sha256((search.ROOT / name).read_bytes()).hexdigest() for name in
                          (*search.SOURCE_FILES, "input_search_adapter.py", "benchmark_adapter.py", "nebius_client.py", "benchmark_fixtures.json")},
    }


def write_synthetic_evidence(output_dir=search.ROOT / "outputs"):
    directory = search.local_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report = run_synthetic_suite()
    (directory / FILES[0]).write_text(app.json_text(report), encoding="utf-8")
    body = ("<h2>СИНТЕТИЧЕСКАЯ проверка соединения</h2>"
            "<p>Ответы создаёт локальное фиксированное правило. Это не результат настоящей NVIDIA и не сравнение качества моделей.</p>"
            f"<p>Совпало с обычным маршрутом: {report['matching_baseline_routes']} из {report['samples']}. "
            f"Искусственных ответов: {report['synthetic_responses']}; новых проб: {report['probe_attempts']}. "
            f"Исходных расхождений: {report['initial_mismatches']}; новым вводом воспроизведено: {report['newly_reproduced']}; "
            f"осталось недостаточно данных: {report['unresolved_results']}. Повторены {report['replayed_proofs']} доказательства.</p>"
            "<p>Максимум три новые пробы и четыре последовательных запроса на сеанс. Ошибочный ответ или отказ транспорта останавливает сеанс без повторов. "
            "Неизвестное потребление остаётся null. Реальных API-вызовов — 0; метрики модели — null / NOT_EVALUATED.</p>"
            "<p>В запросах только тот же контекст, что у обычного правила; результаты независимого сравнения доступны после проверки. "
            "Эталон оценщика, вариант реализации и тип дефекта не передаются.</p>"
            f"<p><a href='{FILES[0]}'>Все запросы, искусственные ответы и исходы</a> · <a href='../INPUT_SEARCH.md'>Методика и ограничения</a></p>"
            "<table><tr><th>Запуск</th><th>Исходно</th><th>Итог</th><th>Новых проб</th><th>Искусственных ответов</th><th>Совпадение маршрута</th></tr>")
    for index, run in enumerate(report["runs"], 1):
        result = run["result"]
        body += (f"<tr><td>{index}: {html.escape(result['initial']['operation'])}</td><td>{result['initial_status']}</td>"
                 f"<td>{result['status']}</td><td>{result['probe_attempts']}</td><td>{run['accounting']['attempted_requests']}</td>"
                 f"<td>{'да' if run['matches_baseline'] else 'нет'}</td></tr>")
    (directory / FILES[1]).write_text(search.html_page("ReproCase — искусственные ответы для подбора входа", body + "</table>"), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Соединение клиента и поиска: только искусственные ответы, без сети и ключа.")
    parser.add_argument("--synthetic", action="store_true", required=True, help="явно выбрать искусственные ответы")
    parser.parse_args(argv)
    report = write_synthetic_evidence()
    print(f"СИНТЕТИЧЕСКИЙ прогон: {report['samples']} запусков; совпало {report['matching_baseline_routes']}; "
          f"ответов {report['synthetic_responses']}; новых проб {report['probe_attempts']}.")
    print("Реальных API-вызовов — 0. Настоящая модель НЕ ПРОВЕРЕНА.")
    print(search.ROOT / "outputs" / FILES[1])
    return 0 if report["matching_baseline_routes"] == report["samples"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
