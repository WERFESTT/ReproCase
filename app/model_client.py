"""Клиент для отдельно подтверждённого подключения. По умолчанию сеть закрыта.

Ничего не выполняется при импорте. Ключ принимается только явным аргументом;
окружение, файлы ключей и аккаунты этот модуль не ищет.
"""

from localization import print
import localization as loc

from dataclasses import dataclass
import http.client
import json
import ssl
import threading
import urllib.error
import urllib.request

from model_settings import ClientError, TOKEN_PARAMETERS, require_settings, validate_model

from proposals import demonstration_proposal, proposal_schema, validate_proposal
from reprocase import validate_case


DEFAULT_MODEL = "reprocase-offline"
MAX_INPUT_BYTES = 4096
MAX_RESPONSE_BYTES = 65_536
MAX_COMPLETION_TOKENS = 512
TIMEOUT_SECONDS = 30


class NetworkDisabled(ClientError):
    pass


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClientError("Ответ содержит повторяющиеся поля JSON.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ClientError("Ответ содержит недопустимую константу JSON.")


def build_request(case, model=DEFAULT_MODEL, *, token_parameter="max_completion_tokens"):
    """Только подготовка тела запроса; нет обращения к сети или ключу."""
    case = validate_case(case)
    validate_model(model)
    context = {"case": case, "allowed_proposal": demonstration_proposal(case)}
    messages = [
        {"role": "system", "content": (
            "Это учебная проверка ReproCase. Верни ровно один вызов propose_check, "
            "содержащий только разрешённое предложение для переданных данных. "
            "Не добавляй код, команды, пути или дополнительные поля. "
            "Ты не исполняешь проверку и не утверждаешь, что причина доказана."
        )},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]
    return build_function_request(messages, proposal_schema(), "propose_check", model, token_parameter=token_parameter)


def build_function_request(messages, schema, function_name, model=DEFAULT_MODEL, *, max_completion_tokens=None,
                           max_request_bytes=None, token_parameter="max_completion_tokens"):
    """Общие ограничения запросов; схема и имя берутся только из локального кода."""
    validate_model(model)
    if token_parameter not in TOKEN_PARAMETERS:
        raise ClientError("Неизвестный параметр лимита ответа.")
    if function_name not in ("propose_check", "diagnostic_action", "input_search_action"):
        raise ClientError("Неизвестная функция клиента.")
    tokens = MAX_COMPLETION_TOKENS if max_completion_tokens is None else max_completion_tokens
    if type(tokens) is not int or not 1 <= tokens <= 1024:
        raise ClientError("Недопустимый предел ответа.")
    if max_request_bytes is not None and (type(max_request_bytes) is not int or not 1 <= max_request_bytes <= 16000):
        raise ClientError("Недопустимый предел размера запроса.")
    if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > (max_request_bytes or MAX_INPUT_BYTES):
        raise ClientError("Инструкция и входные данные больше 4096 байт.")
    request = {
        "model": model, "messages": messages, "stream": False,
        token_parameter: tokens,
        "parallel_tool_calls": False,
        "tools": [{"type": "function", "function": {
            "name": function_name, "description": "Вернуть одно разрешённое диагностическое действие.",
            "parameters": schema,
        }}],
        "tool_choice": {"type": "function", "function": {"name": function_name}},
    }
    if max_request_bytes is not None and len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > max_request_bytes:
        raise ClientError("Полный сериализованный запрос превышает установленный предел.")
    return request


def _usage(value):
    """Отсутствующие сведения остаются неизвестными; ноль не подставляется."""
    if value is None:
        return None
    if type(value) is not dict:
        raise ClientError("Некорректная структура usage; потребление не установлено.")
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    result = {}
    for key in keys:
        item = value.get(key)
        if item is not None and (type(item) is not int or not 0 <= item <= 1_000_000_000):
            raise ClientError("Некорректное значение usage; потребление не установлено.")
        result[key] = item
    if all(result[key] is not None for key in keys):
        if result["total_tokens"] != result["prompt_tokens"] + result["completion_tokens"]:
            raise ClientError("Противоречивые значения usage; потребление не установлено.")
    details = value.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = details["cached_tokens"]
        if type(cached) is not int or cached < 0 or result["prompt_tokens"] is None or cached > result["prompt_tokens"]:
            raise ClientError("Некорректное число кешированных токенов.")
        result["cached_prompt_tokens"] = cached
    return result


@dataclass(frozen=True)
class ValidatedReply:
    proposal: dict
    usage: dict | None
    model: str


@dataclass(frozen=True)
class FunctionReply:
    arguments: str
    usage: dict | None
    model: str
    call_id: str


def decode_function_response(raw, expected_function, expected_model=DEFAULT_MODEL):
    """Проверить общий конверт tool call. Аргументы затем проверяет конкретный маршрут."""
    if expected_function not in ("propose_check", "diagnostic_action", "input_search_action"):
        raise ClientError("Неизвестная ожидаемая функция.")
    validate_model(expected_model)
    try:
        if type(raw) is str:
            raw = raw.encode("utf-8")
        if type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            raise ClientError("Ответ должен быть JSON размером не больше 65536 байт.")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object, parse_constant=_reject_constant)
        if type(data) is not dict or data.get("model") != expected_model:
            raise ClientError("Ответ пришёл не от ожидаемой модели либо не содержит её идентификатор.")
        choices = data.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ClientError("Нужен ровно один вариант ответа.")
        choice = choices[0]
        if type(choice.get("index")) is not int or choice["index"] != 0 or choice.get("finish_reason") != "tool_calls":
            raise ClientError("Ответ не завершён единственным вызовом проверки; расчёт запрещён.")
        message = choice.get("message")
        if type(message) is not dict or message.get("role") != "assistant":
            raise ClientError("Некорректное сообщение модели.")
        if message.get("refusal") not in (None, "") or message.get("content") not in (None, ""):
            raise ClientError("Ответ содержит отказ или посторонний текст; расчёт запрещён.")
        calls = message.get("tool_calls")
        if type(calls) is not list or len(calls) != 1 or type(calls[0]) is not dict:
            raise ClientError("Разрешён ровно один вызов функции.")
        call = calls[0]
        if set(call) != {"id", "type", "function"} or call["type"] != "function":
            raise ClientError("Неизвестная структура вызова функции.")
        if type(call["id"]) is not str or not 1 <= len(call["id"]) <= 200:
            raise ClientError("Некорректный идентификатор вызова.")
        function = call["function"]
        if type(function) is not dict or set(function) != {"name", "arguments"}:
            raise ClientError("Некорректная структура функции.")
        if function["name"] != expected_function or type(function["arguments"]) is not str:
            raise ClientError("Имя функции не совпадает с ожидаемым либо arguments не является строкой JSON.")
        usage = _usage(data.get("usage"))
        return FunctionReply(arguments=function["arguments"], usage=usage, model=expected_model, call_id=call["id"])
    except ClientError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ClientError("Ответ или предложение не прошло локальную проверку; расчёт не запускался.") from None


def decode_response(raw, case, expected_model=DEFAULT_MODEL):
    """Совместимый декодер прежней демонстрации; не используется для benchmark."""
    case = validate_case(case)
    reply = decode_function_response(raw, "propose_check", expected_model)
    try:
        proposal = validate_proposal(reply.arguments, case)
    except ValueError:
        raise ClientError("Предложение не прошло локальную проверку; расчёт не запускался.") from None
    return ValidatedReply(proposal=proposal, usage=reply.usage, model=reply.model)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        fp.close()
        raise ClientError("Перенаправление запрещено. Новый адрес и ключ не отправлены; автоматического повтора нет.")


def _https_once(body, api_key=None, *, settings=None):
    """Один POST на выбранный адрес: HTTPS или HTTP обратной петли; без повторов."""
    settings = require_settings(settings)
    settings.validate_key(api_key)
    if type(body) is not bytes or len(body) > 16000:
        raise ClientError("Превышен предел полного запроса.")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.requires_key:
        headers["Authorization"] = "Bearer " + api_key
    # Локальный сервер не направляется во внешний прокси. HTTPS использует
    # стандартные настройки прокси и доверия сертификатам окружения Python.
    proxy = urllib.request.ProxyHandler({}) if settings.is_loopback else urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(proxy, _NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(settings.endpoint, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ClientError(f"Сервер вернул HTTP {response.status}. Повтор не выполнен; потребление неизвестно.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ClientError("Ответ превысил предел размера. Потребление неизвестно; повтор не выполнен.")
            return raw
    except ClientError:
        raise
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise ClientError(f"Сервер вернул HTTP {status}. Повтор не выполнен; потребление неизвестно.") from None
    except (OSError, ValueError, http.client.HTTPException):
        raise ClientError("Сетевая ошибка или тайм-аут. Отмена у поставщика и потребление неизвестны; повтора нет.") from None


class OneShotClient:
    """Не более одной попытки на экземпляр. Ключ не хранится в объекте и не логируется."""

    def __init__(self, *, allow_network=False, model=None, settings=None):
        if type(allow_network) is not bool:
            raise ClientError("Некорректные параметры клиента.")
        if allow_network or settings is not None:
            settings = require_settings(settings)
        if settings is not None and model is not None and model != settings.model:
            raise ClientError("Модель не совпадает с подтверждённым подключением.")
        self.allow_network = allow_network
        self.settings = settings
        self.model = validate_model(settings.model if settings is not None else DEFAULT_MODEL if model is None else model)
        self.attempted = False
        self._lock = threading.Lock()

    def request_proposal(self, case, api_key=None):
        with self._lock:
            if not self.allow_network:
                raise NetworkDisabled("Сеть отключена. Реальная проба требует отдельного разрешения.")
            if self.attempted:
                raise ClientError("Попытка уже была; автоматический повтор запрещён.")
            self.settings.validate_key(api_key)
            case = validate_case(case)
            body = json.dumps(build_request(case, self.model, token_parameter=self.settings.token_parameter), ensure_ascii=False).encode("utf-8")
            self.attempted = True
        raw = _https_once(body, api_key, settings=self.settings)
        return decode_response(raw, case, self.model)


def artificial_response(case):
    """Явно искусственный ответ для офлайн-тестов. Он создан локально без обращения к модели."""
    case = validate_case(case)
    return {
        "id": "artificial-offline-example", "model": DEFAULT_MODEL, "usage": None,
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "artificial-call", "type": "function", "function": {
                "name": "propose_check", "arguments": json.dumps(demonstration_proposal(case)),
            }}],
        }}],
    }
