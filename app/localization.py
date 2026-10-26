"""RU/EN presentation only. Never rewrite protocol objects or stored evidence."""
import argparse
import builtins
from contextlib import contextmanager
from functools import wraps
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / 'language_settings.json'
TRANSLATIONS = json.loads((ROOT / 'translations.json').read_text(encoding='utf-8'))
_PATTERN = re.compile('|'.join(re.escape(s) for s in sorted(TRANSLATIONS, key=len, reverse=True)))
LANGUAGES = ('ru', 'en')
_language = 'ru'
cli_language = None


def set_language(language):
    global _language
    if language not in LANGUAGES:
        raise ValueError('Language must be ru or en')
    _language = language


def get_language():
    return _language


def load_setting(path=None):
    path = SETTINGS_PATH if path is None else Path(path)
    try:
        if path.is_symlink():
            raise ValueError('symlink')
        with path.open('rb') as stream:
            raw = stream.read(1025)
        def unique(pairs):
            if len(dict(pairs)) != len(pairs):
                raise ValueError('duplicate')
            return dict(pairs)
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if len(raw) > 1024 or type(value) is not dict or set(value) != {'language'} or value['language'] not in LANGUAGES:
            raise ValueError('invalid setting')
        return value['language'], None
    except FileNotFoundError:
        return 'ru', None
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        return 'ru', 'Настройка языка недоступна или неверна; выбран русский язык.'


def save_setting(language, path=None):
    if language not in LANGUAGES:
        raise ValueError('Language must be ru or en')
    path = SETTINGS_PATH if path is None else Path(path)
    if path.is_symlink():
        raise ValueError('Settings cannot be a symlink')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.reprocase-language-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({'language': language}) + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextmanager
def using(language):
    previous = get_language()
    set_language(language)
    try:
        yield
    finally:
        set_language(previous)


def text(value, language=None):
    """Translate authored prose, preserving embedded JSON and fenced raw data."""
    if not isinstance(value, str) or (language or _language) == 'ru':
        return value
    if language is not None and language not in LANGUAGES:
        raise ValueError('Language must be ru or en')
    exact = {'с': 's', 'да': 'yes', 'нет': 'no'}
    if value.strip() in exact:
        return value.replace(value.strip(), exact[value.strip()])
    decoder = json.JSONDecoder()
    protected = []; i = 0
    while i < len(value):
        if value[i] in '{[':
            try:
                obj, end = decoder.raw_decode(value, i)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(obj, (dict, list)):
                    protected.append((i,end)); i = end; continue
        i += 1
    def replace(match):
        # Exact authored prose can include a numeric example; fields inside
        # raw protocol JSON are never translated.
        if any(start <= match.start() < end for start,end in protected):
            return match.group()
        return TRANSLATIONS[match.group()]
    return _PATTERN.sub(replace, value)


def html_text(value, language=None):
    language = language or _language
    if language == 'ru':
        return value
    # Only text nodes; attributes/data-field IDs, hrefs and raw JSON stay intact.
    parts = re.split(r'(<[^>]*>)', value)
    for i in range(0, len(parts), 2):
        parts[i] = html.escape(text(html.unescape(parts[i]), language), quote=False)
    value = ''.join(parts)
    return re.sub(r'(<html\b[^>]*\blang=)([\x22\x27])ru\2', r'\1\2en\2', value, count=1)


def localized_html(fn):
    @wraps(fn)
    def render(*args, **kwargs):
        return html_text(fn(*args, **kwargs))
    return render


def localized_text(fn):
    @wraps(fn)
    def render(*args, **kwargs):
        return text(fn(*args, **kwargs))
    return render


def print(*args, **kwargs):
    builtins.print(*(text(x) for x in args), **kwargs)


def cli_args(argv=None):
    global cli_language
    args = list(sys.argv[1:] if argv is None else argv)
    chosen = None; remaining = []; i = 0
    while i < len(args):
        item = args[i]
        if item == '--language':
            if i + 1 >= len(args):
                raise ValueError('--language requires ru or en')
            chosen = args[i + 1]; i += 2
        elif item.startswith('--language='):
            chosen = item.split('=', 1)[1]; i += 1
        else:
            remaining.append(item); i += 1
    if chosen is not None:
        set_language(chosen)
        cli_language = chosen
    return remaining


class ArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help() + text("\nЯзык: --language ru|en (не меняет данные запроса).\n")

    def _print_message(self, message, file=None):
        super()._print_message(text(message), file)
