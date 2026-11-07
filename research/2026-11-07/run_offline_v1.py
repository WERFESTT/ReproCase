"""Новый локальный эксперимент на неизменённых исходниках ReproCase."""
import sys
sys.dont_write_bytecode = True
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from preflight import verify_source

def block_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                 'socket.gethostbyaddr', 'socket.bind'):
        raise PermissionError('В этом эксперименте сеть запрещена.')

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

def planned_rows(protocol):
    rows = []
    for condition in protocol['conditions']:
        for limit in protocol['budgets_search_and_shrink']:
            for method, seeds in protocol['methods'].items():
                for seed in seeds:
                    for version in protocol['versions']:
                        rows.append({'id': len(rows) + 1, 'condition': condition['id'],
                                     'case_id': condition['case_id'], 'origin': condition['origin'],
                                     'limit': limit, 'method': method, 'seed': seed,
                                     'target_version': version, 'state': 'NOT_STARTED'})
    if len(rows) != protocol['planned_searches']:
        raise ValueError('Число запланированных поисков не совпало.')
    return rows

def load_proposals(protocol, prep):
    proposals = {}
    sources = {}
    for condition in protocol['conditions']:
        expected = prep.proposed(condition['case_id'], condition['assertion'], condition['domain'])
        if condition['origin'] == 'SAVED_REAL_API_REPLAY':
            path = ROOT / 'source' / condition['record']
            record = json.loads(path.read_text())['record']
            actual = record['rows'][condition['row_index']]['proposal']
            if actual != expected:
                raise ValueError('Сохранённое предложение отличается от протокола.')
            proposals[condition['id']] = copy.deepcopy(actual)
            sources[condition['id']] = {'record_path': condition['record'], 'sha256': sha256(path),
                                        'row_index': condition['row_index']}
        else:
            proposals[condition['id']] = expected
            sources[condition['id']] = {'origin': condition['origin']}
    return proposals, sources

def summarize(rows):
    summary = []
    for key in sorted({(r['condition'], r['method'], r['limit']) for r in rows}):
        group = [r for r in rows if (r['condition'], r['method'], r['limit']) == key]
        before = [r for r in group if r['target_version'] == 'before']
        after = [r for r in group if r['target_version'] == 'after']
        summary.append({'condition': key[0], 'method': key[1], 'limit': key[2],
                        'before_scheduled': len(before),
                        'before_found': sum(r.get('search', {}).get('outcome') == 'REGRESSION_CONFIRMED' for r in before),
                        'before_export_confirmed': sum(bool(r.get('export', {}).get('confirmed')) for r in before),
                        'after_scheduled': len(after),
                        'after_counterexamples': sum(r.get('search', {}).get('witness') is not None for r in after),
                        'errors': sum(r['state'] in ('SEARCH_ERROR', 'EXPORT_ERROR') for r in group),
                        'not_started': sum(r['state'] == 'NOT_STARTED' for r in group),
                        'search_shrink_calls': sum(r.get('search', {}).get('search_shrink_calls', 0) for r in group),
                        'confirmation_calls': sum(r.get('search', {}).get('confirmation_calls', 0) for r in group),
                        'export_process_attempts': sum(r.get('export', {}).get('process_attempts', 0) for r in group)})
    return summary

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Выполнить новую серию без сети.')
    args = parser.parse_args()
    protocol_path = ROOT / 'protocol_offline.json'
    protocol = json.loads(protocol_path.read_text())
    rows = planned_rows(protocol)
    if not args.run:
        print(json.dumps({'protocol': protocol['id'], 'searches': len(rows), 'new_model_requests': 0,
                          'run_command': '/usr/bin/python3 -I -B run_offline.py --run'}, ensure_ascii=False))
        return 0
    qualification = verify_source()
    if list(sys.version_info[:2]) != protocol['environment']['python_major_minor']:
        raise RuntimeError('Этот протокол закреплён за Python 3.12; нужна отдельная серия для иной среды.')
    import hypothesis
    import sortedcontainers
    if hypothesis.__version__ != protocol['environment']['hypothesis'] or sortedcontainers.__version__ != protocol['environment']['sortedcontainers']:
        raise RuntimeError('Версии зависимостей не соответствуют новому протоколу.')
    out = ROOT / 'results' / 'offline_v1'
    out.mkdir(parents=True, exist_ok=False)
    os.environ['HYPOTHESIS_STORAGE_DIRECTORY'] = str(out / 'hypothesis-cache')
    sys.addaudithook(block_network)
    sys.path.insert(0, str(ROOT / 'source' / 'app'))
    import historical_core as core
    import property_preparation as prep
    import property_live as export_module
    core.verify_sources()
    proposals, proposal_sources = load_proposals(protocol, prep)
    export_root = core.ROOT / 'outputs' / 'research-domain-budget-v1'
    if export_root.exists():
        raise RuntimeError('Папка новых тестов уже существует; перезапись запрещена.')
    export_root.mkdir(parents=True)
    report = {'status': 'RUNNING', 'protocol': protocol, 'protocol_sha256': sha256(protocol_path),
              'driver_sha256': sha256(__file__), 'source': qualification,
              'environment': {'python': sys.version, 'executable': sys.executable,
                              'platform': platform.platform(), 'hypothesis': hypothesis.__version__,
                              'sortedcontainers': sortedcontainers.__version__},
              'proposal_sources': proposal_sources, 'new_model_requests': 0,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'rows': rows}
    write_json(out / 'results.json', report)
    start = time.perf_counter()
    for row in rows:
        row['state'] = 'RUNNING'
        proposal = proposals[row['condition']]
        try:
            run = prep.run_search(row['case_id'], proposal, row['method'], row['target_version'],
                                  row['seed'], row['limit'])
            row['search'] = run
            if run['outcome'] in ('HARNESS_ERROR', 'CANCELLED'):
                row['state'] = 'SEARCH_ERROR'
            else:
                row['state'] = 'COMPLETED'
            if run['search_shrink_calls'] > row['limit'] or run['confirmation_calls'] > 4:
                raise RuntimeError('Превышен зафиксированный предел.')
            if run['outcome'] == 'REGRESSION_CONFIRMED':
                export_row = {'case_id': row['case_id'], 'seed': row['seed'], 'proposal': proposal,
                              'origin': 'SAVED_REAL_API_REPLAY' if row['origin'] == 'SAVED_REAL_API_REPLAY' else 'SYNTHETIC'}
                try:
                    bundle = export_root / ('run-' + str(row['id']).zfill(3))
                    row['export'] = export_module.export_test(export_row, run, bundle)
                    row['export_relative_path'] = str(bundle.relative_to(ROOT))
                    if not row['export']['confirmed']:
                        row['state'] = 'EXPORT_ERROR'
                except Exception as error:
                    row['state'] = 'EXPORT_ERROR'
                    row['error'] = type(error).__name__ + ': ' + str(error)
        except Exception as error:
            row['state'] = 'SEARCH_ERROR'
            row['error'] = type(error).__name__ + ': ' + str(error)
        write_json(out / 'results.json', report)
        if row['id'] % 10 == 0:
            print('Завершено ' + str(row['id']) + '/' + str(len(rows)), flush=True)
    report['summary'] = summarize(rows)
    report['elapsed_seconds'] = time.perf_counter() - start
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['source_after_run'] = verify_source()
    report['status'] = 'COMPLETED_WITH_ERRORS' if any(r['state'] != 'COMPLETED' for r in rows) else 'COMPLETED'
    write_json(out / 'results.json', report)
    write_json(out / 'summary.json', report['summary'])
    with (out / 'summary.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(report['summary'][0]))
        writer.writeheader()
        writer.writerows(report['summary'])
    print(json.dumps({'status': report['status'], 'searches': len(rows),
                      'confirmed_exports': sum(bool(r.get('export', {}).get('confirmed')) for r in rows),
                      'false_findings_on_fixed': sum(r.get('search', {}).get('witness') is not None for r in rows if r['target_version'] == 'after'),
                      'errors': sum(r['state'] != 'COMPLETED' for r in rows),
                      'new_model_requests': 0, 'elapsed_seconds': report['elapsed_seconds']}, ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'COMPLETED' else 1

if __name__ == '__main__':
    raise SystemExit(main())
