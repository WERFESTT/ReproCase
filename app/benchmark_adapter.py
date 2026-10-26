#!/usr/bin/env python3
"""Адаптер Nebius к benchmark. Командный запуск допускает только синтетический прогон."""

from localization import print
import localization as loc

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import threading

import benchmark_diagnostics as diag
import nebius_client as client


MAX_MODEL_REQUESTS = diag.MAX_CHECKS + 1
ROOT = Path(__file__).resolve().parent


def _valid_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_context(context):
    """Публичные поля и только структурированные результаты уже выполненных проверок."""
    if type(context) is not dict:
        raise client.ClientError("Нужен диагностический контекст.")
    try:
        view = diag.validate_view(context.get("sample"))
        history = context.get("history")
        if type(history) is not list or len(history) > diag.MAX_CHECKS:
            raise client.ClientError("Некорректная история диагностических проверок.")
        seen = set()
        for entry in history:
            if type(entry) is not dict or set(entry) != {"check_id", "result"}:
                raise client.ClientError("Лишние поля в истории проверок.")
            identifier = entry["check_id"]
            if type(identifier) is not str or identifier not in diag.CHECK_DESCRIPTIONS or identifier in seen:
                raise client.ClientError("Неизвестная или повторная проверка в истории.")
            seen.add(identifier)
            result = entry["result"]
            fields = {"input_separates_fault": {"separates"},
                      "matches_contract": {"matches", "calculated_reference", "absolute_error", "allowed_error"},
                      "matches_known_fault": {"matches", "calculated_fault_signature"}}[identifier]
            if type(result) is not dict or set(result) != fields:
                raise client.ClientError("Неизвестные поля результата проверки.")
            flag = "separates" if identifier == "input_separates_fault" else "matches"
            if type(result[flag]) is not bool:
                raise client.ClientError("Неверный тип результата проверки.")
            for key in ("calculated_reference", "calculated_fault_signature"):
                if key in result:
                    diag.validate_view(dict(view, observation=result[key]))
            if identifier == "matches_contract":
                if not _valid_number(result["allowed_error"]) or result["allowed_error"] < 0:
                    raise client.ClientError("Неверный допуск в истории.")
                error = result["absolute_error"]
                if view["operation"] == "seeded_sequence":
                    if error is not None:
                        raise client.ClientError("Числовая ошибка не определена для последовательности.")
                elif not _valid_number(error) or error < 0:
                    raise client.ClientError("Неверная числовая ошибка в истории.")
        canonical = diag.model_context(view, history)
        if context != canonical or set(context) != set(canonical):
            raise client.ClientError("Контекст изменяет разрешённые поля, правила или бюджет.")
        return canonical
    except client.ClientError:
        raise
    except (ValueError, TypeError, OverflowError, KeyError):
        raise client.ClientError("Диагностический контекст не прошёл локальную проверку.") from None


def build_request(context, model=client.DEFAULT_MODEL):
    """Перенести тот же контекст в messages/tools без подсказанного ответа."""
    context = validate_context(context)
    public = {key: value for key, value in context.items() if key not in ("instruction", "action_schema")}
    messages = [
        {"role": "system", "content": context["instruction"]},
        {"role": "user", "content": json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ]
    return client.build_function_request(messages, context["action_schema"], "diagnostic_action", model)


def decode_response(raw, context, model=client.DEFAULT_MODEL):
    context = validate_context(context)
    reply = client.decode_function_response(raw, "diagnostic_action", model)
    try:
        action = diag.validate_action(reply.arguments, context["sample"])
    except diag.DiagnosticError:
        raise client.ClientError("Действие модели не прошло проверку; операция не выполнялась.") from None
    return action, reply.usage


def artificial_response(action, model=client.DEFAULT_MODEL, usage=None):
    """Полный искусственный конверт. Это не ответ настоящей NVIDIA."""
    return {
        "id": "SYNTHETIC-diagnostic-response", "model": model, "usage": usage,
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "SYNTHETIC-call", "type": "function", "function": {
                "name": "diagnostic_action", "arguments": json.dumps(action),
            }}],
        }}],
    }


class SyntheticTransport:
    """Локальная имитация по обычному правилу, проверяет обвязку, не качество модели."""

    def __init__(self, responder=None):
        self.responder = responder
        self.calls = 0

    def __call__(self, body):
        self.calls += 1
        request = json.loads(body)
        context = json.loads(request["messages"][1]["content"])
        context["instruction"] = request["messages"][0]["content"]
        context["action_schema"] = request["tools"][0]["function"]["parameters"]
        context = validate_context(context)
        if self.responder is None:
            result = artificial_response(diag.fixed_order_selector(context), request["model"])
        else:
            result = self.responder(copy.deepcopy(context))
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8")
        return json.dumps(result, ensure_ascii=False).encode("utf-8")


class DiagnosticSession:
    """Один сеанс на наблюдение: до четырёх запросов, без повторов после ошибки."""

    function_name = "diagnostic_action"
    transport_type = SyntheticTransport

    def __init__(self, *, synthetic_transport=None, allow_network=False,
                 max_requests=MAX_MODEL_REQUESTS, model=client.DEFAULT_MODEL):
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_MODEL_REQUESTS:
            raise client.ClientError("Допустим бюджет от одного до четырёх запросов.")
        if type(allow_network) is not bool or model not in client.MODELS:
            raise client.ClientError("Неизвестный режим или модель.")
        if synthetic_transport is not None and not isinstance(synthetic_transport, self.transport_type):
            raise client.ClientError("Разрешён только явно синтетический транспорт.")
        if synthetic_transport is not None and allow_network:
            raise client.ClientError("Синтетический режим нельзя совмещать с включённой сетью.")
        self.transport, self.allow_network, self.model = synthetic_transport, allow_network, model
        self.max_requests, self.attempts, self.events = max_requests, 0, []
        self.stopped = False
        self._sample_digest = None
        self._last_context = None
        self._last_action = None
        self._lock = threading.Lock()

    @property
    def origin(self):
        return "SYNTHETIC" if self.transport is not None else "NEBIUS_NOT_AUTOMATICALLY_AUTHORIZED"

    def _validate_context(self, context):
        return validate_context(context)

    def _build_request(self, context):
        return build_request(context, self.model)

    def _before_send(self):
        pass

    def _send(self, body, api_key):
        return client._https_once(body, api_key)

    def _validate_action(self, arguments, context):
        try:
            return diag.validate_action(arguments, context["sample"])
        except diag.DiagnosticError:
            raise client.ClientError("Действие не прошло локальную проверку; операция не выполнялась.") from None

    def _continuity(self, context):
        digest = context["sample_sha256"]
        history = context["history"]
        if self._sample_digest is None:
            if history:
                raise client.ClientError("Сеанс должен начинаться без готовой истории проверок.")
            self._sample_digest = digest
            return
        if digest != self._sample_digest:
            raise client.ClientError("Нельзя сменить наблюдение внутри сеанса.")
        previous = self._last_context["history"]
        if (self._last_action["action"] != "run_check" or len(history) != len(previous) + 1
                or history[:-1] != previous or history[-1]["check_id"] != self._last_action["check_id"]):
            raise client.ClientError("История не продолжает последнее разрешённое действие; повтор запрещён.")

    def request_action(self, context, api_key=None):
        with self._lock:
            if self.stopped:
                raise client.ClientError("Сеанс остановлен; повтор запрещён.")
            event = {"origin": self.origin, "attempted": False, "network_attempted": False,
                     "usage": None, "status": "rejected", "action": None}
            self.events.append(event)
            try:
                context = self._validate_context(context)
                self._continuity(context)
                if self.attempts >= self.max_requests:
                    raise client.ClientError("Лимит запросов исчерпан; повторов нет.")
                request = self._build_request(context)
                event["request"] = request  # Без ключа и заголовков авторизации.
                if self.transport is None:
                    if not self.allow_network:
                        raise client.NetworkDisabled("Сеть отключена; запрос не отправлен.")
                    if (type(api_key) is not str or not 1 <= len(api_key) <= 512
                            or any(not 33 <= ord(ch) <= 126 for ch in api_key)):
                        raise client.ClientError("Для разрешённого запроса нужен отдельно переданный корректный ключ.")
                elif api_key is not None:
                    raise client.ClientError("В синтетическом режиме ключ не принимается.")
                self._before_send()
                self.attempts += 1
                event["attempted"] = True
                body = json.dumps(request, ensure_ascii=False).encode("utf-8")
                if self.transport is None:
                    event["network_attempted"] = True
                    raw = self._send(body, api_key)
                else:
                    raw = self.transport(body)
                if type(raw) not in (str, bytes) or len(raw) > client.MAX_RESPONSE_BYTES:
                    raise client.ClientError("Ответ превысил допустимый размер или имеет неверный тип.")
                response_text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                event["response_text"] = response_text.replace(api_key, "[REDACTED]") if api_key else response_text
                reply = client.decode_function_response(raw, self.function_name, self.model)
                event["usage"] = reply.usage
                action = self._validate_action(reply.arguments, context)
                event.update(status="validated", action=action)
                self._last_context, self._last_action = copy.deepcopy(context), action
                if action["action"] == "finish":
                    self.stopped = True
                return action
            except client.ClientError as error:
                self.stopped = True
                event["error"] = str(error)
                raise
            except Exception:
                self.stopped = True
                event["error"] = "Транспорт или разбор ответа завершился ошибкой. Расход неизвестен; повтора нет."
                raise client.ClientError(event["error"]) from None

    def accounting(self):
        attempted = [event for event in self.events if event["attempted"]]
        totals, complete = {}, bool(attempted)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            values = [event["usage"].get(key) if event["usage"] is not None else None for event in attempted]
            known = bool(values) and all(value is not None for value in values)
            totals[key] = sum(values) if known else None
            complete = complete and known
        return {"origin": self.origin, "attempted_requests": len(attempted),
                "network_attempts": sum(event["network_attempted"] for event in self.events),
                "usage_complete": complete, "tokens": totals}


def run_sample(view, session, api_key=None):
    """Тот же исполнитель и тот же бюджет проверок, что у обычного метода."""
    def select(context):
        try:
            return session.request_action(context, api_key)
        except client.ClientError as error:
            raise diag.DiagnosticError(str(error)) from None
    try:
        result = diag.diagnose(view, select)
        return {"origin": session.origin, "diagnosis": result, "accounting": session.accounting(),
                "events": copy.deepcopy(session.events)}
    finally:
        session.stopped = True


def run_synthetic_suite():
    """Механическая проверка полного маршрута на тех же 48 публичных наблюдениях."""
    import benchmark
    public, _ = benchmark.build_dataset()
    runs, matches = [], 0
    for view in public:
        record = run_sample(view, DiagnosticSession(synthetic_transport=SyntheticTransport()))
        baseline = diag.diagnose(view)
        matches += record["diagnosis"] == baseline
        runs.append(record)
    return {
        "schema_version": 1, "origin": "SYNTHETIC", "model_evaluation": "NOT_EVALUATED",
        "warning": "Ответы созданы локально обычным правилом. Это тест клиента и маршрута, не качество NVIDIA.",
        "real_api_calls": 0, "model_metrics": None, "samples": len(runs),
        "matching_baseline_routes": matches, "max_checks_per_sample": diag.MAX_CHECKS,
        "max_requests_per_sample": MAX_MODEL_REQUESTS,
        "synthetic_responses": sum(run["accounting"]["attempted_requests"] for run in runs),
        "checks_executed": sum(run["diagnosis"]["checks_run"] for run in runs),
        "runs": runs,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                          ("benchmark_adapter.py", "nebius_client.py", "benchmark_diagnostics.py", "benchmark_fixtures.json")},
    }


def write_synthetic_evidence(output_dir=ROOT / "outputs"):
    result = run_synthetic_suite()
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "benchmark-adapter-synthetic.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    argv = loc.cli_args(argv)
    parser = loc.ArgumentParser(description="Проверить адаптер только на синтетических ответах, без сети и ключа.")
    parser.add_argument("--synthetic", action="store_true", required=True, help="явно выбрать искусственные ответы")
    args = parser.parse_args(argv)
    result = write_synthetic_evidence()
    print(f"СИНТЕТИЧЕСКИЙ прогон: {result['samples']} наблюдений, {result['synthetic_responses']} искусственных ответов, {result['checks_executed']} проверок.")
    print(f"Совпали с обычным маршрутом: {result['matching_baseline_routes']}/{result['samples']}.")
    print("Реальных API-вызовов: 0. Настоящая модель НЕ ПРОВЕРЕНА.")
    print(ROOT / "outputs/benchmark-adapter-synthetic.json")
    return 0 if result["matching_baseline_routes"] == result["samples"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
