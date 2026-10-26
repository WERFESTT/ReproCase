#!/usr/bin/env python3
"""Офлайн-маршрут подготовки свойства; прежние транспорт, поиск и исторические операции."""
import argparse
import copy
import hashlib
import html
import itertools
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

import benchmark_adapter as transport
import historical_core as core
import historical_pilot as search
import nebius_client as client
import property_contract as contract
import reprocase as app

ROOT=core.ROOT
OUT=ROOT/'outputs/property-preparation'
MODEL=client.MODELS[1]
SOURCES=('property_contract.py','property_preparation.py','historical_core.py','historical_pilot.py',
         'historical_plan.json','historical_vendor/manifest.json','property_protocol.json')

class Cancelled(BaseException): pass

def build_request(public):
    public=contract.validate_context(public)
    messages=[{'role':'system','content':'Предложи проверяемое свойство и область входов по симптому и публичному контракту. Верни ровно один propose_check в заданной декларативной схеме. Не добавляй код, пути, сеть или заявление о найденной ошибке. Возможны несколько допустимых свойств; полезность проверит отдельный локальный контур.'},
              {'role':'user','content':json.dumps(public,ensure_ascii=False,sort_keys=True)}]
    return client.build_function_request(messages,contract.schema(),'propose_check',MODEL,
                                         max_completion_tokens=1024,max_request_bytes=16000)

def artificial_response(proposal):
    result=transport.artificial_response(proposal,MODEL)
    result['choices'][0]['message']['tool_calls'][0]['function']['name']='propose_check'
    return result

class SyntheticTransport:
    def __init__(self,responder): self.responder=responder; self.calls=0
    def __call__(self,body):
        self.calls+=1
        public=json.loads(json.loads(body)['messages'][1]['content'])
        answer=self.responder(copy.deepcopy(public))
        return answer if type(answer) in (str,bytes) else json.dumps(answer,ensure_ascii=False).encode()

class OfflineSession(transport.DiagnosticSession):
    function_name='propose_check'
    transport_type=SyntheticTransport
    def __init__(self,responder,cancelled=lambda:False):
        super().__init__(synthetic_transport=SyntheticTransport(responder),allow_network=False,max_requests=1,model=MODEL)
        self.cancelled=cancelled
    def _validate_context(self,value): return contract.validate_context(value)
    def _build_request(self,value): return build_request(value)
    def _validate_action(self,arguments,public): return contract.validate_proposal(arguments,public)
    def _continuity(self,public):
        if self._last_context is not None: raise client.ClientError('Одна попытка предложения на сеанс; повтор запрещён.')
    def _before_send(self):
        if self.cancelled(): raise client.ClientError('Отменено до отправки.')
    def _send(self,body,api_key): raise client.NetworkDisabled('Этот маршрут только искусственный; сеть недоступна.')

def proposed(case,assertion,domain=None):
    """Только ручная тестовая заготовка. Она не является генерацией моделью."""
    public=contract.context(case)
    return {'action':'propose_property','version':contract.VERSION,'context_sha256':public['context_sha256'],
            'domain':copy.deepcopy(public['available_domain'] if domain is None else domain),'assertion':copy.deepcopy(assertion)}

class PropertyLedger(search.Ledger):
    def __init__(self,case,version,proposal,limit=252,cancelled=lambda:False):
        if type(limit) is not int or not 1<=limit<=252: raise contract.PropertyError('Предел поиска от 1 до 252.')
        super().__init__(case,version,limit); self.proposal=proposal; self.cancelled=cancelled
    def attempt(self,value,stage):
        if self.cancelled(): raise Cancelled()
        value=core.validate_input(self.case_id,value)
        if not contract.contains(self.proposal,value): raise core.HarnessError('Проба вне предложенной области.')
        if len(self.events)>=self.limit: raise search.BudgetStop()
        started=time.perf_counter_ns()
        try: result=contract.evaluate(self.case_id,self.version,self.proposal,value)
        except core.HarnessError as error:
            self.events.append({'input':list(value),'stage':stage,'status':'HARNESS_ERROR','error':str(error),'check_wall_ns':time.perf_counter_ns()-started})
            raise
        self.events.append({**result,'stage':stage,'check_wall_ns':time.perf_counter_ns()-started})
        failed=result['status']!='PASS'
        if failed: self.failures.append(value)
        return failed

def boundary(ledger):
    # Тот же порядок границ/нуля/единиц; область ограничена до генерации.
    dimensions=[]
    for index,(lo,hi) in enumerate(ledger.proposal['domain']):
        legal=lambda x: lo<=x<=hi and not (ledger.case_id=='more_reversed' and index==2 and x==0)
        edges=list(dict.fromkeys(x for x in (0,1,-1,lo,hi,lo+1,hi-1) if legal(x)))
        rest=[x for x in range(lo,hi+1) if legal(x) and x not in edges]
        dimensions.append((edges,rest))
    def candidates():
        edges=[a for a,b in dimensions]; seen=set()
        for i in range(max(map(len,edges))):
            value=tuple(axis[i%len(axis)] for axis in edges)
            if value not in seen: seen.add(value); yield value
        for parts in (edges,[a+b for a,b in dimensions]):
            for value in itertools.product(*parts):
                if value not in seen: seen.add(value); yield value
    found=None
    for value in candidates():
        if contract.contains(ledger.proposal,value) and ledger.attempt(value,'generate'):
            found=value; break
    if found is None: return None
    while True:
        for value in search.smaller_inputs(ledger.case_id,found):
            if contract.contains(ledger.proposal,value) and ledger.attempt(value,'shrink'):
                found=value; break
        else: return found

def hypothesis_search(ledger,seed):
    from hypothesis import find,settings,Phase,strategies as st
    from hypothesis.errors import NoSuchExample
    axes=[]
    for index,(lo,hi) in enumerate(ledger.proposal['domain']):
        if ledger.case_id=='more_reversed' and index==2:
            parts=[st.integers(lo,min(-1,hi))] if lo<0 else []
            if hi>0: parts.append(st.integers(max(1,lo),hi))
            axes.append(st.one_of(*parts))
        else: axes.append(st.integers(lo,hi))
    config=settings(max_examples=ledger.limit,database=None,deadline=None,phases=(Phase.generate,Phase.shrink),print_blob=False)
    def predicate(value):
        return ledger.attempt(value,'post_failure_shrink_or_recheck' if ledger.failures else 'generate')
    try: return tuple(find(st.tuples(*axes),predicate,settings=config,random=random.Random(seed)))
    except NoSuchExample: return None

def run_search(case,proposal,method='hypothesis',version='before',seed=0,limit=252,cancelled=lambda:False):
    if method not in ('boundary_grid','hypothesis') or version not in core.VERSIONS or type(seed) is not int or not 0<=seed<=4:
        raise contract.PropertyError('Неизвестный метод, версия или seed.')
    proposal=contract.validate_proposal(proposal,contract.context(case))
    validation=contract.semantic_validation(case,proposal)
    ledger=PropertyLedger(case,version,proposal,limit,cancelled)
    state='COMPLETED'; returned=None
    started=time.perf_counter_ns()
    try:
        if cancelled(): raise Cancelled()
        returned=boundary(ledger) if method=='boundary_grid' else hypothesis_search(ledger,seed)
    except Cancelled: state='CANCELLED'
    except search.BudgetStop: state='BUDGET_EXHAUSTED'
    except core.HarnessError: state='HARNESS_ERROR'
    except Exception: state='HARNESS_ERROR'
    wall=time.perf_counter_ns()-started
    witness=returned if returned is not None else min(ledger.failures,key=search.rank) if ledger.failures else None
    replays=[]
    # Исправленная версия впервые вызывается здесь; обратной связи в селектор/модель нет.
    if witness is not None and state not in ('CANCELLED','HARNESS_ERROR'):
        for replay_version in core.VERSIONS:
            for iteration in range(2):
                if cancelled(): state='CANCELLED'; break
                try: result=contract.evaluate(case,replay_version,proposal,witness)
                except core.HarnessError: result={'status':'HARNESS_ERROR'}; state='HARNESS_ERROR'
                replays.append({'version':replay_version,'iteration':iteration,**result})
                if state=='HARNESS_ERROR': break
            if state in ('CANCELLED','HARNESS_ERROR'): break
    confirmed=(state not in ('CANCELLED','HARNESS_ERROR') and version=='before' and len(replays)==4
               and all(r['status']=='PROPERTY_VIOLATION' for r in replays[:2]) and all(r['status']=='PASS' for r in replays[2:]))
    outcome=(state if state in ('CANCELLED','HARNESS_ERROR') else 'REGRESSION_CONFIRMED' if confirmed
             else 'UNCONFIRMED_COUNTEREXAMPLE' if witness is not None else 'NO_COUNTEREXAMPLE_WITHIN_BUDGET')
    return {'case_id':case,'target_version':version,'method':method,'seed':seed,'proposal':proposal,
      'semantic_validation':validation,'outcome':outcome,'search_status':state,'minimization_completed':returned is not None,
      'events':ledger.events,'replays':replays,'witness':list(witness) if witness is not None else None,
      'search_shrink_calls':len(ledger.events),'confirmation_calls':len(replays),'total_calls':len(ledger.events)+len(replays),'method_wall_ns':wall}

def prepare(case,session,method='hypothesis',version='before',seed=0,limit=252,cancelled=lambda:False):
    public=contract.context(case)
    record={'origin':'SYNTHETIC','context':public,'status':'NOT_STARTED','proposal':None,'search':None}
    try:
        if cancelled(): record['status']='CANCELLED'; return record
        proposal=session.request_action(public)
        record['proposal']=proposal
        record['search']=run_search(case,proposal,method,version,seed,limit,cancelled)
        record['semantic_validation']=record['search']['semantic_validation']
        record['status']=record['search']['outcome']
    except contract.FalseProperty as error:
        record.update(status='FALSE_PROPERTY_REJECTED',reason=str(error),semantic_validation=error.validation)
    except client.ClientError as error: record.update(status='CANCELLED' if cancelled() else 'REJECTED',reason=str(error))
    finally:
        record['accounting']=session.accounting()
        record['transport_events']=[{k:v for k,v in event.items() if k!='response_text'} for event in session.events]
        record['response_sha256']=[hashlib.sha256(e['response_text'].encode()).hexdigest() for e in session.events if 'response_text' in e]
    return record

def fingerprints():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}

def artifact(record):
    if record['origin']!='SYNTHETIC' or record['status']!='REGRESSION_CONFIRMED': raise contract.PropertyError('Нет подтверждённого свидетельства.')
    run=record['search']
    return {'format':'prepared-property-test-v1','origin':record['origin'],'case_id':run['case_id'],
      'context':record['context'],'proposal':record['proposal'],'input':run['witness'],
      'source_method':run['method'],'seed':run['seed'],'request_sha256':contract.digest(build_request(record['context'])),
      'source_sha256':fingerprints()}

def validate_artifact(data):
    contract.exact(data,('format','origin','case_id','context','proposal','input','source_method','seed','request_sha256','source_sha256'))
    if data['format']!='prepared-property-test-v1' or data['origin']!='SYNTHETIC' or data['source_sha256']!=fingerprints():
        raise contract.PropertyError('Неверное происхождение, формат или изменённая версия кода.')
    case=data['case_id']
    if case not in core.CASE_IDS or data['source_method'] not in ('boundary_grid','hypothesis') or type(data['seed']) is not int or not 0<=data['seed']<=4:
        raise contract.PropertyError('Неверная задача или метод свидетельства.')
    if data['context']!=contract.context(case) or data['request_sha256']!=contract.digest(build_request(data['context'])):
        raise contract.PropertyError('Контекст свидетельства изменён.')
    p=contract.validate_proposal(data['proposal'],data['context']); contract.semantic_validation(case,p)
    value=core.validate_input(case,data['input'])
    if not contract.contains(p,value): raise contract.PropertyError('Свидетельство вне выбранной области.')
    return data

def read_artifact(path):
    # Путь задаёт локальная команда пользователя, не ответ модели; чтение ограничено проектом.
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT) or path.stat().st_size>65536: raise contract.PropertyError('Свидетельство вне проекта или слишком велико.')
    return validate_artifact(core.read_json(path))

def replay_assertion(data,version):
    data=validate_artifact(data)
    result=contract.evaluate(data['case_id'],version,data['proposal'],data['input'])
    assert result['status']=='PASS', app.json_text(result)
    return result

def export_test(record,directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to(OUT) or directory.parent!=OUT: raise contract.PropertyError('Нужна локальная папка результата внутри outputs/property-preparation.')
    directory.mkdir(parents=True,exist_ok=True)
    data=artifact(record)
    (directory/'witness.json').write_text(app.json_text(data))
    source='''"""Фиксированный шаблон. Предложение и свидетельство — только проверяемые JSON-данные."""
import argparse
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from property_preparation import read_artifact,replay_assertion
parser=argparse.ArgumentParser()
parser.add_argument('--version',required=True,choices=('before','after'))
args=parser.parse_args()
class Regression(unittest.TestCase):
    def test_prepared_property(self):
        replay_assertion(read_artifact(Path(__file__).with_name('witness.json')),args.version)
result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Regression))
import json
print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
    (directory/'test_prepared.py').write_text(source)
    evidence={}
    for version in core.VERSIONS:
        command=[sys.executable,'-B',str(directory/'test_prepared.py'),'--version',version]
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=20)
        (directory/('test-'+version+'.txt')).write_text(run.stdout+run.stderr)
        try: counts=json.loads(run.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError): counts=None
        evidence[version]={'command':command,'exit_code':run.returncode,'counts':counts}
    evidence['confirmed']=(evidence['before']['counts']=={'tests':1,'failures':1,'errors':0} and evidence['after']['counts']=={'tests':1,'failures':0,'errors':0})
    evidence['extra_sut_calls']=2 if evidence['confirmed'] else None
    return evidence

def demo_scenarios():
    """Полностью ручные искусственные ответы, включая отрицательные; не метрика модели."""
    entries=[]
    for case,assertion in [('cpython_correlation',{'op':'equal_number','value':1}),
                            ('more_reversed',{'op':'sequence_equal','order':'reverse'})]:
        p=proposed(case,assertion)
        for method in ('boundary_grid','hypothesis'):
            for version in core.VERSIONS:
                entries.append((case+'-'+method+'-'+version,case,p,method,version,252,'REGRESSION_CONFIRMED' if version=='before' else 'NO_COUNTEREXAMPLE_WITHIN_BUDGET'))
    entries.extend([
      ('valid_narrow_no_find','cpython_correlation',proposed('cpython_correlation',{'op':'finite'},[[-2,2],[-2,2]]),'hypothesis','before',252,'NO_COUNTEREXAMPLE_WITHIN_BUDGET'),
      ('valid_weak_no_find','more_reversed',proposed('more_reversed',{'op':'completes'},[[0,0],[1,3],[1,2]]),'boundary_grid','before',252,'NO_COUNTEREXAMPLE_WITHIN_BUDGET'),
      ('budget_exhausted','cpython_correlation',proposed('cpython_correlation',{'op':'finite'}),'boundary_grid','before',1,'NO_COUNTEREXAMPLE_WITHIN_BUDGET'),
      ('false_correlation','cpython_correlation',proposed('cpython_correlation',{'op':'equal_number','value':0}),'hypothesis','before',252,'FALSE_PROPERTY_REJECTED'),
      ('false_order','more_reversed',proposed('more_reversed',{'op':'sequence_equal','order':'forward'}),'hypothesis','before',252,'FALSE_PROPERTY_REJECTED'),
      ('false_length','more_reversed',proposed('more_reversed',{'op':'length_between','min':1,'max':32}),'hypothesis','before',252,'FALSE_PROPERTY_REJECTED')])
    for name,field,value in [('extra_code','python','raise SystemExit()'),('extra_path','path','/outside-project'),('extra_network','url','https://invalid.example'),('oversized','padding','x'*5000)]:
        p=proposed('cpython_correlation',{'op':'finite'}); p[field]=value
        entries.append((name,'cpython_correlation',p,'hypothesis','before',252,'REJECTED'))
    p=proposed('cpython_correlation',{'op':'unknown'})
    entries.append(('unknown_operation','cpython_correlation',p,'hypothesis','before',252,'REJECTED'))
    return entries

def render(report):
    rows=[]
    for row in report['scenarios']:
        record=row['record']; run=record.get('search')
        witness='' if run is None else str(run['witness'])
        link='' if not row.get('export') else '<a href="'+row['name']+'/test_prepared.py">тест</a> · <a href="'+row['name']+'/witness.json">свидетельство</a>'
        rows.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in (row['name'],record['status'],run['total_calls'] if run else 0,witness))+'<td>'+link+'</td></tr>')
    return """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReproCase — подготовка свойства</title><style>body{font:17px/1.5 system-ui;max-width:1100px;margin:25px auto;padding:16px}aside{background:#fff0cc;padding:15px}td,th{border:1px solid #aaa;padding:8px;overflow-wrap:anywhere}table{border-collapse:collapse}pre{white-space:pre-wrap;overflow-wrap:anywhere}.scroll{overflow-x:auto}</style>
<h1>От описания сбоя к проверяемому свойству</h1>
<aside><strong>Все ответы в этом отчёте искусственные и подготовлены вручную. Настоящая модель здесь не проверена.</strong> Это проверка программы, не доказательство качества генерации тестов или экономии труда.</aside>
<p>Описание симптома и публичный контракт → декларативное предложение → проверка смысла → обычный поиск / Hypothesis → повтор до и после исправления → тест из фиксированного шаблона.</p>
<p>Словарь свойств, генераторы входов и математические правила приёмки написаны вручную для двух известных задач. Несколько свойств допустимы; узкое или слабое свойство может ничего не найти. Произвольный Python, пути и сетевые операции из ответа не исполняются.</p>
<p>Исправленный код не используется при приёмке свойства или выборе проб. Его роль — окончательное подтверждение и отдельные отрицательные запуски. Поисковые результаты и эталоны не возвращаются модели. Бюджет на поиск с сокращением — до 252 исполнений плюс 4 подтверждения; экспортированный тест имеет отдельный счётчик.</p>
<div class="scroll"><table><tr><th>Искусственный сценарий</th><th>Фактический исход</th><th>Исполнений</th><th>Свидетельство</th><th>Файлы</th></tr>"""+''.join(rows)+"""</table></div>
<p>Отклонённые предложения не запускали исторический код. Отсутствие находки не означает исправность. Ложное свойство отклоняется до поиска; сбой, сохраняющийся после исправления, не получает статус подтверждённой регрессии.</p>
<p><a href="results.json">Полные входы, предложения, попытки, отказы и происхождение</a> · <a href="../../PROPERTY_PREPARATION.md">Методика и команды повторения</a></p>
<p>Прежний исторический пилот и его настоящие исходники сохранены. Это открытые изученные случаи. SHA-256 фиксирует содержимое, но не доказывает авторство. Визуальная проверка этого отчёта не выполнена.</p></html>\n"""

def write_demo():
    OUT.mkdir(parents=True,exist_ok=True)
    scenarios=[]
    for name,case,p,method,version,limit,expected in demo_scenarios():
        session=OfflineSession(lambda public,p=p:artificial_response(p))
        record=prepare(case,session,method,version,0,limit)
        row={'name':name,'expected':expected,'record':record,'matched':record['status']==expected}
        if record['status']=='REGRESSION_CONFIRMED': row['export']=export_test(record,OUT/name)
        scenarios.append(row)
    case='cpython_correlation'; p=proposed(case,{'op':'finite'})
    def timeout(public): raise TimeoutError('искусственный тайм-аут')
    partial=artificial_response(p); partial['choices'][0]['finish_reason']='length'
    multiple=artificial_response(p); multiple['choices'][0]['message']['tool_calls']*=2
    for name,responder in [('transport_failure',timeout),('partial_answer',lambda public:partial),('extra_call',lambda public:multiple)]:
        record=prepare(case,OfflineSession(responder))
        scenarios.append({'name':name,'expected':'REJECTED','record':record,'matched':record['status']=='REJECTED'})
    for name,cancel_at in [('cancel_before_request',1),('cancel_after_response',2)]:
        calls=[0]
        def cancelled(): calls[0]+=1; return calls[0]>=cancel_at
        record=prepare(case,OfflineSession(lambda public:artificial_response(p)),cancelled=cancelled)
        scenarios.append({'name':name,'expected':'CANCELLED','record':record,'matched':record['status']=='CANCELLED'})
    summary={'scheduled_synthetic_scenarios':len(scenarios),'matched_expected_scenarios':sum(s['matched'] for s in scenarios),
      'confirmed_exported_tests':sum(bool(s.get('export',{}).get('confirmed')) for s in scenarios),
      'sut_calls':sum(s['record']['search']['total_calls'] for s in scenarios if s['record'].get('search')),
      'export_test_extra_sut_calls':sum(s.get('export',{}).get('extra_sut_calls',0) for s in scenarios)}
    report={'origin':'SYNTHETIC','model':{'status':'NOT_EVALUATED','metrics':None},'model_api_calls':0,
      'protocol':core.read_json(ROOT/'property_protocol.json'),'summary':summary,'scenarios':scenarios,'source_sha256':fingerprints(),
      'manual_work':['описания симптомов','контракты','грамматика генераторов и свойств','правила семантической приёмки','все искусственные ответы'],
      'human_time_savings':None}
    (OUT/'results.json').write_text(app.json_text(report))
    (OUT/'report.html').write_text(render(report))
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description='Офлайн-подготовка свойства по искусственным ответам. Сетевого режима нет.')
    parser.add_argument('--synthetic',action='store_true',required=True)
    parser.parse_args(argv)
    try: import hypothesis
    except ImportError:
        if not search.PYTHON.is_file(): print('Нет локального Hypothesis; см. HISTORICAL_PILOT.md.'); return 2
        return subprocess.run([str(search.PYTHON),'-B',str(Path(__file__).resolve()),'--synthetic'],cwd=ROOT).returncode
    if hypothesis.__version__!=core.plan()['hypothesis_version']: print('Версия Hypothesis не соответствует плану.'); return 2
    os.environ['HYPOTHESIS_STORAGE_DIRECTORY']=str(ROOT/'outputs/hypothesis-cache')
    result=write_demo()
    print('ИСКУССТВЕННЫЕ ответы:',app.json_text(result['summary']))
    print('Модель не проверена; реальных API-вызовов — 0.'); print(OUT/'report.html')
    return 0 if result['summary']['matched_expected_scenarios']==len(result['scenarios']) and result['summary']['confirmed_exported_tests']==4 else 1

if __name__=='__main__': raise SystemExit(main())
