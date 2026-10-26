"""Закрытый перечень учебных задач и разрешённых проверок. Это не плагины."""

CATALOG = {
    "travel_time": {
        "case_id": "units", "hypothesis": "millimetres_used_as_metres",
        "check_id": "independent_mm_oracle", "file": "unit_mismatch.json",
        "title": "Метры и миллиметры",
        "task": "Найти время прохождения заданного пути при постоянной скорости.",
        "hypothesis_text": "Миллиметры использованы как метры, поэтому время завышено в 1000 раз.",
        "reference_text": "Отдельный эталон переводит скорость в мм/с и делит путь на неё через Decimal. Исправный вариант переводит путь в метры.",
        "not_proven": "Не проверено поведение расчёта вне заданного учебного правила.",
        "unit": "с",
    },
    "seeded_sequence": {
        "case_id": "seed", "hypothesis": "configured_seed_ignored",
        "check_id": "closed_form_seed_oracle", "file": "seed_mismatch.json",
        "title": "Начальное значение генератора",
        "task": "Получить count чисел по правилу x[n+1] = (5·x[n]+1) mod 16, начиная с seed.",
        "hypothesis_text": "Настройка seed проигнорирована: расчёт всегда начинает с нуля.",
        "reference_text": "Эталон вычисляет каждый член напрямую: (5ⁿ·seed + (5ⁿ−1)/4) mod 16. Он не вызывает рекуррентный генератор. При seed=3 первые пять чисел: [0, 1, 6, 15, 12].",
        "not_proven": "Не проверено качество случайности. Это маленький учебный генератор, непригодный для криптографии.",
        "unit": "",
    },
    "constant_rate_steps": {
        "case_id": "step", "hypothesis": "step_denominator_off_by_one",
        "check_id": "analytic_constant_rate_oracle", "file": "step_mismatch.json",
        "title": "Ошибка величины шага",
        "task": "Выполнить N обновлений x ← x + rate·h за длительность T. Правильный шаг h = T/N.",
        "hypothesis_text": "Выполнено N обновлений, но шаг взят как T/(N−1); расчёт проходит лишнее время.",
        "reference_text": "Эталон без цикла считает initial + rate·T через Decimal. Пошаговые варианты используют точные дроби, чтобы ошибка округления не скрывала ошибку шага.",
        "not_proven": "Проверена только постоянная скорость изменения. Не доказана точность метода для произвольных уравнений.",
        "unit": "условных единиц",
    },
}

VERDICTS = {
    "CONFIRMED": "Подтверждено",
    "REFUTED": "Опровергнуто на этом вводе",
    "INSUFFICIENT_DATA": "Недостаточно данных",
}
