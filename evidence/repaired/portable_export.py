"""Локальный комплект закреплённых исходников для повторения одного теста.

Не установщик и не публичная сборка. Никаких путей или кода из ответа модели.
"""
import hashlib
import json
from pathlib import Path


MODULES = ('benchmark', 'benchmark_adapter', 'benchmark_diagnostics', 'calculations', 'catalog',
           'historical_core', 'historical_pilot', 'input_search', 'input_search_adapter',
           'live_search', 'nebius_client', 'property_contract', 'property_live',
           'property_preparation', 'proposals', 'reference', 'regression', 'reporting', 'reprocase')
DATA_FILES = ('historical_plan.json', 'property_protocol.json', 'historical_vendor/manifest.json')

TEMPLATE = r'''"""Закреплённый локальный тест; рядом должны быть все файлы комплекта."""
import sys
if not sys.flags.isolated or not sys.flags.no_site:
    print('Запустите: python3 -I -S -B test_prepared.py --version before (или after)', file=sys.stderr)
    raise SystemExit(2)
sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
HASHES = __PINNED_HASHES__
WITNESS_SHA256 = __WITNESS_SHA256__

def checked_bytes(name):
    path = ROOT / name
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise ValueError('Ссылка или путь вне комплекта: ' + name)
    value = path.read_bytes()
    if hashlib.sha256(value).hexdigest() != HASHES[name]:
        raise ValueError('Файл комплекта изменён: ' + name)
    return value

class PinnedLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        # Не используем pyc даже при совпадении времени/размера исходника.
        name = Path(self.path).relative_to(ROOT).as_posix()
        return self.source_to_code(checked_bytes(name), self.path)

class PinnedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        name = fullname + '.py'
        if name not in HASHES:
            return None
        source = str(ROOT / name)
        return importlib.util.spec_from_file_location(fullname, source, loader=PinnedLoader(fullname, source))

def prepare():
    for path in ROOT.rglob('*'):
        if path.is_symlink() or path.suffix == '.pyc' or path.name == '__pycache__':
            raise ValueError('Ссылки и кэш байткода не допускаются в комплекте.')
    for name in HASHES:
        checked_bytes(name)
    witness = ROOT / 'witness.json'
    if witness.stat().st_size > 65536 or hashlib.sha256(witness.read_bytes()).hexdigest() != WITNESS_SHA256:
        raise ValueError('Свидетельство изменено или слишком велико.')
    # sys.path остаётся путём изолированного Python. Разрешены только закреплённые модули.
    sys.meta_path.insert(0, PinnedFinder())
    from property_live import replay
    return replay

parser = argparse.ArgumentParser(description='Локальный повтор: тот же тест до/после исправления.')
parser.add_argument('--version', required=True, choices=('before', 'after'))
args = parser.parse_args()
try:
    replay = prepare()
except Exception as error:
    print('Ошибка комплекта: ' + str(error), file=sys.stderr)
    print(json.dumps({'tests': 0, 'failures': 0, 'errors': 1}))
    raise SystemExit(2)

class Regression(unittest.TestCase):
    def test_property(self):
        replay(ROOT / 'witness.json', args.version)

result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Regression))
print(json.dumps({'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors)}))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def runtime_names(source_root):
    root = Path(source_root).resolve()
    manifest = json.loads((root/'historical_vendor/manifest.json').read_text(encoding='utf-8'))
    names = [module+'.py' for module in MODULES] + list(DATA_FILES)
    # Прежний закреплённый код до исправления не импортирует этот модуль.
    if (root/'portable_export.py').is_file():
        names.append('portable_export.py')
    for name in manifest['files']:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not name.startswith('historical_vendor/'):
            raise ValueError('Некорректный путь исторического исходника.')
        names.append(name)
    return sorted(set(names))


def write_bundle(source_root, directory, witness_text):
    """Пути задаёт локальный экспортёр; предложение модели их не определяет."""
    source_root = Path(source_root).resolve()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('Для нового комплекта нужна пустая папка; существующие файлы не перезаписаны.')
    contents = {}
    for name in runtime_names(source_root):
        source = source_root/name
        if not source.resolve().is_relative_to(source_root) or any(
                p.is_symlink() for p in (source, *source.parents) if p != source_root and p.is_relative_to(source_root)):
            raise ValueError('Исходники должны быть обычными файлами внутри выбранного комплекта.')
        contents[name] = source.read_bytes()
    manifest = json.loads(contents['historical_vendor/manifest.json'])
    for name, item in manifest['files'].items():
        if hashlib.sha256(contents[name]).hexdigest() != item['sha256']:
            raise ValueError('Исторические исходники не соответствуют манифесту.')
    raw = witness_text.encode('utf-8')
    if len(raw) > 65536:
        raise ValueError('Слишком большое свидетельство.')
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
    for name, content in contents.items():
        path = directory/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (directory/'witness.json').write_bytes(raw)
    (directory/'test_prepared.py').write_text(TEMPLATE.replace('__PINNED_HASHES__', repr(hashes)).replace(
        '__WITNESS_SHA256__', repr(hashlib.sha256(raw).hexdigest())), encoding='utf-8')
    (directory/'runtime-manifest.json').write_text(json.dumps({
        'format':'reprocase-local-replay-bundle-v1', 'source_sha256':hashes,
        'witness_sha256':hashlib.sha256(raw).hexdigest(),
        'requires':'CPython 3.14; стандартная библиотека. Hypothesis нужен для поиска, не для этого повтора.',
        'scope':'Локальный комплект проверки, не публикация и не выбранная лицензия ReproCase.',
    }, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (directory/'README.txt').write_text(
        'Переносите эту папку целиком, включая historical_vendor и все файлы .py/.json.\n'
        'Это не одиночный самостоятельный тест: рядом закреплены его исходники и лицензии.\n'
        'Нужен CPython 3.14 с обычной стандартной библиотекой; GUI, Hypothesis, ключ и сеть для повтора не нужны.\n'
        'python3 -I -S -B /путь/к/этой/папке/test_prepared.py --version before\n'
        'python3 -I -S -B /путь/к/этой/папке/test_prepared.py --version after\n'
        'До: ожидается один assertion-провал, exit 1; после: успех, exit 0. Ошибка комплекта: exit 2.\n'
        'Запускайте команды отдельно, без &&. Не создавайте __pycache__ внутри комплекта.\n'
        'Содержимое не ищется в старом проекте/cwd/PYTHONPATH; запуск требует -I -S.\n'
        'Хеши обнаруживают изменение относительно этого доверенного теста; это не подпись автора\n'
        'и не защита от согласованной замены самого теста и всех хешей.\n'
        'Сторонние лицензии сохранены. Лицензия нового кода владельцем ещё не выбрана.\n', encoding='utf-8')
    return directory/'test_prepared.py'
