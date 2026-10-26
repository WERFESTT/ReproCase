"""Подтверждаемый пользователем поиск: один рабочий поток, ключ только в памяти."""

import copy
import html
import json
from pathlib import Path
import queue
import shlex
import tempfile
import threading

import input_search as search
import input_search_adapter as adapter
import nebius_client as client
import reprocase as app

HOST = "api.tokenfactory.us-central1.nebius.com"
MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_REQUEST_BYTES = 16000
MAX_COMPLETION_TOKENS = 1024
MAX_REQUESTS = 4
RATES = {"input": 0.30, "cached_input": 0.06, "output": 0.90}


def consent_text(case):
    case = app.validate_case(case)
    return (f"Реальный поиск через Nebius\nМодель: {MODEL}\nАдрес: https://{HOST}{client.PATH}\n\n"
            "Будут отправлены правило учебной задачи, эти входные числа, наблюдение, разрешённые кандидаты и результаты проверок:\n"
            + app.json_text(case["inputs"]) + "\nВариант реализации и метки оценщика не отправляются. Проверяется намеренно ошибочный учебный вариант.\n\n"
            "Максимум 4 последовательных запроса, 3 новые пробы, 1024 токена ответа на запрос (включая reasoning), "
            "16000 байт полного запроса. Автоматических повторов нет.\n"
            "$0.30 / $0.06 / $0.90 за миллион входных / кешированных входных / выходных токенов — ставки с карточки пользователя. "
            "Это оценка, не жёсткий денежный лимит. Адрес поставщика может измениться: произвольный новый адрес здесь ввести нельзя.\n\n"
            "Ключ вводится скрыто, используется только в памяти и не сохраняется. Отмена прекращает следующие запросы; "
            "она не подтверждает отмену или отсутствие списания за уже отправленный запрос. Отчёт сохранится отдельно.\n\n"
            "Разрешить отправку этих данных в пределах указанного запуска?")


class LiveSession(adapter.SearchSession):
    def __init__(self, cancelled):
        super().__init__(allow_network=True, max_requests=MAX_REQUESTS, model=MODEL)
        self.cancelled = cancelled

    @property
    def origin(self):
        return "REAL_API"

    def _build_request(self, context):
        return adapter.build_request(context, self.model, max_completion_tokens=MAX_COMPLETION_TOKENS,
                                     max_request_bytes=MAX_REQUEST_BYTES)

    def _before_send(self):
        if self.cancelled.is_set():
            raise client.ClientError("Запуск отменён; следующий запрос не отправлен.")

    def _send(self, body, api_key):
        if len(body) > MAX_REQUEST_BYTES:
            raise client.ClientError("Превышен предел полного запроса.")
        return client._https_once(body, api_key, host=HOST)

    def _validate_action(self, arguments, context):
        if self.cancelled.is_set():
            raise client.ClientError("Ответ получен после отмены; новая проба не выполнялась.")
        return super()._validate_action(arguments, context)

    def request_action(self, context, api_key=None):
        try:
            return super().request_action(context, api_key)
        finally:
            # Не сохраняем непроверенный текст поставщика, даже если он повторил секрет.
            for event in self.events:
                if event.get("usage") is None and event.get("response_text"):
                    try:
                        payload = json.loads(event["response_text"], object_pairs_hook=client._json_object, parse_constant=client._reject_constant)
                        if type(payload) is dict and payload.get("model") == MODEL:
                            event["usage"] = client._usage(payload.get("usage"))
                    except (ValueError, TypeError, RecursionError):
                        pass  # Неполный JSON или неверный usage остаётся неизвестным.
                event.pop("response_text", None)


def estimate_cost(events):
    """Диапазон по известному usage; при неизвестном кеше не выдаём точную цену."""
    low = high = 0.0
    attempted = [e for e in events if e["network_attempted"]]
    unknown = 0
    for event in attempted:
        usage = event.get("usage") or {}
        incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if incoming is None or outgoing is None:
            unknown += 1
            continue
        cached = usage.get("cached_prompt_tokens")
        if cached is None:
            low += (incoming * RATES["cached_input"] + outgoing * RATES["output"]) / 1e6
            high += (incoming * RATES["input"] + outgoing * RATES["output"]) / 1e6
        else:
            amount = ((incoming - cached) * RATES["input"] + cached * RATES["cached_input"] + outgoing * RATES["output"]) / 1e6
            low += amount
            high += amount
    bounds = [round(low, 10), round(high, 10)]
    return {"rates_usd_per_million": dict(RATES), "estimate_usd_range": bounds if attempted and not unknown else None,
            "known_usage_subtotal_usd_range": bounds if len(attempted) > unknown else None,
            "requests_with_unknown_usage": unknown,
            "note": "Оценка по указанным ставкам, не счёт поставщика и не денежный лимит. Неизвестное потребление не считается нулевым."}


def summary(record):
    result = record.get("result")
    if result and result["status"] == "REPRODUCED":
        text = "Расхождение воспроизведено локальной проверкой."
    elif record["cancel_requested"]:
        text = "Запуск остановлен по запросу пользователя."
    elif record.get("failure") or (result and result["stop_reason"] in ("REJECTED_ACTION", "SELECTOR_ERROR", "OPERATION_ERROR")):
        text = "Запуск остановлен после отказа или ошибки. Автоматического повтора нет."
    else:
        text = "В пределах выполненных проб расхождение не найдено. Исправность не доказана."
    count = record["accounting"]["network_attempts"]
    text += f"\nЗапросов к API: {count} из {MAX_REQUESTS}; новых проб: {result['probe_attempts'] if result else 0} из 3."
    if not count:
        text += "\nРеального запроса не было."
    tokens = record["accounting"]["tokens"]
    text += "\nТокены: вход " + str(tokens["prompt_tokens"]) + ", выход " + str(tokens["completion_tokens"]) + ". null означает неизвестно."
    cost = record["cost"]["estimate_usd_range"]
    text += (f"\nОценка по известному usage: ${cost[0]:.8f}–${cost[1]:.8f}." if cost is not None
             else "\nПолная стоимость неизвестна; нулевое списание не утверждается.")
    for event in record["events"]:
        if event.get("error"):
            text += "\n" + event["error"]
    text += "\nОценка не является денежным лимитом. Отмена и тайм-аут не доказывают отмену у поставщика."
    if record.get("replay_command"):
        text += "\nЛокальное повторение без API:\n" + record["replay_command"]
    return text


def save_report(record, directory):
    directory = search.local_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = record.get("result")
    if result and result["status"] == "REPRODUCED":
        path, command = search.save_reproducer(result, "buggy", directory / "reproducers")
        record["local_replay"] = search.replay_file(path)
        record["replay_command"] = "python3 -B " + shlex.quote(str(search.ROOT / "input_search.py")) + " replay --artifact " + shlex.quote(str(path))
    record["summary_ru"] = summary(record)
    (directory / "result.json").write_text(app.json_text(record), encoding="utf-8")
    body = ("<!doctype html><html lang='ru'><meta charset='utf-8'><title>ReproCase — реальный запуск</title>"
            "<style>body{font:18px/1.5 system-ui;max-width:1000px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            "<h1>ReproCase — запуск с подтверждением пользователя</h1><p>Учебный пример с намеренной ошибкой. "
            "Модель выбирает вход; доказательство проверяется локально. Это не оценка качества на всём наборе.</p>"
            f"<p>Источник: {html.escape(record['origin'])}; модель: {html.escape(record['model'])}</p>"
            f"<pre>{html.escape(record['summary_ru'])}</pre><p><a href='result.json'>Данные запуска</a></p></html>\n")
    (directory / "report.html").write_text(body, encoding="utf-8")
    return directory / "report.html"


def _redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, dict):
        return {k: _redact(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


class LiveJob:
    """Один запуск на экземпляр; Tk читается и обновляется только главным потоком."""
    directory_prefix = "live-search-"
    def __init__(self, output_dir=None, session_factory=LiveSession):
        self.output_dir = app.ROOT / "outputs" if output_dir is None else search.local_path(output_dir)
        self.session_factory = session_factory
        self.cancelled = threading.Event()
        self.results = queue.Queue()
        self._lock = threading.Lock()
        self.started = False
        self.thread = None

    def start(self, case, key, *, confirmed=False):
        if confirmed is not True:
            raise client.ClientError("Нужно явное подтверждение отправки.")
        case = copy.deepcopy(self._validate_payload(case))
        if type(key) is not str or not 1 <= len(key) <= 512 or any(not 33 <= ord(ch) <= 126 for ch in key):
            raise client.ClientError("Введите ключ без пробелов и управляющих символов.")
        with self._lock:
            if self.started:
                raise client.ClientError("Этот запуск уже начат; повтор не выполняется.")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix=self.directory_prefix, dir=self.output_dir))
            self.thread = threading.Thread(target=self._work, args=(case, key, directory), daemon=False)
            self.started = True
            self.thread.start()

    def _validate_payload(self, case):
        return app.validate_case(case)

    def cancel(self):
        self.cancelled.set()

    def poll(self):
        try:
            return self.results.get_nowait()
        except queue.Empty:
            return None

    def _work(self, case, key, directory):
        session = None
        try:
            session = self.session_factory(self.cancelled)
            record = adapter.run_case(case, "buggy", session, key)
        except Exception:
            record = {"result": None, "events": copy.deepcopy(session.events) if session else [],
                      "accounting": session.accounting() if session else {"network_attempts": 0, "attempted_requests": 0,
                        "tokens": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}},
                      "failure": "Запуск остановлен из-за внутренней ошибки. Повтора нет; сведения о расходе могут быть неполными."}
        try:
            record = _redact(record, key)
            record.update(origin="REAL_API" if record["accounting"]["network_attempts"] else "LOCAL_NO_REQUEST",
                          model=MODEL, endpoint=HOST, cancel_requested=self.cancelled.is_set(),
                          limits={"requests": MAX_REQUESTS, "trials": 3, "completion_tokens_per_request": MAX_COMPLETION_TOKENS,
                                  "serialized_request_bytes": MAX_REQUEST_BYTES}, cost=estimate_cost(record["events"]))
            for event in record["events"]:
                event.pop("response_text", None)
            record["summary_ru"] = summary(record)
            try:
                path = save_report(record, directory)
                outcome = {"record": record, "directory": str(directory), "report": str(path), "save_error": None}
            except Exception:
                outcome = {"record": record, "directory": str(directory), "report": None,
                           "save_error": "Отчёт не сохранён полностью. Результат остался в памяти; можно повторить только сохранение."}
            self.results.put(outcome)
        finally:
            key = None
            session = None
