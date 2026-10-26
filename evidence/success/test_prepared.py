"""Закреплённый локальный тест; рядом должны быть все файлы комплекта."""
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
HASHES = {'benchmark.py': '83364d2767e2504fe9c28025f1b4b9f17205d162784f892b519f4a1ef2df1a66', 'benchmark_adapter.py': 'f07a88d0e22b87e78c2eb2a775701c0b9e3d2a59fc3aca44390fde3ea37fbfcf', 'benchmark_diagnostics.py': '0a0e3cc387a9923f0552e44e5fa31e0c76abf013b63421ebf319d0346ea202f3', 'calculations.py': '8f605395d27971c776050205246b74ab0e5b17062cd8aa04d9b6640f3545df73', 'catalog.py': '574085d8c1478755993153193491db52fc033ba31e138a387a6243e8418706fa', 'historical_core.py': '205b6dfcce09f3719d7cbc621757ea8e5a1fd706af92bbd4b02d0d95af713bf5', 'historical_pilot.py': 'd0cc1ce2fda764b11a9f9883ff3909ddf90d69f7b4717f63e72be0524706ce6f', 'historical_plan.json': 'af81d9a69a34848f39d72404ac4fcb67554bac16a8b6b792d430a13dc27fd2cc', 'historical_vendor/cpython_correlation/after/LICENSE': '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf', 'historical_vendor/cpython_correlation/after/Lib/statistics.py': '768169a7717439c0e043515ad2d315c619c99ca764a7bf0f5fdbf15cd48551f3', 'historical_vendor/cpython_correlation/before/LICENSE': '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf', 'historical_vendor/cpython_correlation/before/Lib/statistics.py': '62c87a06c233f8e49407201e1b5596beaac2ca255000c09317814c248ebc418a', 'historical_vendor/cpython_correlation/fix-commit.json': 'cde3be9f6580f2e68d53ef0af83c3281bf23fba62089117f5219fa7f4dbab867', 'historical_vendor/manifest.json': '36fd26e6e5f8c030631dcbcc180b78950076f27d0d141c3bb54dd5756c8ea3cb', 'historical_vendor/more_reversed/after/LICENSE': '09f1c8c9e941af3e584d59641ea9b87d83c0cb0fd007eb5ef391a7e2643c1a46', 'historical_vendor/more_reversed/after/more_itertools/__init__.py': 'eeff04c91d301040b38262d7090133b042795dd44d5ecdcfd4d8269abcac0306', 'historical_vendor/more_reversed/after/more_itertools/more.py': 'ba7159b4dbb69ddd0a4836369012ae26f4106d7570326c24d25774cd32173be2', 'historical_vendor/more_reversed/after/more_itertools/recipes.py': '6aff1f84b0a70b96c102e3b92a70539255f1489765fc54140b1c1478f95b4828', 'historical_vendor/more_reversed/before/LICENSE': '09f1c8c9e941af3e584d59641ea9b87d83c0cb0fd007eb5ef391a7e2643c1a46', 'historical_vendor/more_reversed/before/more_itertools/__init__.py': 'eeff04c91d301040b38262d7090133b042795dd44d5ecdcfd4d8269abcac0306', 'historical_vendor/more_reversed/before/more_itertools/more.py': 'd4f4133e2c5b904ca0fa4f4a45063c50a5b5e2260f8e10670397afab4ca34cd7', 'historical_vendor/more_reversed/before/more_itertools/recipes.py': '6aff1f84b0a70b96c102e3b92a70539255f1489765fc54140b1c1478f95b4828', 'historical_vendor/more_reversed/fix-commit.json': 'c86e39ef4343743db0ac3c8fa8742a2725525ec301ee254d9b20993210c66620', 'input_search.py': 'a5c70cedc1f710a2ee6603845f07cc907676ff77a631929e1a7161bc62fd65de', 'input_search_adapter.py': '2ee3ec6bd877d033ee94ce6b50bd2466d5c6ea83a944ee797b971c2f1d1a46b5', 'live_search.py': 'b9c060a07fcf7d5092dacebb6f38835e5a2fd102f7c2a65c47ccf8535dab59fd', 'nebius_client.py': '9c22630a9aaf38c17b213124f9574858f0a2cf7f188d8dfc7a48d173973ad2d6', 'property_contract.py': 'eda44ed8fe0e19b150de05a5c48e3bfd85c396135bf3233d7c886a6c1dfbf7c6', 'property_live.py': '65b9d7f7c5d789c8672fc56ca2a680872100741652f4e3d1504cc8079cfe7d1f', 'property_preparation.py': '4652cb12e81e70e266560716d4a7cd9fc55d5a21e806b050bafcbc3ef1ab49d7', 'property_protocol.json': 'e42317321a60ba8831bc28a8b144a9a00b8afca0ad2add108d39a8350b630c56', 'proposals.py': 'cb877852279037fff840ac0f3c52f8c83e5b2f3ebee2f3b45d786c734b78bc02', 'reference.py': '1bb2b51ae30ff1976727af2a12aa34a32ce05a25e22f5a73ded470c57b2a91c2', 'regression.py': '2c063ed41d079b3c2a12e32cf23da73a963ad4c3e7cb91fc6a6c100af04b4d14', 'reporting.py': '4302fe865ecf1d0f670f46a68f64f95af09dc0721618d6b486639281529d0adc', 'reprocase.py': '98924c86edbeaee4cf86867c129dcf04176c57963e612a3eb147b59dfed684b0'}
WITNESS_SHA256 = '0d9387fa065a3028ecfd8cd453a991a34556f620f27c70c11707fa1688afe312'

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
