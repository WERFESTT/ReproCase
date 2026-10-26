"""Один и тот же регрессионный тест для обоих заранее известных вариантов."""

import unittest

from calculations import CALCULATIONS, VARIANTS
from reference import REFERENCES, compare, compare_value, expected_seconds


class DurationRegression(unittest.TestCase):
    """Время должно совпадать с независимым эталоном в пределах допуска."""

    def __init__(self, variant: str, inputs: dict, absolute_tolerance_s: float):
        super().__init__("runTest")
        self.variant = variant
        self.inputs = inputs
        self.absolute_tolerance_s = absolute_tolerance_s

    def runTest(self):
        actual = VARIANTS[self.variant](**self.inputs)
        expected = expected_seconds(**self.inputs)
        result = compare(actual, expected, self.absolute_tolerance_s)
        self.assertTrue(
            result["passed"],
            f"Получено {actual:.12g} с; ожидается {expected:.12g} с; "
            f"допуск {result['allowed_error_s']:.12g} с.",
        )


def run_regression(variant: str, inputs: dict, absolute_tolerance_s: float) -> dict:
    """Выполнить unittest без часов и случайности в сохраняемых результатах."""
    if variant not in VARIANTS:
        raise ValueError("Неизвестный вариант расчёта.")
    result = unittest.TestResult()
    test = DurationRegression(variant, inputs, absolute_tolerance_s)
    test.run(result)
    return {
        "test_id": "regression.DurationRegression.runTest",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "passed": result.wasSuccessful(),
        "details": [details for _, details in result.failures + result.errors],
    }


class ValueRegression(unittest.TestCase):
    """Тот же контракт для новых примеров: совпадение с отдельным эталоном."""

    def __init__(self, operation, variant, inputs, tolerance):
        super().__init__("runTest")
        self.operation, self.variant = operation, variant
        self.inputs, self.tolerance = inputs, tolerance

    def runTest(self):
        actual = CALCULATIONS[self.operation][self.variant](**self.inputs)
        expected = REFERENCES[self.operation](**self.inputs)
        result = compare_value(self.operation, actual, expected, self.tolerance)
        self.assertTrue(result["passed"], f"Получено {actual}; ожидается {expected}.")


def run_case_regression(operation, variant, inputs, tolerance):
    if operation == "travel_time":
        return run_regression(variant, inputs, tolerance)
    result = unittest.TestResult()
    ValueRegression(operation, variant, inputs, tolerance).run(result)
    return {
        "test_id": f"regression.ValueRegression.runTest:{operation}",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "passed": result.wasSuccessful(),
        "details": [details for _, details in result.failures + result.errors],
    }
