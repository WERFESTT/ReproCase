"""Сверка обезличенной копии. Это не проверка байтов исходной версии Git."""
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def verify_source():
    inventory = json.loads((ROOT / 'source_inventory.json').read_text())
    for item in inventory['files']:
        path = ROOT / 'source' / item['path']
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT / 'source'):
            raise ValueError('Небезопасный путь исходника')
        raw = path.read_bytes()
        if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Изменён исходник: ' + item['path'])
    return {'checked_text_files': len(inventory['files']), 'matched': True,
            'commit': 'provider-neutral-derivative-v2', 'base_commit': inventory['base_commit'],
            'binary_wheels_transferred': False, 'not_byte_identical_to_original_commit': True}


if __name__ == '__main__':
    print(json.dumps(verify_source(), ensure_ascii=False, indent=2))
