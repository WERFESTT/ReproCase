#!/usr/bin/env python3
"""ReproCase: три учебных случая и строгий офлайн-маршрут проверки."""

from localization import print
import localization as loc

import argparse
import hashlib
import json
import math
import platform
import sys
from pathlib import Path

from calculations import CALCULATIONS, VARIANTS
from catalog import CATALOG
from proposals import (MAX_PROPOSAL_BYTES, ProposalError, demonstration_proposal,
                       input_digest, validate_proposal)
from reference import RELATIVE_TOLERANCE, REFERENCES, compare_value, predicted_fault
from regression import run_case_regression
from reporting import numeric, render_html, render_markdown


ROOT = Path(__file__).resolve().parent
DEFAULT_CASE = ROOT / "examples" / "unit_mismatch.json"
MAX_INPUT_BYTES = 16_384
SOURCE_FILES = ("calculations.py", "reference.py", "regression.py", "reprocase.py",
                "catalog.py", "proposals.py", "reporting.py", "nebius_client.py", "desktop.py")
CHECKS = {(op, item["hypothesis"]): item["check_id"] for op, item in CATALOG.items()}
DISCLAIMER = (
    "Учебный пример с намеренно внесённой ошибкой. "
    "Результаты относятся только к заданному учебному правилу."
)


class InputError(ValueError):
    """Некорректные данные: расчёт не запускается."""


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InputError(f"{label}: нужны только поля {', '.join(keys)}.")


def number(value, label, minimum, maximum):
    if type(value) not in (int, float):
        raise InputError(f"{label}: нужно число, а не строка или логическое значение.")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise InputError(f"{label}: число слишком велико.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise InputError(f"{label}: допустимо конечное число от {minimum:g} до {maximum:g}.")
    return value


def integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f"{label}: нужно целое число от {minimum} до {maximum}.")
    return value


def select_check(operation, hypothesis):
    """Граница будущего адаптера: только выбор известного идентификатора проверки."""
    if type(operation) is not str or type(hypothesis) is not str:
        raise InputError("Операция и гипотеза должны быть строками из разрешённого списка.")
    try:
        return CHECKS[(operation, hypothesis)]
    except KeyError:
        raise InputError("Эта операция или гипотеза не поддерживается учебным прототипом.") from None


def validate_case(data):
    exact_keys(data, ("schema_version", "operation", "hypothesis", "inputs", "check"), "Настройки")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise InputError("Поддерживается только целочисленная schema_version = 1.")
    select_check(data["operation"], data["hypothesis"])
    operation = data["operation"]
    if operation == "travel_time":
        exact_keys(data["inputs"], ("distance_mm", "speed_m_s"), "Исходные данные")
        exact_keys(data["check"], ("absolute_tolerance_s",), "Проверка")
        inputs = {
            "distance_mm": number(data["inputs"]["distance_mm"], "Путь, мм", 0, 1e6),
            "speed_m_s": number(data["inputs"]["speed_m_s"], "Скорость, м/с", 1e-6, 1e3),
        }
        checks = {"absolute_tolerance_s": number(
            data["check"]["absolute_tolerance_s"], "Абсолютный допуск, с", 0, 1e-6)}
    elif operation == "seeded_sequence":
        exact_keys(data["inputs"], ("seed", "count"), "Исходные данные")
        exact_keys(data["check"], ("comparison",), "Проверка")
        if data["check"]["comparison"] != "exact":
            raise InputError("Последовательность сравнивается только точно: comparison = exact.")
        inputs = {"seed": integer(data["inputs"]["seed"], "Начальное значение", 0, 15),
                  "count": integer(data["inputs"]["count"], "Число элементов", 0, 32)}
        checks = {"comparison": "exact"}
    else:
        exact_keys(data["inputs"], ("initial", "rate", "duration_s", "steps"), "Исходные данные")
        exact_keys(data["check"], ("absolute_tolerance",), "Проверка")
        inputs = {
            "initial": number(data["inputs"]["initial"], "Начальное значение", -1e6, 1e6),
            "rate": number(data["inputs"]["rate"], "Скорость изменения", -1000, 1000),
            "duration_s": number(data["inputs"]["duration_s"], "Длительность", 0, 100),
            "steps": integer(data["inputs"]["steps"], "Число шагов", 2, 1000),
        }
        checks = {"absolute_tolerance": number(
            data["check"]["absolute_tolerance"], "Абсолютный допуск", 1e-12, 1e-6)}
    return {
        "schema_version": 1,
        "operation": data["operation"],
        "hypothesis": data["hypothesis"],
        "inputs": inputs,
        "check": checks,
    }


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InputError(f"Поле {key!r} повторяется в JSON.")
        value[key] = item
    return value


def reject_constant(value):
    raise InputError(f"Значение {value} не является конечным числом JSON.")


def load_case(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise InputError(f"Файл настроек больше {MAX_INPUT_BYTES} байт.")
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except InputError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise InputError(f"Не удалось прочитать настройки JSON: {exc}") from None
    return validate_case(data)


def evaluate(case, variant):
    case = validate_case(case)
    return execute_proposal(case, demonstration_proposal(case), variant)


def execute_proposal(case, proposal, variant, source="deterministic_demo"):
    """Сначала полная проверка данных предложения, только затем разрешённый расчёт."""
    case = validate_case(case)
    checked = validate_proposal(proposal, case)
    if type(variant) is not str or variant not in VARIANTS:
        raise InputError("Разрешены только варианты buggy и fixed.")
    if source not in ("deterministic_demo", "offline_supplied"):
        raise InputError("Разрешены только офлайн-источники предложений.")
    inputs = case["inputs"]
    operation = case["operation"]
    tolerance = case["check"].get("absolute_tolerance_s", case["check"].get("absolute_tolerance", 0))
    expected = REFERENCES[operation](**inputs)
    actual = CALCULATIONS[operation][variant](**inputs)
    result = compare_value(operation, actual, expected, tolerance)
    predicted = predicted_fault(operation, inputs)
    informative = not compare_value(operation, predicted, expected, tolerance)["passed"]
    signature_matches = compare_value(operation, actual, predicted, tolerance)["passed"]
    regression = run_case_regression(operation, variant, inputs, tolerance)
    consistent = result["passed"] == regression["passed"] and regression["errors"] == 0
    if not informative or not consistent:
        verdict = "INSUFFICIENT_DATA"
        reason = "Этот ввод не различает заданную ошибку и исправный расчёт, либо проверки не согласованы."
    elif result["passed"]:
        verdict = "REFUTED"
        reason = "На этом различающем вводе результат совпадает с эталоном; следствие заданной ошибки отсутствует."
    elif signature_matches:
        verdict = "CONFIRMED"
        reason = "Результат не совпадает с эталоном и совпадает с независимо предсказанным следствием заданной ошибки."
    else:
        verdict = "INSUFFICIENT_DATA"
        reason = "Отклонение есть, но оно не соответствует заданной причине; приписывать ему эту причину нельзя."
    result["variant"] = variant
    result.update({"regression": regression, "proposal": checked, "proposal_source": source,
                   "executed_check": checked["check_id"], "verdict": verdict, "reason": reason,
                   "predicted_fault": predicted, "informative_input": informative,
                   "hypothesis_signature_matches": signature_matches})
    if operation == "travel_time":
        result.update({"actual_s": result["actual"], "expected_s": result["expected"],
                       "absolute_error_s": result["absolute_error"], "allowed_error_s": result["allowed_error"]})
    return result


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def make_evidence(case):
    case = validate_case(case)
    buggy, fixed = (evaluate(case, variant) for variant in ("buggy", "fixed"))
    item = CATALOG[case["operation"]]
    expected = buggy["expected"]
    ratio = buggy["actual"] / expected if case["operation"] == "travel_time" and expected else None
    regression_agrees = all(
        result["passed"] == result["regression"]["passed"]
        and result["regression"]["errors"] == 0
        and result["regression"]["tests_run"] == 1
        for result in (buggy, fixed)
    )
    reproduced = buggy["verdict"] == "CONFIRMED" and fixed["verdict"] == "REFUTED" and regression_agrees
    return {
        "report_schema_version": 2,
        "project": "ReproCase",
        "disclaimer": DISCLAIMER,
        "case": case,
        "description": item,
        "classification": "REPRODUCED" if reproduced else "NOT_REPRODUCED",
        "hypothesis_confirmed_for_this_input": reproduced,
        "buggy_to_expected_ratio": ratio,
        "regression_agrees_with_measurements": regression_agrees,
        "checker": {
            "id": select_check(case["operation"], case["hypothesis"]),
            "selection": "deterministic_demo",
            "reference_description": item["reference_text"],
            "relative_tolerance": RELATIVE_TOLERANCE,
            "nvidia_adapter_connected": False,
            "model_calls": 0,
        },
        "results": {"buggy": buggy, "fixed": fixed},
        "provenance": {
            "python_version": platform.python_version(),
            "normalized_input_sha256": hashlib.sha256(json_text(case).encode()).hexdigest(),
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in SOURCE_FILES
            },
        },
    }


def make_suite_evidence():
    cases = [make_evidence(load_case(ROOT / "examples" / item["file"])) for item in CATALOG.values()]
    return {
        "report_schema_version": 2, "project": "ReproCase", "stage": 2,
        "disclaimer": DISCLAIMER, "mode": "deterministic_demo", "model_connected": False,
        "model_calls": 0, "actual_model_usage": None, "cases": cases,
        "summary": {
            "cases": len(cases),
            "confirmed_buggy": sum(c["results"]["buggy"]["verdict"] == "CONFIRMED" for c in cases),
            "refuted_fixed": sum(c["results"]["fixed"]["verdict"] == "REFUTED" for c in cases),
            "regressions_run": sum(r["regression"]["tests_run"] for c in cases for r in c["results"].values()),
        },
    }


def write_report(evidence, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "bug-reproducer-evidence.json": json_text(evidence),
        "report.html": render_html(evidence),
        "bug-reproducer-report.md": render_markdown(evidence),
    }
    for name, content in files.items():
        (output_dir / name).write_text(content, encoding="utf-8")


class RussianParser(loc.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Ошибка параметров. Используйте --help для списка команд и вариантов.\n")


def parser():
    root = RussianParser(description="ReproCase — три учебных офлайн-проверки.")
    root._positionals.title = "Команды"
    root._optionals.title = "Параметры"
    root._actions[0].help = "показать справку"
    commands = root.add_subparsers(dest="command", required=True)
    for name, description in (("demo", "проверить один пример и записать отчёт"),
                              ("demo-all", "проверить три примера и записать общий отчёт"),
                              ("propose", "показать демонстрационное предложение без расчёта"),
                              ("check", "запустить разрешённую проверку одного варианта")):
        child = commands.add_parser(name, help=description, description=description)
        child._optionals.title = "Параметры"
        child._actions[0].help = "показать справку"
        if name != "demo-all":
            child.add_argument("--input", type=Path, default=DEFAULT_CASE, help="путь к настройкам JSON")
        if name in ("demo", "demo-all"):
            child.add_argument("--output-dir", type=Path, default=ROOT / "outputs", help="каталог отчёта")
        elif name == "check":
            child.add_argument("--variant", choices=tuple(VARIANTS), required=True,
                               help="buggy — намеренно ошибочный; fixed — исправный")
            child.add_argument("--proposal", type=Path, help="локальный JSON искусственного предложения")
    return root


def main(argv=None):
    argv = loc.cli_args(argv)
    args = parser().parse_args(argv)
    try:
        if args.command == "demo-all":
            evidence = make_suite_evidence()
            write_report(evidence, args.output_dir)
            for item in evidence["cases"]:
                print(f"{item['description']['title']}: {item['classification']}; "
                      f"buggy={numeric(item['results']['buggy']['actual'])}; "
                      f"fixed={numeric(item['results']['fixed']['actual'])}.")
            print("Демонстрационный режим. Настоящая нейросеть ещё не подключена.")
            print(f"Отчёт: {(args.output_dir / 'report.html').resolve()}")
            return 0 if all(c["hypothesis_confirmed_for_this_input"] for c in evidence["cases"]) else 1
        case = load_case(args.input)
        if args.command == "propose":
            print(json_text(demonstration_proposal(case)), end="")
            return 0
        if args.command == "check":
            if args.proposal:
                with args.proposal.open("rb") as stream:
                    proposal = stream.read(MAX_PROPOSAL_BYTES + 1)
                result = execute_proposal(case, proposal, args.variant, source="offline_supplied")
            else:
                result = evaluate(case, args.variant)
            print(json_text(result), end="")
            return 0 if result["passed"] and result["regression"]["passed"] else 1
        evidence = make_evidence(case)
        write_report(evidence, args.output_dir)
        for variant, result in evidence["results"].items():
            status = "прошёл" if result["passed"] else "не прошёл"
            print(f"{variant}: {numeric(result['actual'])}; эталон "
                  f"{numeric(result['expected'])}; тест {status}; вывод {result['verdict']}.")
        print(f"Статус: {evidence['classification']}")
        print(f"Отчёт: {(args.output_dir / 'report.html').resolve()}")
        print(DISCLAIMER)
        return 0 if evidence["hypothesis_confirmed_for_this_input"] else 1
    except (InputError, ProposalError, OSError) as exc:
        print(f"Ошибка ввода или записи: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
