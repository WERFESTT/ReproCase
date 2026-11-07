"""Параметры подключения без секретов; чтение и сохранение не обращаются к сети."""

from dataclasses import asdict, dataclass
import ipaddress
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


SETTINGS_PATH = Path(__file__).resolve().parent / "connection_settings.json"
TOKEN_PARAMETERS = ("max_tokens", "max_completion_tokens")
MAX_SETTINGS_BYTES = 4096


class ClientError(ValueError):
    """Сообщение без ключа и необработанного ответа сервера."""


def validate_model(model):
    if (type(model) is not str or not 1 <= len(model) <= 256
            or any(not 33 <= ord(ch) <= 126 for ch in model)):
        raise ClientError("Укажите идентификатор модели: до 256 символов, без пробелов и управляющих знаков.")
    return model


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_endpoint(endpoint):
    if (type(endpoint) is not str or not 1 <= len(endpoint) <= 2048
            or any(not 33 <= ord(ch) <= 126 for ch in endpoint)
            or any(ch in endpoint for ch in ("\\", "?", "#"))):
        raise ClientError("Нужен полный адрес HTTP/HTTPS без пробелов, параметров запроса и фрагмента.")
    try:
        parts = urlsplit(endpoint)
        host, port = parts.hostname, parts.port
        if (parts.scheme not in ("http", "https") or not host or not parts.path.startswith("/")
                or parts.username is not None or parts.password is not None
                or port is not None and not 1 <= port <= 65535):
            raise ValueError
        try:
            ipaddress.ip_address(host)
            if "%" in host:
                raise ValueError
        except ValueError:
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                          for label in host.rstrip(".").split(".")):
                raise ValueError
        if host.rstrip(".") == "invalid" or host.rstrip(".").endswith(".invalid"):
            raise ClientError("Адрес-заглушка недопустим. Укажите работающий сервер.")
        if parts.scheme == "http" and not _loopback(host):
            raise ClientError("HTTP разрешён только для localhost или адреса обратной петли. Для удалённого сервера нужен HTTPS.")
        return parts
    except ClientError:
        raise
    except ValueError:
        raise ClientError("Некорректный адрес сервера. Укажите полный путь API без имени пользователя и пароля.") from None


@dataclass(frozen=True)
class ConnectionSettings:
    endpoint: str
    model: str
    auth: str = "none"
    token_parameter: str = "max_tokens"

    def __post_init__(self):
        validate_endpoint(self.endpoint)
        validate_model(self.model)
        if self.auth not in ("none", "bearer") or self.token_parameter not in TOKEN_PARAMETERS:
            raise ClientError("Выберите допустимую авторизацию и параметр лимита ответа.")

    @property
    def requires_key(self):
        return self.auth == "bearer"

    @property
    def is_loopback(self):
        return _loopback(urlsplit(self.endpoint).hostname)

    def as_dict(self):
        return asdict(self)

    def validate_key(self, key):
        if not self.requires_key:
            if key is not None:
                raise ClientError("Для подключения без авторизации ключ не принимается.")
        elif (type(key) is not str or not 1 <= len(key) <= 512
              or any(not 33 <= ord(ch) <= 126 for ch in key)):
            raise ClientError("Введите ключ без пробелов и управляющих символов.")


def require_settings(value):
    if type(value) is not ConnectionSettings:
        raise ClientError("Сначала настройте подключение модели: адрес сервера и идентификатор модели.")
    return value


def _unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ClientError("Настройки содержат повторяющиеся поля.")
    return result


def load_settings(path=None):
    path = SETTINGS_PATH if path is None else Path(path)
    try:
        if path.is_symlink():
            raise ValueError
        with path.open("rb") as stream:
            raw = stream.read(MAX_SETTINGS_BYTES + 1)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if (len(raw) > MAX_SETTINGS_BYTES or type(data) is not dict
                or set(data) != {"endpoint", "model", "auth", "token_parameter"}):
            raise ValueError
        return ConnectionSettings(**data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ClientError("Настройки подключения недоступны или неверны. Откройте «Подключение модели…» и сохраните их заново.") from None


def save_settings(settings, path=None):
    settings = require_settings(settings)
    path = SETTINGS_PATH if path is None else Path(path)
    if path.is_symlink():
        raise ClientError("Файл настроек не должен быть символической ссылкой.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".connection-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings.as_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def connection_text(settings):
    if settings is None:
        return "Подключение модели не настроено. Это локальный предпросмотр; отправка недоступна."
    settings = require_settings(settings)
    authorization = "ключ доступа (Bearer)" if settings.requires_key else "без ключа"
    return (f"Модель: {settings.model}\nАдрес: {settings.endpoint}\n"
            f"Авторизация: {authorization}\nПараметр лимита ответа: {settings.token_parameter}")


COST_NOTICE = ("Тариф выбранного сервера не задан; стоимость неизвестна. "
               "Лимиты запросов и токенов не являются денежным лимитом. "
               "Отмена и тайм-аут не доказывают отсутствие списания за отправленный запрос.")
