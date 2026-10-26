"""Закрытые операции над точными историческими исходниками. Без сети и Hypothesis."""

import functools
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

import reprocase as app

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "historical_vendor"
CASE_IDS = ("cpython_correlation", "more_reversed")
VERSIONS = ("before", "after")
PLAN_SHA256 = "af81d9a69a34848f39d72404ac4fcb67554bac16a8b6b792d430a13dc27fd2cc"


class HarnessError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=app.unique_object,
                      parse_constant=app.reject_constant)


def verify_sources():
    manifest = read_json(VENDOR / "manifest.json")
    for name, record in manifest["files"].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(VENDOR) or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise HarnessError("Исторические исходники не совпадают с зафиксированными файлами.")
    return manifest


def plan():
    path = ROOT / "historical_plan.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != PLAN_SHA256:
        raise HarnessError("План пилота изменился после фиксации.")
    return read_json(path)


def validate_input(case_id, value):
    if case_id not in CASE_IDS or type(value) not in (list, tuple):
        raise HarnessError("Неизвестная задача или формат входа.")
    bounds = plan()["domains"][case_id]
    if len(value) != len(bounds) or any(type(x) is not int or not lo <= x <= hi for x, (lo, hi) in zip(value, bounds)):
        raise HarnessError("Ввод вне фиксированной области.")
    if case_id == "more_reversed" and value[2] == 0:
        raise HarnessError("Нулевой шаг не входит в допустимые данные.")
    return tuple(value)


@functools.lru_cache(maxsize=4)
def load_version(case_id, version):
    if case_id not in CASE_IDS or version not in VERSIONS:
        raise HarnessError("Неизвестная версия или операция.")
    verify_sources()
    folder = VENDOR / case_id / version
    if case_id == "cpython_correlation":
        source = folder / "Lib/statistics.py"
        locations = None
    else:
        source = folder / "more_itertools/__init__.py"
        locations = [str(source.parent)]
    name = "_reprocase_historical_" + case_id + "_" + version
    spec = importlib.util.spec_from_file_location(name, source, submodule_search_locations=locations)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(name, None)
        raise HarnessError("Исторический модуль не загрузился: " + type(error).__name__) from None
    return module


def encoded_number(value):
    if type(value) is float and not math.isfinite(value):
        return {"nonfinite": "nan" if math.isnan(value) else "infinity" if value > 0 else "-infinity"}
    return value


def check(case_id, version, value):
    value = validate_input(case_id, value)
    module = load_version(case_id, version)  # Ошибка установки не считается находкой.
    if case_id == "cpython_correlation":
        a, b = (math.ldexp(1.0, exponent) for exponent in value)
        inputs = {"x": [-a, a], "y": [-b, b]}
        expected = 1.0  # Две ненулевые пропорциональные симметричные точки.
        call = lambda: module.correlation(inputs["x"], inputs["y"])
        known_exceptions = (module.StatisticsError, ValueError, OverflowError, ZeroDivisionError)
    else:
        inputs = {"start": value[0], "stop": value[1], "step": value[2]}
        expected = list(reversed(range(*value)))
        call = lambda: list(reversed(module.numeric_range(*value)))
        known_exceptions = (IndexError,)
    try:
        actual = call()
    except known_exceptions as error:
        tb = error.__traceback__
        reached_source = False
        while tb is not None:
            reached_source |= Path(tb.tb_frame.f_code.co_filename).is_relative_to(VENDOR / case_id / version)
            tb = tb.tb_next
        if not reached_source:
            raise HarnessError("Исключение возникло вне проверяемых исторических исходников.") from None
        return {"status": "SUT_EXCEPTION", "inputs": inputs, "expected": expected,
                "actual": None, "exception_type": type(error).__name__, "exception": str(error)}
    except Exception as error:
        raise HarnessError("Неожиданное исключение проверяющего контура: " + type(error).__name__) from None
    passed = (type(actual) is float and math.isfinite(actual) and abs(actual - 1.0) <= 1e-12
              if case_id == "cpython_correlation" else actual == expected)
    return {"status": "PASS" if passed else "VALUE_MISMATCH", "inputs": inputs,
            "expected": expected, "actual": encoded_number(actual), "exception_type": None, "exception": None}


def assert_contract(case_id, version, value):
    result = check(case_id, version, value)
    assert result["status"] == "PASS", app.json_text(result)
    return result


def size(value):
    return {"integer_fields": len(value), "sum_abs": sum(abs(x) for x in value),
            "json_bytes": len(json.dumps(list(value), separators=(",", ":")).encode())}
