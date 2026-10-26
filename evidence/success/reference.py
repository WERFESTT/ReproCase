"""Независимый эталон: перевод скорости в мм/с, без импорта расчётов."""

import math
from decimal import Decimal, localcontext


RELATIVE_TOLERANCE = 1e-12


def expected_seconds(distance_mm: float, speed_m_s: float) -> float:
    """Путь в мм / скорость в мм/с. Десятичная арифметика для эталона."""
    with localcontext() as context:
        context.prec = 40
        speed_mm_s = Decimal(str(speed_m_s)) * Decimal("1000")
        return float(Decimal(str(distance_mm)) / speed_mm_s)


def expected_sequence(seed: int, count: int) -> list:
    """Прямая формула, без повторения рекуррентного цикла генератора."""
    return [(pow(5, n) * seed + (pow(5, n) - 1) // 4) % 16 for n in range(1, count + 1)]


def expected_integral(initial: float, rate: float, duration_s: float, steps: int) -> float:
    """Точный итог при постоянной производной; от числа шагов не зависит."""
    with localcontext() as context:
        context.prec = 40
        return float(Decimal(str(initial)) + Decimal(str(rate)) * Decimal(str(duration_s)))


REFERENCES = {
    "travel_time": expected_seconds,
    "seeded_sequence": expected_sequence,
    "constant_rate_steps": expected_integral,
}


def predicted_fault(operation: str, inputs: dict):
    """Независимо предсказать следствие ровно заданной учебной причины."""
    if operation == "travel_time":
        return expected_seconds(**inputs) * 1000
    if operation == "seeded_sequence":
        return expected_sequence(0, inputs["count"])
    if operation == "constant_rate_steps":
        with localcontext() as context:
            context.prec = 40
            n = Decimal(inputs["steps"])
            total = Decimal(str(inputs["initial"])) + (
                Decimal(str(inputs["rate"])) * Decimal(str(inputs["duration_s"])) * n / (n - 1)
            )
            return float(total)
    raise ValueError("Неизвестная операция.")


def compare_value(operation: str, actual, expected, tolerance: float) -> dict:
    if operation == "seeded_sequence":
        valid = type(actual) is list and all(type(item) is int for item in actual)
        return {"actual": actual, "expected": expected, "passed": valid and actual == expected,
                "absolute_error": None, "allowed_error": 0}
    result = compare(actual, expected, tolerance)
    return {"actual": result["actual_s"], "expected": result["expected_s"],
            "passed": result["passed"], "absolute_error": result["absolute_error_s"],
            "allowed_error": result["allowed_error_s"]}


def compare(actual_s: float, expected_s: float, absolute_tolerance_s: float) -> dict:
    """Сравнение результата с эталоном; NaN и бесконечности не проходят."""
    allowed_error = max(absolute_tolerance_s, abs(expected_s) * RELATIVE_TOLERANCE)
    finite = math.isfinite(actual_s) and math.isfinite(expected_s)
    error = abs(actual_s - expected_s) if finite else None
    return {
        "actual_s": actual_s if math.isfinite(actual_s) else None,
        "expected_s": expected_s if math.isfinite(expected_s) else None,
        "absolute_error_s": error,
        "allowed_error_s": allowed_error,
        "passed": finite and error <= allowed_error,
    }
