"""Публичное правило, строгие действия и обычные стратегии. Меток оценщика здесь нет."""

import copy
import re

import input_search as shared
import reprocase as app

VERSION = "adaptive-threshold-v1"
MAX_TRIALS = 3
VALUES = tuple(range(1, 9))
THRESHOLDS = tuple(range(2, 9))
INITIAL_X = 4
RULE = ("Учебный целочисленный переключатель: вернуть 0 при x < T, иначе 1. "
        "Один неизвестный порог T из списка possible_thresholds постоянен внутри сеанса. "
        "Проверяющий знает настройку T и после каждой разрешённой пробы возвращает фактический "
        "результат и независимую проверку. Настройка T и тип реализации селектору не сообщаются.")
INSTRUCTION = ("Выберите новый вход из candidates или finish. Используйте только публичное правило "
               "и наблюдения. Не добавляйте код, пути, команды или новые числа. "
               "Совпадение нескольких проверок не доказывает исправность.")
Error = shared.SearchError


def candidates():
    return [{"id": f"c{x:02}", "inputs": {"x": x}} for x in VALUES]


def validate_candidates(items):
    if type(items) is not list or len(items) != len(VALUES):
        raise Error("Нужны ровно восемь разрешённых входов.")
    ids, xs = set(), set()
    for item in items:
        if type(item) is not dict or set(item) != {"id", "inputs"}:
            raise Error("Лишние поля кандидата.")
        identifier, inputs = item["id"], item["inputs"]
        if type(identifier) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", identifier) or identifier in ids:
            raise Error("Некорректный или повторный ID кандидата.")
        if type(inputs) is not dict or set(inputs) != {"x"} or type(inputs["x"]) is not int or inputs["x"] not in VALUES:
            raise Error("Ввод не входит в конечный план.")
        ids.add(identifier); xs.add(inputs["x"])
    if xs != set(VALUES):
        raise Error("Повторный или отсутствующий вход.")
    return copy.deepcopy(items)


def validate_observation(observation, *, trial=False):
    fields = {"inputs", "actual", "expected", "passed"} | ({"candidate_id"} if trial else set())
    if type(observation) is not dict or set(observation) != fields:
        raise Error("Некорректные поля наблюдения.")
    inputs = observation["inputs"]
    if type(inputs) is not dict or set(inputs) != {"x"} or type(inputs["x"]) is not int or inputs["x"] not in VALUES:
        raise Error("Некорректный численный ввод.")
    if any(type(observation[k]) is not int or observation[k] not in (0, 1) for k in ("actual", "expected")):
        raise Error("Результат должен быть целым 0 или 1.")
    if type(observation["passed"]) is not bool or observation["passed"] != (observation["actual"] == observation["expected"]):
        raise Error("Неверный итог сравнения.")


def make_context(initial, history, items=None):
    items = validate_candidates(candidates() if items is None else items)
    validate_observation(initial)
    if initial["inputs"]["x"] != INITIAL_X or not initial["passed"]:
        raise Error("После исходного расхождения поиск не продолжается.")
    if type(history) is not list or len(history) > MAX_TRIALS:
        raise Error("Некорректная история или превышенный бюджет.")
    by_id = {c["id"]: c["inputs"] for c in items}
    seen = {INITIAL_X}
    for entry in history:
        validate_observation(entry, trial=True)
        if (type(entry["candidate_id"]) is not str or entry["candidate_id"] not in by_id
                or entry["inputs"] != by_id[entry["candidate_id"]] or entry["inputs"]["x"] in seen or not entry["passed"]):
            raise Error("История содержит повтор, чужой вход или расхождение.")
        seen.add(entry["inputs"]["x"])
    observations = [initial, *history]
    if not any(all(int(o["inputs"]["x"] >= t) == o["actual"] for o in observations) for t in THRESHOLDS):
        raise Error("Наблюдения не согласуются с публичным правилом.")
    public = {"contract": VERSION, "rule": RULE, "possible_thresholds": list(THRESHOLDS),
              "initial": initial, "history": history, "candidates": items,
              "max_trials": MAX_TRIALS, "remaining_trials": MAX_TRIALS - len(history)}
    public["session_sha256"] = shared.digest(public)
    public["instruction"] = INSTRUCTION
    public["action_schema"] = shared.action_schema(items)
    return copy.deepcopy(public)


def validate_context(context):
    try:
        if type(context) is not dict:
            raise Error("Нужен публичный контекст.")
        canonical = make_context(context["initial"], context["history"], context["candidates"])
        if app.json_text(context) != app.json_text(canonical):
            raise Error("Контекст меняет правило, поля или бюджет.")
        return canonical
    except (KeyError, TypeError, ValueError, RecursionError):
        raise Error("Публичный контекст не прошёл проверку.") from None


def validate_action(action, context):
    return shared.validate_action(action, validate_context(context))


def available(context):
    seen = {context["initial"]["inputs"]["x"], *(o["inputs"]["x"] for o in context["history"])}
    return [c for c in context["candidates"] if c["inputs"]["x"] not in seen] if context["remaining_trials"] else []


def choose(context, candidate=None):
    action = {"action": "finish", "session_sha256": context["session_sha256"]}
    if candidate is not None:
        action.update(action="try_input", candidate_id=candidate["id"])
    return action


def fixed_order(context):
    context = validate_context(context)
    items = available(context)
    return choose(context, items[0] if items else None)


def c02_first(context):
    context = validate_context(context)
    items = available(context)
    return choose(context, next((c for c in items if c["id"] == "c02"), items[0] if items else None))


def minimax_boundary(context):
    """Деление допустимых порогов; при равенстве — покрытие соседей границы.

    Это обычный поиск по монотонному правилу, не модель дефектов. ID/порядок не ранжируют входы.
    """
    context = validate_context(context)
    items = available(context)
    if not items:
        return choose(context)
    observations = [context["initial"], *context["history"]]
    possible = [t for t in context["possible_thresholds"]
                if all(int(o["inputs"]["x"] >= t) == o["actual"] for o in observations)]
    def score(candidate):
        x = candidate["inputs"]["x"]
        zeros = sum(x < t for t in possible)
        boundary_coverage = sum(x in (t - 1, t) for t in possible)
        return (max(zeros, len(possible) - zeros), -boundary_coverage,
                abs(2 * x - (min(possible) + max(possible) - 1)), x)
    return choose(context, min(items, key=score))


STRATEGIES = {"fixed_order": fixed_order, "c02_first": c02_first, "minimax_boundary": minimax_boundary}
