"""Офлайн-граница будущей модели: только данные, строгая проверка, никакого API."""

import hashlib
import json

from catalog import CATALOG

MAX_PROPOSAL_BYTES = 4096
FIELDS = ("schema_version", "case_id", "hypothesis_id", "action", "check_id", "input_sha256")


class ProposalError(ValueError):
    """Предложение отклонено до запуска вычислений."""


def input_digest(case):
    normalized = json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def demonstration_proposal(case):
    """Детерминированный демонстрационный режим; это не ответ нейросети."""
    item = CATALOG[case["operation"]]
    return {
        "schema_version": 1, "case_id": item["case_id"], "hypothesis_id": item["hypothesis"],
        "action": "run_check", "check_id": item["check_id"], "input_sha256": input_digest(case),
    }


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProposalError("Повторяющиеся поля предложения запрещены.")
        value[key] = item
    return value


def _reject_constant(value):
    raise ProposalError("Нечисловые константы JSON в предложении запрещены.")


def validate_proposal(proposal, case):
    """Принимает JSON аргументов или словарь, сверяет структуру и привязку к вводу."""
    try:
        if type(proposal) is bytes:
            if len(proposal) > MAX_PROPOSAL_BYTES:
                raise ProposalError("Предложение больше 4096 байт.")
            proposal = proposal.decode("utf-8")
        if type(proposal) is str:
            if len(proposal.encode("utf-8")) > MAX_PROPOSAL_BYTES:
                raise ProposalError("Предложение больше 4096 байт.")
            proposal = json.loads(proposal, object_pairs_hook=_object, parse_constant=_reject_constant)
        if type(proposal) is not dict or set(proposal) != set(FIELDS):
            raise ProposalError("Предложение должно содержать ровно шесть разрешённых полей.")
        if type(proposal["schema_version"]) is not int or proposal["schema_version"] != 1:
            raise ProposalError("Нужна целочисленная версия предложения 1.")
        if any(type(proposal[key]) is not str for key in FIELDS[1:]):
            raise ProposalError("Идентификаторы предложения должны быть строками.")
        if proposal != demonstration_proposal(case):
            raise ProposalError("Операция, проверка, гипотеза или отпечаток ввода не разрешены для этой задачи.")
        return dict(proposal)
    except ProposalError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ProposalError("Некорректный JSON предложения.") from exc


def proposal_schema():
    """Описание для будущего tool/function.parameters; локальная проверка обязательна."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Предложение одной разрешённой проверки ReproCase",
        "type": "object", "additionalProperties": False, "required": list(FIELDS),
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "case_id": {"type": "string", "enum": [x["case_id"] for x in CATALOG.values()]},
            "hypothesis_id": {"type": "string", "enum": [x["hypothesis"] for x in CATALOG.values()]},
            "action": {"type": "string", "const": "run_check"},
            "check_id": {"type": "string", "enum": [x["check_id"] for x in CATALOG.values()]},
            "input_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        "oneOf": [
            {"properties": {"case_id": {"const": x["case_id"]},
                            "hypothesis_id": {"const": x["hypothesis"]},
                            "check_id": {"const": x["check_id"]}}}
            for x in CATALOG.values()
        ],
    }
