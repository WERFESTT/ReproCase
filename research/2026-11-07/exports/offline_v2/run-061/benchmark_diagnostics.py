"""Диагностическая сторона оценки: только публичное наблюдение и закрытые проверки.

Модуль не читает набор эталонных меток и не получает вариант buggy/fixed.
"""

import copy
import hashlib
import json
import math

from catalog import CATALOG
from reference import REFERENCES, compare_value, predicted_fault
from reprocase import validate_case


MAX_CHECKS = 3
VERDICTS = ("BUG", "CLEAN", "INSUFFICIENT_DATA")
CHECK_DESCRIPTIONS = {
    "input_separates_fault": "Проверить, различает ли этот ввод исправное поведение и известную учебную ошибку.",
    "matches_contract": "Сверить наблюдаемый результат с независимым расчётом по правилу задачи.",
    "matches_known_fault": "Проверить, соответствует ли наблюдение следствию известной для этого типа задачи ошибки.",
}
PUBLIC_FIELDS = {"schema_version", "sample_id", "operation", "task", "inputs", "check", "observation"}


class DiagnosticError(ValueError):
    pass


def view_digest(view):
    return hashlib.sha256(json.dumps(view, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_view(view):
    if type(view) is not dict or set(view) != PUBLIC_FIELDS:
        raise DiagnosticError("Нужны только поля публичного диагностического наблюдения.")
    if type(view["schema_version"]) is not int or view["schema_version"] != 1:
        raise DiagnosticError("Неизвестная версия наблюдения.")
    if type(view["sample_id"]) is not str or len(view["sample_id"]) != 16 or any(c not in "0123456789abcdef" for c in view["sample_id"]):
        raise DiagnosticError("Неверный идентификатор наблюдения.")
    operation = view["operation"]
    if type(operation) is not str or operation not in CATALOG or view["task"] != CATALOG[operation]["task"]:
        raise DiagnosticError("Неизвестный тип или описание задачи.")
    try:
        case = validate_case({"schema_version": 1, "operation": operation,
                              "hypothesis": CATALOG[operation]["hypothesis"],
                              "inputs": view["inputs"], "check": view["check"]})
    except ValueError:
        raise DiagnosticError("Некорректные диагностические входные данные.") from None
    value = view["observation"]
    if operation == "seeded_sequence":
        valid = type(value) is list and len(value) == case["inputs"]["count"] and all(type(x) is int and 0 <= x <= 15 for x in value)
    else:
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e15
        except OverflowError:
            valid = False
    if not valid:
        raise DiagnosticError("Некорректный наблюдаемый результат.")
    return copy.deepcopy(view)


def action_schema():
    """Новая схема для оценки: без готового правильного предложения или эталона."""
    return {
        "oneOf": [
            {"type": "object", "additionalProperties": False,
             "required": ["action", "check_id", "sample_sha256"],
             "properties": {"action": {"const": "run_check"},
                            "check_id": {"enum": list(CHECK_DESCRIPTIONS)},
                            "sample_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
            {"type": "object", "additionalProperties": False,
             "required": ["action", "verdict", "sample_sha256"],
             "properties": {"action": {"const": "finish"}, "verdict": {"enum": list(VERDICTS)},
                            "sample_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
        ]
    }


def model_context(view, history):
    """Только эти данные разрешено передавать будущему селектору проверок."""
    view = validate_view(view)
    return {
        "mode": "evaluation_no_answer_hint", "sample": view,
        "sample_sha256": view_digest(view), "history": copy.deepcopy(history),
        "checks": dict(CHECK_DESCRIPTIONS), "max_checks": MAX_CHECKS,
        "remaining_checks": MAX_CHECKS - len(history), "action_schema": action_schema(),
        "instruction": "Выберите разрешённую проверку или закончите с диагностическим выводом. "
                       "Эталонная метка, вариант реализации и готовый ответ вам не переданы. "
                       "Не добавляйте код, команды, пути или новые входные данные.",
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("Повторяющиеся поля решения запрещены.")
        result[key] = value
    return result


def _reject_constant(value):
    raise DiagnosticError("Некорректная константа в решении.")


def validate_action(action, view):
    try:
        if type(action) is str:
            if len(action.encode("utf-8")) > 4096:
                raise DiagnosticError("Решение больше 4096 байт.")
            action = json.loads(action, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if type(action) is not dict or action.get("action") not in ("run_check", "finish"):
            raise DiagnosticError("Неизвестное действие.")
        extra = "check_id" if action["action"] == "run_check" else "verdict"
        allowed = CHECK_DESCRIPTIONS if extra == "check_id" else VERDICTS
        if set(action) != {"action", extra, "sample_sha256"} or type(action.get(extra)) is not str or action[extra] not in allowed:
            raise DiagnosticError("Лишние поля или неизвестное значение решения.")
        if action["sample_sha256"] != view_digest(view):
            raise DiagnosticError("Решение не относится к текущему наблюдению.")
        return dict(action)
    except DiagnosticError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DiagnosticError("Некорректное решение диагностического метода.") from None


def run_check(view, check_id):
    view = validate_view(view)
    if type(check_id) is not str or check_id not in CHECK_DESCRIPTIONS:
        raise DiagnosticError("Проверка не разрешена.")
    operation, inputs = view["operation"], view["inputs"]
    tolerance = view["check"].get("absolute_tolerance_s", view["check"].get("absolute_tolerance", 0))
    expected = REFERENCES[operation](**inputs)
    fault = predicted_fault(operation, inputs)
    if check_id == "input_separates_fault":
        return {"separates": not compare_value(operation, fault, expected, tolerance)["passed"]}
    if check_id == "matches_contract":
        result = compare_value(operation, view["observation"], expected, tolerance)
        return {"matches": result["passed"], "calculated_reference": expected,
                "absolute_error": result["absolute_error"], "allowed_error": result["allowed_error"]}
    return {"matches": compare_value(operation, view["observation"], fault, tolerance)["passed"],
            "calculated_fault_signature": fault}


def fixed_order_selector(context):
    """Обычное правило с ранней остановкой; не использует эталон оценщика."""
    history = {item["check_id"]: item["result"] for item in context["history"]}
    digest = context["sample_sha256"]
    def run(identifier):
        return {"action": "run_check", "check_id": identifier, "sample_sha256": digest}
    def finish(verdict):
        return {"action": "finish", "verdict": verdict, "sample_sha256": digest}
    if "input_separates_fault" not in history:
        return run("input_separates_fault")
    if not history["input_separates_fault"]["separates"]:
        return finish("INSUFFICIENT_DATA")
    if "matches_contract" not in history:
        return run("matches_contract")
    if history["matches_contract"]["matches"]:
        return finish("CLEAN")
    if "matches_known_fault" not in history:
        return run("matches_known_fault")
    return finish("BUG" if history["matches_known_fault"]["matches"] else "INSUFFICIENT_DATA")


def diagnose(view, selector=fixed_order_selector):
    view = validate_view(view)
    history = []
    try:
        for _ in range(MAX_CHECKS + 1):
            action = validate_action(selector(model_context(view, history)), view)
            if action["action"] == "finish":
                return {"sample_id": view["sample_id"], "verdict": action["verdict"],
                        "checks_run": len(history), "history": history, "protocol_error": None}
            if len(history) >= MAX_CHECKS:
                raise DiagnosticError("Бюджет из трёх проверок исчерпан.")
            if any(item["check_id"] == action["check_id"] for item in history):
                raise DiagnosticError("Повтор одной проверки запрещён.")
            history.append({"check_id": action["check_id"], "result": run_check(view, action["check_id"])})
    except DiagnosticError as error:
        return {"sample_id": view["sample_id"], "verdict": "INSUFFICIENT_DATA",
                "checks_run": len(history), "history": history, "protocol_error": str(error)}
    raise DiagnosticError("Не завершён диагностический маршрут.")
