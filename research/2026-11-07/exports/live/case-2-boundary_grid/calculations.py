"""Три учебных случая. Все ошибки в функциях buggy внесены намеренно."""

from fractions import Fraction


def buggy_travel_time(distance_mm: float, speed_m_s: float) -> float:
    """Намеренная ошибка: число миллиметров используется как число метров."""
    return distance_mm / speed_m_s


def fixed_travel_time(distance_mm: float, speed_m_s: float) -> float:
    """Перевести путь в метры, затем вычислить время в секундах."""
    distance_m = distance_mm / 1000.0
    return distance_m / speed_m_s


# Только эти заранее написанные функции может вызвать проверяющий контур.
VARIANTS = {"buggy": buggy_travel_time, "fixed": fixed_travel_time}


def fixed_sequence(seed: int, count: int) -> list:
    """Учебный генератор x[n+1] = (5*x[n]+1) mod 16; не для криптографии."""
    state = seed
    values = []
    for _ in range(count):
        state = (5 * state + 1) % 16
        values.append(state)
    return values


def buggy_sequence(seed: int, count: int) -> list:
    """Намеренно игнорирует настройку seed и всегда начинает с нуля."""
    return fixed_sequence(0, count)


def _integrate(initial: float, rate: float, duration_s: float, steps: int, denominator: int) -> float:
    """Пошаговое суммирование с точными дробями: изолируем именно ошибку шага."""
    value = Fraction(str(initial))
    increment = Fraction(str(rate)) * Fraction(str(duration_s)) / denominator
    for _ in range(steps):
        value += increment
    return float(value)


def fixed_integration(initial: float, rate: float, duration_s: float, steps: int) -> float:
    return _integrate(initial, rate, duration_s, steps, steps)


def buggy_integration(initial: float, rate: float, duration_s: float, steps: int) -> float:
    """Намеренная ошибка: N обновлений, но длительность делится на N-1."""
    return _integrate(initial, rate, duration_s, steps, steps - 1)


CALCULATIONS = {
    "travel_time": VARIANTS,
    "seeded_sequence": {"buggy": buggy_sequence, "fixed": fixed_sequence},
    "constant_rate_steps": {"buggy": buggy_integration, "fixed": fixed_integration},
}
