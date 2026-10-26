#!/usr/bin/env python3
"""Конечный офлайн-пилот исторических дефектов и Hypothesis. Автоустановки и сети нет."""

from localization import print
import localization as loc

import argparse
from collections import Counter
import hashlib
import html
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

import historical_core as core
import reprocase as app

ROOT = core.ROOT
OUTPUT = ROOT / "outputs/historical-pilot"
PYTHON = ROOT / ".venv-historical/bin/python"
LABELS = {"cpython_correlation": "CPython: корреляция при разных масштабах",
          "more_reversed": "more-itertools: обратный числовой диапазон"}


class BudgetStop(BaseException):
    pass


def rank(value):
    return (sum(abs(x) for x in value), tuple(abs(x) for x in value), tuple(value))


def axes(case_id):
    result = []
    for index, (lo, hi) in enumerate(core.plan()["domains"][case_id]):
        excluded = core.plan().get("excluded_values", {}).get(case_id, {}).get(str(index), [])
        ordered = list(dict.fromkeys(x for x in (0, 1, -1, lo, hi, lo + 1, hi - 1) if lo <= x <= hi and x not in excluded))
        rest = [x for x in range(lo, hi + 1) if x not in excluded and x not in ordered]
        result.append((ordered, rest))
    return result


def boundary_candidates(case_id):
    """Границы/ноль/единицы и затем полный конечный домен. Исправление и версия не читаются."""
    dimensions = axes(case_id)
    seen = set()
    edges = [x[0] for x in dimensions]
    for index in range(max(map(len, edges))):
        value = tuple(axis[index % len(axis)] for axis in edges)
        seen.add(value)
        yield value
    for parts in (edges, [first + rest for first, rest in dimensions]):
        for value in itertools.product(*parts):
            if value not in seen:
                seen.add(value)
                yield value


def smaller_inputs(case_id, value):
    for index, old in enumerate(value):
        options = [0, 1 if old >= 0 else -1]
        reduced = old
        while abs(reduced) > 1:
            reduced = math.trunc(reduced / 2)
            options.append(reduced)
        options.append(old - (1 if old > 0 else -1))
        for new in dict.fromkeys(options):
            candidate = list(value); candidate[index] = new; candidate = tuple(candidate)
            try:
                core.validate_input(case_id, candidate)
            except core.HarnessError:
                continue
            if rank(candidate) < rank(value):
                yield candidate


class Ledger:
    def __init__(self, case_id, version, limit):
        self.case_id, self.version, self.limit = case_id, version, limit
        self.events = []
        self.failures = []

    def attempt(self, value, stage):
        value = core.validate_input(self.case_id, value)
        if len(self.events) >= self.limit:
            raise BudgetStop()
        started = time.perf_counter_ns()
        try:
            result = core.check(self.case_id, self.version, value)
        except core.HarnessError as error:
            self.events.append({"input": list(value), "stage": stage, "status": "HARNESS_ERROR",
                                "error": str(error), "check_wall_ns": time.perf_counter_ns() - started})
            raise
        failed = result["status"] != "PASS"
        self.events.append({"input": list(value), "stage": stage, **result,
                            "check_wall_ns": time.perf_counter_ns() - started})
        if failed:
            self.failures.append(value)
        return failed


def run_boundary(ledger):
    current = None
    for value in boundary_candidates(ledger.case_id):
        if ledger.attempt(value, "generate"):
            current = value
            break
    if current is None:
        return None
    while True:
        for value in smaller_inputs(ledger.case_id, current):
            if ledger.attempt(value, "shrink"):
                current = value
                break
        else:
            return current


def hypothesis_strategy(case_id):
    from hypothesis import strategies as st
    dimensions = []
    for index, (lo, hi) in enumerate(core.plan()["domains"][case_id]):
        excluded = core.plan().get("excluded_values", {}).get(case_id, {}).get(str(index), [])
        if excluded == [0]:
            dimensions.append(st.one_of(st.integers(lo, -1), st.integers(1, hi)))
        else:
            dimensions.append(st.integers(lo, hi))
    return st.tuples(*dimensions)


def run_hypothesis(ledger, seed):
    from hypothesis import find, settings, Phase
    from hypothesis.errors import NoSuchExample
    config = settings(max_examples=ledger.limit, database=None, deadline=None,
                      phases=(Phase.generate, Phase.shrink), derandomize=False, print_blob=False)
    def predicate(value):
        # Публичный API не сообщает фазу каждого вызова: после первой находки сюда входят
        # сокращение и внутренние перепроверки. Все они учитываются, без попытки их скрыть.
        return ledger.attempt(value, "post_failure_shrink_or_recheck" if ledger.failures else "generate")
    try:
        return tuple(find(hypothesis_strategy(ledger.case_id), predicate, settings=config, random=random.Random(seed)))
    except NoSuchExample:
        return None


def run_one(case_id, version, method, seed=None, repetition=0):
    if method not in ("boundary_grid", "hypothesis"):
        raise core.HarnessError("Неизвестный метод.")
    core.load_version(case_id, version)  # Подготовка окружения до измерения метода.
    limits = core.plan()
    ledger = Ledger(case_id, version, limits["search_and_shrink_limit"])
    started = time.perf_counter_ns()
    search_status, returned = "COMPLETED", None
    try:
        returned = run_boundary(ledger) if method == "boundary_grid" else run_hypothesis(ledger, seed)
    except BudgetStop:
        search_status = "BUDGET_EXHAUSTED"
    except core.HarnessError:
        search_status = "HARNESS_ERROR"
    except Exception as error:
        search_status = "HARNESS_ERROR"
        ledger.events.append({"stage": "framework", "status": "HARNESS_ERROR", "error_type": type(error).__name__})
    wall_ns = time.perf_counter_ns() - started
    witness = returned if returned is not None else min(ledger.failures, key=rank) if ledger.failures else None
    replays = []
    if witness is not None and search_status != "HARNESS_ERROR":
        for replay_version in core.VERSIONS:
            for iteration in range(2):
                t = time.perf_counter_ns()
                try:
                    checked = core.check(case_id, replay_version, witness)
                except core.HarnessError as error:
                    checked = {"status": "HARNESS_ERROR", "error": str(error)}
                    search_status = "HARNESS_ERROR"
                replays.append({"version": replay_version, "iteration": iteration, "input": list(witness),
                                **checked, "check_wall_ns": time.perf_counter_ns() - t})
                if search_status == "HARNESS_ERROR":
                    break
            if search_status == "HARNESS_ERROR":
                break
    confirmed = (search_status != "HARNESS_ERROR" and version == "before" and len(replays) == 4
                 and all(r["status"] != "PASS" for r in replays[:2]) and all(r["status"] == "PASS" for r in replays[2:]))
    outcome = ("HARNESS_ERROR" if search_status == "HARNESS_ERROR" else "REGRESSION_CONFIRMED" if confirmed
               else "COUNTEREXAMPLE_NOT_FIXED" if witness is not None else "NO_COUNTEREXAMPLE_WITHIN_BUDGET")
    first_failure = next((index + 1 for index, e in enumerate(ledger.events) if e["status"] in ("VALUE_MISMATCH", "SUT_EXCEPTION")), None)
    executed = sum(e.get("stage") != "framework" for e in ledger.events)
    assert executed + len(replays) <= limits["sut_call_limit"]
    return {"case_id": case_id, "version": version, "method": method, "seed": seed, "repetition": repetition,
            "outcome": outcome, "search_status": search_status, "minimization_completed": returned is not None,
            "first_failure_execution": first_failure, "search_shrink_calls": executed, "replay_calls": len(replays),
            "total_calls": executed + len(replays), "method_wall_ns": wall_ns,
            "witness": list(witness) if witness is not None else None,
            "witness_size": core.size(witness) if witness is not None else None,
            "events": ledger.events, "replays": replays}


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k,v in value.items() if k not in ("check_wall_ns", "method_wall_ns", "repetition")}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def summary(runs):
    rows = []
    for case in core.CASE_IDS:
        for version in core.VERSIONS:
            for method in ("boundary_grid", "hypothesis"):
                entries = [r for r in runs if (r["case_id"],r["version"],r["method"]) == (case,version,method)]
                rows.append({"case_id":case,"version":version,"method":method,"runs":len(entries),
                             "outcomes":dict(Counter(r["outcome"] for r in entries)),
                             "total_calls_range":[min(r["total_calls"] for r in entries),max(r["total_calls"] for r in entries)],
                             "first_failure_range": ([min(r["first_failure_execution"] for r in entries),max(r["first_failure_execution"] for r in entries)]
                                                     if all(r["first_failure_execution"] is not None for r in entries) else None),
                             "method_wall_ms_range":[min(r["method_wall_ns"] for r in entries)/1e6,max(r["method_wall_ns"] for r in entries)/1e6]})
    return rows


def write_regression_tests(runs, directory):
    witnesses = {}
    for case in core.CASE_IDS:
        candidates = [r for r in runs if r["case_id"] == case and r["outcome"] == "REGRESSION_CONFIRMED"]
        selected = next((r for r in candidates if r["method"] == "hypothesis" and r["seed"] == 0 and r["repetition"] == 0), candidates[0] if candidates else None)
        if selected is None:
            continue
        witnesses[case] = {"input": selected["witness"], "source_method": selected["method"], "seed": selected["seed"]}
    (directory/"regressions.json").write_text(app.json_text(witnesses),encoding="utf-8")
    # Фиксированный шаблон теста; только JSON-числа взяты из найденного примера.
    source = '''"""Регрессионные тесты исторического пилота. Версия выбирается только из before/after."""
import argparse
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from historical_core import assert_contract, verify_sources

class Regression(unittest.TestCase):
    pass

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',required=True,choices=('before','after'))
    args=parser.parse_args()
    verify_sources()
    data=json.loads(Path(__file__).with_name('regressions.json').read_text())
    for case_id, record in data.items():
        def test(self, case_id=case_id, value=record['input']):
            assert_contract(case_id, args.version, value)
        setattr(Regression,'test_'+case_id,test)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Regression))
    print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
'''
    (directory/"test_regressions.py").write_text(source,encoding="utf-8")
    evidence = {}
    for version in core.VERSIONS:
        command=[sys.executable,'-I','-B',str(directory/'test_regressions.py'),'--version',version]
        process=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=20)
        (directory/f"test-{version}.txt").write_text(process.stdout+process.stderr,encoding="utf-8")
        try:
            counts=json.loads(process.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError):
            counts=None
        evidence[version]={"command":command,"exit_code":process.returncode,"counts":counts}
    evidence['confirmed']=(len(witnesses)==2 and evidence['before']['counts']=={'tests':2,'failures':2,'errors':0}
                           and evidence['after']['counts']=={'tests':2,'failures':0,'errors':0})
    evidence['extra_sut_calls']=4 if evidence['confirmed'] else None
    return evidence


@loc.localized_html
def render(report):
    rows=""
    for r in report['summary']:
        rows += ('<tr><td>'+html.escape(LABELS[r['case_id']])+'</td><td>'+r['version']+'</td><td>'+r['method']+'</td><td>'+str(r['runs'])
                 +'</td><td>'+html.escape(str(r['outcomes']))+'</td><td>'+str(r['first_failure_range'])+'</td><td>'+str(r['total_calls_range'])
                 +'</td><td>'+str([round(t,2) for t in r['method_wall_ms_range']])+'</td></tr>')
    origins="".join('<li>'+html.escape(LABELS[k])+': <a href="'+v['pull_request']+'">официальное исправление</a>; до '+v['before_commit']+'; после '+v['after_commit']+'</li>' for k,v in report['provenance']['cases'].items())
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>ReproCase — исторический пилот</title><style>body{font:17px/1.5 system-ui;max-width:1250px;margin:30px auto;padding:15px}"
            "table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:7px}pre{white-space:pre-wrap;overflow-wrap:anywhere}aside{background:#fff0cf;padding:14px}</style>"
            "<h1>Два исторических дефекта: обычный поиск и Hypothesis</h1><aside>Это реальные опубликованные исправления, "
            "воспроизведённые на точных Python-исходниках. Это не новые находки и не независимый скрытый тест. Модель не запускалась.</aside>"
            "<ul>"+origins+"</ul><p>Исходники до/после не редактировались. CPython проверяется как исторический модуль на нынешнем Python, "
            "а не как полный старый интерпретатор. Для корреляции нами вручную подготовлено свойство пропорциональных данных; "
            "для числового диапазона — сравнение с встроенным range на ограниченных целых числах.</p>"
            "<p>Единый предел на запуск: 252 исполнения для поиска и сокращения + 4 для повторов до/после, всего до 256. "
            "Бюджет проверяется перед исполнением. Повторы Hypothesis учитываются; max_examples не выдаётся за полный счётчик. "
            "После первой находки вызовы Hypothesis помечены вместе как сокращение/перепроверка: точную внутреннюю фазу публичный API не сообщает.</p>"
            "<table><tr><th>Случай</th><th>Версия</th><th>Метод</th><th>Запусков</th><th>Исходы</th><th>Первая находка, вызов</th><th>Всего вызовов</th><th>Время метода, мс</th></tr>"+rows+"</table>"
            "<p>Обычный метод: ноль, единицы, границы и полный конечный порядок, затем жадное сокращение. Hypothesis: стандартные "
            "целочисленные стратегии в тех же областях, seeds 0–4, без базы готовых примеров и @example. Два повторения каждого запуска. "
            "Исторический контрпример и patch не передавались ни одному методу. Области и свойства подготовлены вручную после изучения исправлений.</p>"
            f"<p>Повторяемость решений без времени: {report['repeatable_decisions']}. Регрессионные тесты до/после: {report['regression_tests']['confirmed']}.</p>"
            "<p>Отсутствие находки в исправленной версии не доказывает отсутствие других ошибок. Бюджет может прервать shrinking; "
            "это отмечено в каждой трассе. Минимальность глобально не доказана. Время включает работу метода и проверяющего; "
            "это не измерение экономии человеческого времени.</p>"
            "<p>Автоматизированы ограниченный поиск, журнал всех попыток, сокращение, повтор до/после, создание теста из фиксированного шаблона "
            "и отчёт. Вручную выбраны дефекты, версии, допустимые входы и независимые свойства. Разбор произвольного сообщения о сбое не автоматизирован.</p>"
            "<p><a href='results.json'>Все попытки, размеры примеров, времена и результаты</a> · <a href='test_regressions.py'>Готовый регрессионный тест</a> · "
            "<a href='regressions.json'>Найденные примеры</a> · <a href='test-before.txt'>Падающая версия</a> · <a href='test-after.txt'>Исправленная версия</a></p></html>\n")


def main(argv=None):
    argv = loc.cli_args(argv)
    parser=loc.ArgumentParser(description="Воспроизвести два исторических дефекта и сравнить обычный метод с Hypothesis, офлайн.")
    parser.parse_args(argv)
    if importlib.util.find_spec('hypothesis') is None:
        if not PYTHON.is_file():
            print('Нет локального Hypothesis. Сначала восстановите изолированное окружение по HISTORICAL_PILOT.md.'); return 2
        return subprocess.run([str(PYTHON),'-B',str(Path(__file__).resolve()),'--language',loc.get_language()],cwd=ROOT).returncode
    os.environ['HYPOTHESIS_STORAGE_DIRECTORY']=str(ROOT/'outputs/hypothesis-cache')
    import hypothesis
    if hypothesis.__version__ != core.plan()['hypothesis_version']:
        print('Версия Hypothesis не совпадает с планом.'); return 2
    provenance=core.verify_sources()
    for case in core.CASE_IDS:
        for version in core.VERSIONS: core.load_version(case,version)
    runs=[]
    for case in core.CASE_IDS:
        for version in core.VERSIONS:
            for method in ('boundary_grid','hypothesis'):
                seeds=[None] if method=='boundary_grid' else core.plan()['hypothesis_seeds']
                for seed in seeds:
                    for repetition in range(core.plan()['repetitions']):
                        runs.append(run_one(case,version,method,seed,repetition))
    pairs={}
    for r in runs:
        pairs.setdefault((r['case_id'],r['version'],r['method'],r['seed']),[]).append(stable(r))
    repeatable=all(len(values)==2 and values[0]==values[1] for values in pairs.values())
    OUTPUT.mkdir(parents=True,exist_ok=True)
    regression=write_regression_tests(runs,OUTPUT)
    source_names=('historical_core.py','historical_pilot.py','historical_plan.json','historical_vendor/manifest.json','historical_vendor/wheels/manifest.json')
    report={'version':'historical-pilot-v1','provenance':provenance,'plan':core.plan(),
            'environment':{'python':sys.version,'platform':platform.platform(),'hypothesis':hypothesis.__version__},
            'model':{'status':'NOT_EVALUATED','metrics':None},'model_api_calls':0,'repeatable_decisions':repeatable,
            'summary':summary(runs),'runs':runs,'regression_tests':regression,
            'comparison_sut_calls':sum(r['total_calls'] for r in runs),
            'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in source_names}}
    (OUTPUT/'results.json').write_text(app.json_text(report),encoding='utf-8')
    (OUTPUT/'report.html').write_text(render(report),encoding='utf-8')
    for row in report['summary']: print(row['case_id'],row['version'],row['method'],row['outcomes'],'вызовов',row['total_calls_range'])
    print('Повторяемость решений:',repeatable,'; тест до/после:',regression['confirmed'])
    print('Реальных модельных API-вызовов: 0.'); print(OUTPUT/'report.html')
    return 0 if (repeatable and regression['confirmed']
                 and not any(r['outcome']=='HARNESS_ERROR' or (r['version']=='after' and r['witness'] is not None) for r in runs)) else 1


if __name__=='__main__':
    raise SystemExit(main())
