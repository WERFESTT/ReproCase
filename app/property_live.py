"""Подтверждаемый запуск подготовки свойства. Сеть только после явного согласия в окне."""
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading

import benchmark_adapter as transport
import historical_core as core
import live_search as live
import nebius_client as client
import property_contract as contract
import property_preparation as prep
import reprocase as app

OPTIONS=('Корреляция — один запрос','Обратный диапазон — один запрос','Обе задачи — серия из шести запросов')
STATES={'NOT_STARTED':'не запускалось','RUNNING':'выполняется','COMPLETED':'завершено','CANCELLED':'отменено',
        'REJECTED':'ответ отклонён','FALSE_PROPERTY':'ложное свойство','ERROR':'ошибка','UNCONFIRMED':'расхождение не подтверждено',
        'NO_COUNTEREXAMPLE':'свойство принято; пример за лимит не найден',
        'INVALID_MODEL_RESPONSE':'недопустимый ответ модели','TRANSPORT_ERROR':'ошибка транспорта',
        'LOCAL_EXPORT_ERROR':'локальная ошибка экспорта или повтора','LOCAL_CHECK_ERROR':'локальная ошибка проверки',
        'STALE_RUNTIME':'код обновлён; нужен перезапуск окна'}


class StaleRuntime(client.ClientError):
    pass


def make_plan(choice=0):
    if type(choice) is not int or choice not in (0,1,2): raise client.ClientError('Выберите один из трёх режимов.')
    protocol=core.read_json(core.ROOT/'property_protocol.json')
    fixed={'independent_proposal_repetitions':[0,1,2], 'total_requests_upper_bound':6,
           'proposal_requests_per_session':1, 'max_completion_tokens_per_request':1024,
           'max_serialized_request_bytes':16000, 'automatic_retries':0}
    if any(protocol.get(k)!=v or type(protocol.get(k)) is not type(v) for k,v in fixed.items()):
        raise client.ClientError('Закреплённые лимиты или повторы плана изменены; запуск запрещён.')
    rows=([{'case_id':case,'seed':seed} for case in core.CASE_IDS for seed in protocol['independent_proposal_repetitions']]
          if choice==2 else [{'case_id':core.CASE_IDS[choice],'seed':0}])
    return {'kind':'PROPERTY_PREPARATION','choice':choice,'rows':rows,'requests':len(rows),
            'max_completion_tokens':1024,'max_request_bytes':16000,
            'protocol_sha256':hashlib.sha256((core.ROOT/'property_protocol.json').read_bytes()).hexdigest()}


def validate_plan(plan):
    if type(plan) is not dict or plan!=make_plan(plan.get('choice')) or set(plan)!=set(make_plan(plan['choice'])):
        raise client.ClientError('План или ограничения изменены.')
    return copy.deepcopy(plan)


def preview(plan):
    plan=validate_plan(plan)
    cases=list(dict.fromkeys(row['case_id'] for row in plan['rows']))
    return ('ПРЕДПРОСМОТР. НИЧЕГО НЕ ОТПРАВЛЕНО.\nЧисла прежней учебной формы здесь не используются.\n\n'+consent_text(plan)+
            '\n\nПолные тела запросов, без ключа (для повторений тело одинаково):\n'+
            '\n\n'.join(app.json_text(prep.build_request(contract.context(case))) for case in cases))


def consent_text(plan):
    plan=validate_plan(plan); count=plan['requests']
    cases=list(dict.fromkeys(row['case_id'] for row in plan['rows']))
    descriptions='\n\n'.join(prep.search.LABELS[case]+': '+contract.context(case)['symptom'] for case in cases)
    return (f'Подготовка проверяемого теста: разрешить {count} '+('запрос?' if count==1 else 'независимых запросов?')+
      f'\nМодель: {live.MODEL}\nАдрес: https://{live.HOST}{client.PATH}\n\n'+descriptions+
      '\n\nПоставщику отправятся публичный симптом, контракт API, описание генератора, области входов и схема свойства. '
      'Исправления, исходники, эталоны, найденные примеры и результаты локального поиска не отправляются. '
      'Полные данные доступны кнопкой предпросмотра.\n\n'+
      f'Каждый сеанс — ровно одна попытка, до 1024 токенов ответа (включая reasoning), 16000 байт тела, тайм-аут {client.TIMEOUT_SECONDS} с. '
      f'Итого до {count} запросов, {count*1024} токенов ответа и {count*16000} байт запросов. '
      'Повторов и автоматического исправления ответа нет. Первая ошибка или отмена останавливает остаток серии.\n\n'
      'Это платная отправка: ставки с карточки $0.30 / $0.06 / $0.90 за миллион входных / кешированных / выходных токенов. '
      'Байты не равны токенам; ограничения не гарантируют денежный потолок. Неизвестное usage не считается нулём. '
      'Отмена и тайм-аут не доказывают отмену запроса или списания у поставщика.\n\n'
      'Ключ вводится скрыто, только в память; в отчёт не попадёт. Затем локальные методы проверят свойство и тест до/после. '
      'Приёмка, генераторы и словарь свойств подготовлены вручную; польза модели ещё не доказана. '
      'Согласие относится только к этому запуску.')


class Budget:
    def __init__(self,limit):
        if type(limit) is not int or limit not in (1,6): raise client.ClientError('Допустимы только один или шесть запросов.')
        self.limit=limit; self.used=0; self._lock=threading.Lock()
    def reserve(self):
        with self._lock:
            if self.used>=self.limit: raise client.ClientError('Общий предел запросов исчерпан.')
            self.used+=1


class PropertySession(live.LiveSession):
    function_name='propose_check'
    def __init__(self,cancelled,budget):
        transport.DiagnosticSession.__init__(self,allow_network=True,max_requests=1,model=live.MODEL)
        self.cancelled=cancelled; self.budget=budget; self.response_received=False
    def _validate_context(self,context): return contract.validate_context(context)
    def _build_request(self,context): return prep.build_request(context)
    def _continuity(self,context):
        if self.attempts or self._last_context is not None: raise client.ClientError('Одна попытка на сеанс; повтор запрещён.')
    def _before_send(self):
        ensure_runtime_current()
        super()._before_send(); self.budget.reserve()
    def _send(self,body,key):
        raw=super()._send(body,key)
        self.response_received=True
        return raw
    def _validate_action(self,arguments,context):
        if self.cancelled.is_set(): raise client.ClientError('Ответ получен после отмены; расчёт не запускался.')
        return contract.validate_proposal(arguments,context)


def source_hashes():
    return {**prep.fingerprints(),'property_live.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'portable_export.py':hashlib.sha256((core.ROOT/'portable_export.py').read_bytes()).hexdigest()}


LOADED_SOURCE_HASHES=source_hashes()


def ensure_runtime_current():
    if source_hashes()!=LOADED_SOURCE_HASHES:
        raise StaleRuntime('Перезапустите ReproCase: файлы проверки изменены после загрузки окна. Дальнейшие запросы запрещены.')


def make_artifact(row,run):
    ensure_runtime_current()
    if run['outcome']!='REGRESSION_CONFIRMED': raise client.ClientError('Нет подтверждённого теста.')
    data={'format':'live-prepared-property-v1','origin':row['origin'],'case_id':row['case_id'],
      'context':contract.context(row['case_id']),'proposal':row['proposal'],'input':run['witness'],
      'method':run['method'],'seed':row['seed'],'source_sha256':source_hashes()}
    data['content_sha256']=contract.digest(data)
    return data


def read_artifact(path):
    path=Path(path).resolve()
    if not path.is_relative_to(core.ROOT) or path.stat().st_size>65536: raise client.ClientError('Неверный путь или размер свидетельства.')
    data=core.read_json(path)
    contract.exact(data,('format','origin','case_id','context','proposal','input','method','seed','source_sha256','content_sha256'))
    payload={k:v for k,v in data.items() if k!='content_sha256'}
    if data['content_sha256']!=contract.digest(payload) or data['source_sha256']!=source_hashes():
        raise client.ClientError('Свидетельство или версия кода изменены.')
    if data['format']!='live-prepared-property-v1' or data['origin'] not in ('REAL_API','SYNTHETIC','SAVED_REAL_API_REPLAY'):
        raise client.ClientError('Неизвестное происхождение.')
    if data['case_id'] not in core.CASE_IDS or data['context']!=contract.context(data['case_id']): raise client.ClientError('Неверный контекст.')
    if data['method'] not in ('boundary_grid','hypothesis') or type(data['seed']) is not int or data['seed'] not in (0,1,2):
        raise client.ClientError('Неверный метод или seed.')
    proposal=contract.validate_proposal(data['proposal'],data['context']);contract.semantic_validation(data['case_id'],proposal)
    value=core.validate_input(data['case_id'],data['input'])
    if not contract.contains(proposal,value): raise client.ClientError('Ввод вне области.')
    return data


def replay(path,version):
    data=read_artifact(path)
    checked=contract.evaluate(data['case_id'],version,data['proposal'],data['input'])
    assert checked['status']=='PASS',app.json_text(checked)
    return checked


def export_test(row,run,directory):
    ensure_runtime_current()
    from portable_export import write_bundle
    directory=live.search.local_path(directory)
    test=write_bundle(core.ROOT,directory,app.json_text(make_artifact(row,run)))
    evidence={'process_attempts':0}
    for version in core.VERSIONS:
        command=[sys.executable,'-I','-S','-B',str(test),'--version',version]
        evidence['process_attempts']+=1
        try:
            result=subprocess.run(command,cwd=core.ROOT,capture_output=True,text=True,timeout=20)
            (directory/('test-'+version+'.txt')).write_text(result.stdout+result.stderr)
            try: counts=json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError,IndexError): counts=None
            evidence[version]={'exit_code':result.returncode,'counts':counts,'command':command}
        except (OSError,subprocess.TimeoutExpired) as error:
            reason='Не удалось запустить или завершить локальный тест: '+type(error).__name__
            (directory/('test-'+version+'.txt')).write_text(reason+'\n')
            evidence[version]={'exit_code':None,'counts':None,'command':command,'error':reason}
    evidence['confirmed']=(evidence['before']['exit_code']==1 and evidence['after']['exit_code']==0 and
        evidence['before']['counts']=={'tests':1,'failures':1,'errors':0} and evidence['after']['counts']=={'tests':1,'failures':0,'errors':0})
    evidence['extra_sut_calls']=2 if evidence['confirmed'] else None
    evidence['commands']=['python3 -I -S -B '+shlex.quote(str(test))+' --version '+v for v in core.VERSIONS]
    return evidence


def refresh(record):
    events=[copy.deepcopy(e) for row in record['rows'] for e in row.get('events',[])]
    attempted=[e for e in events if e.get('network_attempted')]
    tokens={}
    for k in ('prompt_tokens','completion_tokens','total_tokens'):
        values=[(e.get('usage') or {}).get(k) for e in attempted]
        tokens[k]=sum(values) if values and all(x is not None for x in values) else None
    origins={r.get('origin') for r in record['rows'] if r.get('origin')}
    record['origin']='SYNTHETIC' if origins=={'SYNTHETIC'} else 'REAL_API' if attempted else 'LOCAL_NO_REQUEST'
    record['events']=events;record['accounting']={'network_attempts':len(attempted),'tokens':tokens}
    record['cost']=live.estimate_cost(events)
    rows=record['rows'];n=len(rows)
    exports=[e for r in rows for e in r.get('exports',[])]
    export_calls=[e.get('extra_sut_calls') for e in exports]
    metrics={'scheduled':n,'schema_accepted':sum(r.get('proposal') is not None for r in rows),
      'contract_accepted':sum(bool(r.get('semantic_validation',{}).get('accepted')) for r in rows),
      'useful_exported_tests_sessions':sum(any(e['confirmed'] for e in r.get('exports',[])) for r in rows),
      'not_started':sum(r['state']=='NOT_STARTED' for r in rows),
      'fixed_counterexamples':sum(run['witness'] is not None for r in rows for run in r.get('searches',[]) if run['target_version']=='after'),
      'model_property_sut_calls':sum(run['total_calls'] for r in rows for run in r.get('searches',[])),
      'manual_baseline_sut_calls':sum(run['total_calls'] for r in rows for run in r.get('manual_baseline',[])),
      'export_test_process_attempts':sum(e.get('process_attempts',sum(v in e for v in core.VERSIONS)) for e in exports),
      'export_test_extra_sut_calls':sum(export_calls) if all(x is not None for x in export_calls) else None}
    record['metrics']=metrics
    lines=['Подготовка свойства: '+STATES.get(record['state'],record['state'])+'.',
      'Источник: '+record['origin']+'.',
      f"Попыток запросов: {len(attempted)} из {record['plan']['requests']}; по одному на независимый сеанс.",
      f"Принято по структуре: {metrics['schema_accepted']}/{n}; по контракту: {metrics['contract_accepted']}/{n}; подтверждённый экспорт: {metrics['useful_exported_tests_sessions']}/{n}.",
      f"Незапущенных позиций: {metrics['not_started']}; расхождений на исправленной версии: {metrics['fixed_counterexamples']}.",
      f"Исполнения для свойства модели: {metrics['model_property_sut_calls']}; ручного контроля: {metrics['manual_baseline_sut_calls']}.\n"
      f"Файлов тестов: {len(exports)}; попыток запуска этих файлов до/после: {metrics['export_test_process_attempts']}.\n"
      f"Дополнительные исполнения расчёта: {metrics['export_test_extra_sut_calls'] if metrics['export_test_extra_sut_calls'] is not None else 'неизвестно (экспорт или повтор не подтверждён)' }.",
      'Токены: вход '+str(tokens['prompt_tokens'])+', выход '+str(tokens['completion_tokens'])+'. None означает неизвестно.']
    cost=record['cost']['estimate_usd_range']
    lines.append(f'Оценка ${cost[0]:.8f}–${cost[1]:.8f}.' if cost else 'Полная стоимость неизвестна; нулевое списание не утверждается.')
    for row in rows:
        lines.append(prep.search.LABELS[row['case_id']]+f", повтор {row['seed']+1}: "+STATES[row['state']]+'. '+row.get('reason',''))
        if row.get('proposal'): lines.append('Предложение:\n'+app.json_text(row['proposal']))
        for run in row.get('searches',[]): lines.append(f"{run['method']} / {run['target_version']}: {run['outcome']}; исполнений {run['total_calls']}; пример {run['witness']}.")
        for item in row.get('exports',[]): lines.extend(item['commands'])
    lines+=['Отказы и незапущенные позиции остаются в знаменателе. Отсутствие находки не доказывает исправность.',
      'Словарь свойств и приёмка подготовлены вручную; это не доказательство автономности или экономии труда.',
      'Оценка не является денежным лимитом. Отмена и тайм-аут не доказывают отмену или отсутствие списания у поставщика.']
    if record['origin']=='SYNTHETIC': lines.insert(0,'ИСКУССТВЕННЫЙ транспорт для проверки программы; это не результат настоящей модели.')
    record['summary_ru']='\n\n'.join(lines)


def save_report(record,directory):
    directory=live.search.local_path(directory);directory.mkdir(parents=True,exist_ok=True)
    refresh(record)
    (directory/'result.json').write_text(app.json_text(record))
    body="<!doctype html><html lang='ru'><meta charset='utf-8'><title>ReproCase — подготовка теста</title><style>body{font:17px/1.5 system-ui;max-width:1100px;margin:24px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Подготовка свойства и регрессионного теста</h1><p>Исторические открытые случаи; ручная грамматика. Результат модели отделён от локальной проверки.</p><pre>"+html.escape(record['summary_ru'])+"</pre><a href='result.json'>Полные результаты и попытки</a></html>\n"
    (directory/'report.html').write_text(body)
    return directory/'report.html'


class PropertyJob(live.LiveJob):
    directory_prefix='property-live-'
    def __init__(self,output_dir=None,session_factory=PropertySession):
        super().__init__(output_dir,session_factory);self.progress_text='Подготовка запуска.'
    def _validate_payload(self,plan):
        ensure_runtime_current()
        os.environ['HYPOTHESIS_STORAGE_DIRECTORY']=str(core.ROOT/'outputs/hypothesis-cache')
        import hypothesis
        if hypothesis.__version__!=core.plan()['hypothesis_version']: raise client.ClientError('Версия локального Hypothesis не соответствует плану.')
        core.verify_sources()
        return validate_plan(plan)
    def start(self,plan,key,*,confirmed=False,approved_requests=None):
        plan=validate_plan(plan)
        if type(approved_requests) is not int or approved_requests!=plan['requests']:
            raise client.ClientError('Нужно подтвердить точное общее число запросов.')
        return super().start(plan,key,confirmed=confirmed)
    def _work(self,plan,key,directory):
        budget=Budget(plan['requests']);session=None;save_error=None
        rows=[dict(r,state='NOT_STARTED',proposal=None,searches=[],manual_baseline=[],exports=[],events=[]) for r in plan['rows']]
        record={'kind':'PROPERTY_PREPARATION','state':'RUNNING','plan':plan,'rows':rows,'model':live.MODEL,
                'endpoint':live.HOST,'cancel_requested':False,'source_sha256':copy.deepcopy(LOADED_SOURCE_HASHES)}
        try:
            save_report(record,directory)
            for index,row in enumerate(rows):
                if self.cancelled.is_set(): record['state']='CANCELLED';break
                validate_plan(plan)
                ensure_runtime_current()
                row['state']='RUNNING';self.progress_text=f"Подготовка свойства {index+1}/{len(rows)}. Попыток начато: {budget.used}."
                session=self.session_factory(self.cancelled,budget);row['origin']=session.origin
                stage='request'
                try:
                    row['proposal']=session.request_action(contract.context(row['case_id']),key)
                    validate_plan(plan)
                    ensure_runtime_current()
                    if self.cancelled.is_set(): row['state']='CANCELLED'
                    else:
                        stage='validation'
                        row['semantic_validation']=contract.semantic_validation(row['case_id'],row['proposal'])
                        manual=core.read_json(core.ROOT/'property_protocol.json')['manual_baseline'][row['case_id']]
                        manual=prep.proposed(row['case_id'],manual['assertion'],manual['domain'])
                        stage='search'
                        for target,proposal in (('searches',row['proposal']),('manual_baseline',manual)):
                            for method in ('boundary_grid','hypothesis'):
                                for version in core.VERSIONS:
                                    run=prep.run_search(row['case_id'],proposal,method,version,row['seed'],cancelled=self.cancelled.is_set)
                                    row[target].append(run)
                                    if run['outcome'] in ('CANCELLED','HARNESS_ERROR'): raise client.ClientError('Локальная проверка остановлена.')
                        stage='export'
                        for run in row['searches']:
                            if self.cancelled.is_set(): raise client.ClientError('Отменено перед экспортом.')
                            ensure_runtime_current()
                            if run['outcome']=='REGRESSION_CONFIRMED':
                                item=export_test(row,run,directory/f"case-{index+1}-{run['method']}")
                                row['exports'].append(item)
                                if not item['confirmed']: raise client.ClientError('Экспортированный тест не подтвердился.')
                        row['state']='COMPLETED'
                        if any(r['outcome']=='UNCONFIRMED_COUNTEREXAMPLE' for r in row['searches']): row['state']='UNCONFIRMED'
                        elif not row['exports']: row['state']='NO_COUNTEREXAMPLE'
                except StaleRuntime as error:
                    row.update(state='STALE_RUNTIME',reason=str(error))
                except contract.FalseProperty as error:
                    row.update(state='FALSE_PROPERTY',reason=str(error),semantic_validation=error.validation)
                except Exception as error:
                    if self.cancelled.is_set(): state='CANCELLED'
                    elif stage=='request': state='INVALID_MODEL_RESPONSE' if session.response_received else 'TRANSPORT_ERROR'
                    elif stage=='export': state='LOCAL_EXPORT_ERROR'
                    else: state='LOCAL_CHECK_ERROR'
                    reason=str(error) if isinstance(error,client.ClientError) else 'Локальная операция или транспорт завершились ошибкой: '+type(error).__name__
                    row.update(state=state,reason=reason,failure_stage=stage)
                finally:
                    row['events']=copy.deepcopy(session.events)
                    for event in row['events']: event.pop('response_text',None)
                    # Данные уже строгие; дополнительное маскирование перед любой записью/показом.
                    rows[index]=row=live._redact(row,key)
                if self.cancelled.is_set(): row['state']='CANCELLED'
                record['cancel_requested']=self.cancelled.is_set()
                if row['state'] not in ('COMPLETED','NO_COUNTEREXAMPLE'): record['state']=row['state']
                save_report(record,directory)
                if record['state']!='RUNNING': break
            else: record['state']='COMPLETED' if any(r['exports'] for r in rows) else 'NO_COUNTEREXAMPLE'
        except StaleRuntime as error:
            record['state']='STALE_RUNTIME';record['reason']=str(error)
        except Exception:
            record['state']='ERROR'
            for row in rows:
                if row['state']=='RUNNING': row['state']='ERROR'
            save_error='Запуск остановлен из-за ошибки подготовки, проверки или сохранения. Следующих запросов нет.'
        finally:
            record=live._redact(record,key);key=None;session=None
            record['cancel_requested']=self.cancelled.is_set();record['budget_reserved']=budget.used
            if self.cancelled.is_set(): record['state']='CANCELLED'
            refresh(record)
            try: path=save_report(record,directory)
            except Exception:
                path=None;save_error='Отчёт сохранён не полностью. Результат доступен в памяти; повтор сохранения не вызывает API.'
            self.results.put({'record':record,'directory':str(directory),'report':str(path) if path else None,'save_error':save_error})
