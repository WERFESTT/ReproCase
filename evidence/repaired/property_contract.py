"""Ручной ограниченный язык свойств. Только данные, без eval/exec и вызовов по имени."""
import copy
import hashlib
import itertools
import json
import math

import historical_core as core
import nebius_client as client

VERSION = 'property-v1'
MAX_PROPOSAL_BYTES = 4096
CASE_OPERATIONS = {'cpython_correlation': 'correlation', 'more_reversed': 'reversed_numeric_range'}
OPERATION_CASES = {v:k for k,v in CASE_OPERATIONS.items()}

class PropertyError(client.ClientError):
    pass

class FalseProperty(PropertyError):
    def __init__(self,message,reference_evaluations,counterexample):
        super().__init__(message)
        self.validation={'accepted':False,'reference_evaluations':reference_evaluations,
                         'counterexample':counterexample,'sut_calls':0}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def schema():
    def obj(properties):
        return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    number={'type':'number','minimum':-1000000,'maximum':1000000}
    integer={'type':'integer','minimum':0,'maximum':1000000}
    enum=lambda *values:{'type':'string','enum':list(values)}
    return obj({'action':enum('propose_property'),'version':enum(VERSION),
      'context_sha256':{'type':'string','minLength':64,'maxLength':64},
      'domain':{'type':'array','minItems':2,'maxItems':3,'items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'integer','minimum':-500,'maximum':500}}},
      'assertion':{'oneOf':[obj({'op':enum('finite')}),obj({'op':enum('completes')}),
          obj({'op':enum('between'),'min':number,'max':number}),obj({'op':enum('equal_number'),'value':number}),
          obj({'op':enum('sequence_equal'),'order':enum('forward','reverse')}),
          obj({'op':enum('length_between'),'min':integer,'max':integer})]}})

def context(case):
    if case not in CASE_OPERATIONS: raise PropertyError('Неизвестная задача.')
    if case=='cpython_correlation':
        symptom='Корреляция на некоторых допустимых непостоянных данных возвращает нечисловой результат или исключение о постоянном входе.'
        contract='Pearson correlation: ковариация, делённая на произведение стандартных отклонений. Для непостоянных конечных данных результат конечен и лежит в [-1, 1].'
        generator='Пара целых показателей a,b задаёт x=[-2**a, 2**a], y=[-2**b, 2**b]. Выберите поддиапазоны показателей и утверждение. Выполнение арифметики генератора реализовано локально.'
    else:
        symptom='Обратный обход numeric_range иногда вызывает IndexError при допустимых целых аргументах.'
        contract='numeric_range(start, stop, step) задаёт арифметическую последовательность с исключённым stop, как встроенный range на целых. reversed меняет порядок элементов; пустая последовательность тоже допустима.'
        generator='Три целых аргумента start, stop, step; step=0 всегда исключён. Выберите поддиапазоны и утверждение. sequence_equal сравнивает со встроенным range в указанном порядке.'
    public={'version':VERSION,'operation':CASE_OPERATIONS[case],'symptom':symptom,'public_contract':contract,
      'generator':generator,'available_domain':copy.deepcopy(core.plan()['domains'][case]),
      'assertion_semantics':{'finite':'Результат — конечное число.',
        'between':'Число конечно и лежит между min и max включительно.',
        'equal_number':'Число конечно; абсолютная разница с value не больше 1e-12.',
        'sequence_equal':'Точное совпадение со встроенным целочисленным range в выбранном порядке.',
        'length_between':'Длина последовательности лежит между min и max включительно.',
        'completes':'Вызов завершился без исключения; значение результата не проверяется.'},
      'excluded_values':copy.deepcopy(core.plan().get('excluded_values',{}).get(case,{})),
      'limits':{'proposal_requests':1,'max_completion_tokens':1024,'max_request_bytes':16000,'search_and_shrink_calls':252,'confirmation_calls':4}}
    public['context_sha256']=digest(public)
    return public

def validate_context(value):
    if type(value) is not dict or type(value.get('operation')) is not str or value['operation'] not in OPERATION_CASES:
        raise PropertyError('Неверный публичный контекст.')
    canonical=context(OPERATION_CASES[value['operation']])
    if value!=canonical or set(value)!=set(canonical): raise PropertyError('Контекст изменён или содержит лишние поля.')
    return canonical

def exact(value, fields):
    if type(value) is not dict or set(value)!=set(fields): raise PropertyError('Неизвестные или отсутствующие поля предложения.')

def numeric(value):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1000000:
        raise PropertyError('Нужно ограниченное конечное число.')
    return value

def validate_proposal(raw, public):
    public=validate_context(public)
    try:
        if isinstance(raw,dict): raw=json.dumps(raw,allow_nan=False,ensure_ascii=False)
        if type(raw) is not str or len(raw.encode())>MAX_PROPOSAL_BYTES:
            raise PropertyError('Предложение должно быть JSON не больше 4096 байт.')
        p=json.loads(raw,object_pairs_hook=client._json_object,parse_constant=client._reject_constant)
        exact(p,('action','version','context_sha256','domain','assertion'))
        if p['action']!='propose_property' or p['version']!=VERSION or p['context_sha256']!=public['context_sha256']:
            raise PropertyError('Неизвестное действие, версия или чужой контекст.')
        domain=p['domain']; bounds=public['available_domain']
        if type(domain) is not list or len(domain)!=len(bounds): raise PropertyError('Неверная размерность области.')
        for pair,(lo,hi) in zip(domain,bounds):
            if type(pair) is not list or len(pair)!=2 or any(type(x) is not int for x in pair) or not lo<=pair[0]<=pair[1]<=hi:
                raise PropertyError('Область выходит за разрешённые границы.')
        if len(domain)==3 and domain[2]==[0,0]: raise PropertyError('Область не содержит допустимого шага.')
        a=p['assertion']
        if type(a) is not dict or type(a.get('op')) is not str: raise PropertyError('Неверное утверждение.')
        op=a['op']
        fields={'finite':('op',),'completes':('op',),'between':('op','min','max'),
          'equal_number':('op','value'),'sequence_equal':('op','order'),'length_between':('op','min','max')}
        if op not in fields: raise PropertyError('Неизвестная операция свойства.')
        exact(a,fields[op])
        if op in ('between','length_between'):
            if numeric(a['min'])>numeric(a['max']): raise PropertyError('Перепутаны границы утверждения.')
        if op=='length_between' and any(type(a[k]) is not int or a[k]<0 for k in ('min','max')):
            raise PropertyError('Длины должны быть неотрицательными целыми.')
        if op=='equal_number': numeric(a['value'])
        if op=='sequence_equal' and a['order'] not in ('forward','reverse'): raise PropertyError('Неизвестный порядок.')
        scalar=public['operation']=='correlation'
        if (scalar and op in ('sequence_equal','length_between')) or (not scalar and op in ('finite','between','equal_number')):
            raise PropertyError('Утверждение не подходит типу результата.')
        return p
    except PropertyError: raise
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise PropertyError('Предложение не прошло строгую проверку JSON.') from None

def contains(proposal,value):
    return all(lo<=x<=hi for x,(lo,hi) in zip(value,proposal['domain']))

def holds(assertion, value, inputs):
    """Компилятор ограниченных операторов: фиксированная диспетчеризация, нет кода из ответа."""
    op=assertion['op']
    if op=='completes': return True
    if op=='finite': return type(value) in (int,float) and math.isfinite(value)
    if op=='between': return type(value) in (int,float) and math.isfinite(value) and assertion['min']<=value<=assertion['max']
    if op=='equal_number': return type(value) in (int,float) and math.isfinite(value) and abs(value-assertion['value'])<=1e-12
    if op=='length_between': return type(value) is list and assertion['min']<=len(value)<=assertion['max']
    if op=='sequence_equal':
        reference=list(range(*inputs))
        if assertion['order']=='reverse': reference.reverse()
        return value==reference
    raise PropertyError('Неизвестный оператор.')

def semantic_validation(case,proposal):
    """Не запускает ни одну историческую версию. Доказательство ограничено нашим языком."""
    p=validate_proposal(proposal,context(case)); assertion=p['assertion']
    if case=='cpython_correlation':
        # Ручное математическое правило: симметричные пары положительно пропорциональны.
        # Проверка всех показателей следует из этого вывода, не из fixed-реализации.
        if not holds(assertion,1.0,(0,0)):
            raise FalseProperty('Свойство противоречит корреляции положительно пропорциональных пар.',1,
                                {'basis':'symbolic_positive_proportional_pairs','contract_result':1.0})
        return {'accepted':True,'basis':'symbolic_positive_proportional_pairs','reference_evaluations':1,'sut_calls':0}
    count=0
    for value in itertools.product(*(range(lo,hi+1) for lo,hi in p['domain'])):
        if value[2]==0: continue
        count+=1
        expected=list(reversed(range(*value)))
        if not holds(assertion,expected,value):
            raise FalseProperty('Свойство опровергнуто независимым контрактом целочисленного диапазона.',count,
                                {'input':list(value),'contract_result':expected})
    return {'accepted':True,'basis':'exhaustive_finite_integer_contract','reference_evaluations':count,'sut_calls':0}

def evaluate(case,version,proposal,value):
    """Поиск использует только предложенное свойство. Эталон старого контура не подменяет его."""
    result=core.check(case,version,value)
    bad=result['status']=='SUT_EXCEPTION' or not holds(proposal['assertion'],result['actual'],value)
    return {'status':'PROPERTY_VIOLATION' if bad else 'PASS','actual':result['actual'],
            'exception_type':result['exception_type'],'input':list(value)}
