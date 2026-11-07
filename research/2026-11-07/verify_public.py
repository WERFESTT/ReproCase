#!/usr/bin/env python3
"""Проверка обезличенной исследовательской копии без сетевых обращений."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', action='store_true', help='Повторить переносимые тесты.')
    parser.add_argument('--save', type=Path, help='Сохранить новый отчёт без перезаписи.')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'SHA256SUMS.json').read_text())
    for entry in manifest['files']:
        path = ROOT / entry['path']
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Небезопасный путь: ' + entry['path'])
        raw = path.read_bytes()
        if len(raw) != entry['size'] or sha(raw) != entry['sha256']:
            raise ValueError('Файл отличается от контрольной суммы: ' + entry['path'])
    release = json.loads((ROOT / 'PUBLIC_RELEASE.json').read_text())
    outcomes = []
    groups = {'live': 0, 'offline_v2': 0, 'prior_evidence': 0}
    for bundle in release['bundles']:
        folder = ROOT / bundle['path']
        witness = json.loads((folder / 'witness.json').read_text())
        factual = {key: value for key, value in witness.items() if key not in ('source_sha256', 'content_sha256')}
        if sha(canonical(factual)) != bundle['unchanged_witness_facts_sha256']:
            raise ValueError('Изменены проверяемые данные свидетельства: ' + bundle['path'])
        if not args.replay:
            continue
        for version in ('before', 'after'):
            process = subprocess.run([sys.executable, '-I', '-S', '-B', str(folder / 'test_prepared.py'),
                                      '--version', version], cwd=ROOT, capture_output=True, text=True, timeout=20)
            try:
                counts = json.loads(process.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                counts = None
            expected = {'tests': 1, 'failures': int(version == 'before'), 'errors': 0}
            matched = counts == expected and process.returncode == int(version == 'before')
            outcomes.append({'bundle': bundle['path'], 'group': bundle['group'], 'version': version,
                             'matched': matched, 'exit_code': process.returncode, 'counts': counts,
                             'stderr': process.stderr, 'stdout': process.stdout})
            groups[bundle['group']] += int(matched)
    live = json.loads((ROOT / 'results/live/series.json').read_text())['record']
    offline = json.loads((ROOT / 'results/offline_v2/results.json').read_text())
    if live['state'] != 'COMPLETED' or len(live['rows']) != 6:
        raise ValueError('Неполный журнал сетевой серии')
    if offline['status'] != 'COMPLETED' or len(offline['rows']) != 120:
        raise ValueError('Неполный журнал локальной серии')
    if live['metrics']['model_property_sut_calls'] != 3911 or live['metrics']['manual_baseline_sut_calls'] != 3911:
        raise ValueError('Изменены итоги сетевой серии')
    if not all(row['state'] == 'COMPLETED' for row in offline['rows']):
        raise ValueError('Есть незавершённые поиски')
    report = {'state': 'VERIFIED' if all(row['matched'] for row in outcomes) else 'REPLAY_MISMATCH',
              'scope': 'SANITIZED_DERIVATIVE_REPLAY', 'files_verified': len(manifest['files']),
              'bundles': len(release['bundles']), 'replay_attempts': len(outcomes),
              'expected_outcomes': sum(row['matched'] for row in outcomes), 'groups': groups,
              'new_model_requests': 0, 'not_new_experimental_samples': True, 'runs': outcomes}
    if args.save:
        with args.save.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'runs'}, ensure_ascii=False, indent=2))
    return 0 if report['state'] == 'VERIFIED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
