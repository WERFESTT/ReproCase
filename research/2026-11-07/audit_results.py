"""Независимая сверка сохранённой серии и повтор переносимых тестов."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def audit(replay=False):
    report = json.loads((ROOT / 'results/offline_v2/results.json').read_text())
    protocol = json.loads((ROOT / 'protocol_offline.json').read_text())
    rows = report['rows']
    assert report['status'] == 'COMPLETED'
    assert report['protocol'] == protocol
    assert report['protocol_sha256'] == digest(ROOT / 'protocol_offline.json')
    assert report['driver_sha256'] == digest(ROOT / 'run_offline.py')
    assert report['source_after_run']['matched'] is True
    expected = {(c['id'], limit, method, seed, version)
                for c in protocol['conditions']
                for limit in protocol['budgets_search_and_shrink']
                for method, seeds in protocol['methods'].items()
                for seed in seeds for version in protocol['versions']}
    actual = {(r['condition'], r['limit'], r['method'], r['seed'], r['target_version']) for r in rows}
    assert len(rows) == len(actual) == len(expected) == 120
    assert actual == expected
    assert all(r['state'] == 'COMPLETED' for r in rows)
    assert report['new_model_requests'] == 0
    totals = {}
    replays = []
    for row in rows:
        search = row['search']
        assert 0 <= search['search_shrink_calls'] <= row['limit']
        assert 0 <= search['confirmation_calls'] <= 4
        assert search['total_calls'] == search['search_shrink_calls'] + search['confirmation_calls']
        if row['target_version'] == 'after':
            assert search['witness'] is None
        total = totals.setdefault(row['condition'], {'before': 0, 'confirmed_exports': 0, 'after': 0, 'after_findings': 0})
        total[row['target_version']] += 1
        total['after_findings'] += int(row['target_version'] == 'after' and search['witness'] is not None)
        if row.get('export', {}).get('confirmed'):
            total['confirmed_exports'] += 1
            assert row['target_version'] == 'before'
            assert search['outcome'] == 'REGRESSION_CONFIRMED'
            for version in ('before', 'after'):
                expected_counts = {'tests': 1, 'failures': int(version == 'before'), 'errors': 0}
                assert row['export'][version]['counts'] == expected_counts
                assert row['export'][version]['exit_code'] == int(version == 'before')
                if replay:
                    bundle = ROOT / row['export_relative_path']
                    if not bundle.is_dir():
                        bundle = ROOT / 'exports/offline_v2' / Path(row['export_relative_path']).name
                    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(bundle / 'test_prepared.py'), '--version', version],
                                            cwd=bundle, text=True, capture_output=True, timeout=20)
                    try:
                        counts = json.loads(result.stdout.strip().splitlines()[-1])
                    except (ValueError, IndexError):
                        counts = None
                    matched = counts == expected_counts and result.returncode == int(version == 'before')
                    replays.append({'id': row['id'], 'version': version, 'matched': matched,
                                    'exit_code': result.returncode, 'counts': counts,
                                    'stdout': result.stdout, 'stderr': result.stderr})
                    assert matched, 'Переносимый тест не подтвердился: ' + str(row['id'])
    summary_file = json.loads((ROOT / 'results/offline_v2/summary.json').read_text())
    assert report['summary'] == summary_file
    for name, total in totals.items():
        groups = [x for x in summary_file if x['condition'] == name]
        assert total['before'] == sum(x['before_scheduled'] for x in groups)
        assert total['after'] == sum(x['after_scheduled'] for x in groups)
        assert total['confirmed_exports'] == sum(x['before_export_confirmed'] for x in groups)
        assert total['after_findings'] == sum(x['after_counterexamples'] for x in groups)
        assert sum(x['errors'] + x['not_started'] for x in groups) == 0
    old = json.loads((ROOT / 'results/offline_v1/results.json').read_text())
    return {'status': 'VERIFIED', 'primary_run': 'offline_v2', 'searches': len(rows),
            'condition_totals': totals, 'portable_test_replays': replays,
            'verified_replays': sum(x['matched'] for x in replays),
            'primary_results_sha256': digest(ROOT / 'results/offline_v2/results.json'),
            'prior_run': {'id': 'offline_v1', 'included_in_primary': False,
                          'saved_state': old['status'],
                          'saved_completed_rows': sum(r['state'] == 'COMPLETED' for r in old['rows']),
                          'reason_ru': 'Полная сводка не совпала с сохранностью подробного журнала. Данные оставлены отдельно; серия повторена целиком.'},
            'new_model_requests': 0}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()
    report = audit(args.replay)
    if args.save:
        with (ROOT / 'results/verification.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'portable_test_replays'}, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
