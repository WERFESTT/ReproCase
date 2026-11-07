#!/usr/bin/env python3
"""Воспроизводимая оценка обычного метода. Модель здесь не вызывается."""

from localization import print
import localization as loc

import argparse
import copy
import hashlib
import html
import json
import math
from pathlib import Path
import platform

from calculations import CALCULATIONS
from catalog import CATALOG
import benchmark_diagnostics as diagnostics
import reprocase as app


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "benchmark_fixtures.json"
FILES = ("benchmark-report.html", "benchmark-results.json", "benchmark-diagnostics.json",
         "benchmark-truth.json", "benchmark-model-context.json")
LABELS = {"BUG": "Различимая ошибка", "CLEAN": "Исправный результат", "AMBIGUOUS": "Неоднозначно",
          "INSUFFICIENT_DATA": "Недостаточно данных"}


class DatasetError(ValueError):
    pass


def declared_equal(left, right, case):
    """Проверка замороженных ручных численных эталонов, не ответ диагностического метода."""
    if case["operation"] == "seeded_sequence":
        return type(left) is list and type(right) is list and left == right
    absolute = case["check"].get("absolute_tolerance_s", case["check"].get("absolute_tolerance", 0))
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=absolute)


def build_dataset(path=FIXTURES):
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if source.get("version") != "educational-variations-v1" or not source.get("variations"):
        raise DatasetError("Неизвестная или пустая версия набора.")
    public, truth, seen = [], {}, set()
    for item in source["variations"]:
        if set(item) != {"id", "operation", "inputs", "check", "separable", "correct", "faulty"}:
            raise DatasetError("Некорректная запись набора.")
        if type(item["id"]) is not str or item["id"] in seen or type(item["separable"]) is not bool:
            raise DatasetError("Повтор вариации или некорректная ручная метка.")
        seen.add(item["id"])
        operation = item["operation"]
        if operation not in CATALOG:
            raise DatasetError("Неизвестная операция набора.")
        case = app.validate_case({"schema_version": 1, "operation": operation,
                                  "hypothesis": CATALOG[operation]["hypothesis"],
                                  "inputs": item["inputs"], "check": item["check"]})
        if item["separable"] == declared_equal(item["correct"], item["faulty"], case):
            raise DatasetError("Ручная метка различимости противоречит замороженным численным эталонам.")
        for variant in ("buggy", "fixed"):
            sample_id = hashlib.sha256((source["version"] + "|" + item["id"] + "|" + variant).encode()).hexdigest()[:16]
            if sample_id in truth:
                raise DatasetError("Повтор идентификатора наблюдения.")
            observed = CALCULATIONS[operation][variant](**case["inputs"])
            anchor = item["faulty"] if variant == "buggy" else item["correct"]
            if not declared_equal(observed, anchor, case):
                raise DatasetError(f"Расчёт {item['id']} не совпал с заранее сохранённым численным эталоном; оценка остановлена.")
            view = {"schema_version": 1, "sample_id": sample_id, "operation": operation,
                    "task": CATALOG[operation]["task"], "inputs": case["inputs"], "check": case["check"],
                    "observation": observed}
            public.append(diagnostics.validate_view(view))
            truth[sample_id] = {
                "variation": item["id"], "operation": operation, "implementation": variant,
                "label": ("BUG" if variant == "buggy" else "CLEAN") if item["separable"] else "AMBIGUOUS",
                "injected_bug": variant == "buggy", "separable": item["separable"],
                "frozen_correct_result": item["correct"], "frozen_faulty_result": item["faulty"],
            }
    return sorted(public, key=lambda item: item["sample_id"]), truth


def score(diagnoses, truth):
    if len({item["sample_id"] for item in diagnoses}) != len(diagnoses):
        raise DatasetError("Повтор диагностического результата.")
    if {item["sample_id"] for item in diagnoses} != set(truth):
        raise DatasetError("Для оценки нужны все наблюдения ровно по одному разу.")
    result = {
        "samples": len(diagnoses), "detectable_bugs": 0, "clean_samples": 0, "ambiguous_samples": 0,
        "all_injected_bugs": 0, "masked_injected_bugs": 0, "bugs_found": 0, "missed_detectable_bugs": 0,
        "injected_bugs_not_confirmed": 0, "false_positives_clean": 0,
        "unsupported_confirmations_ambiguous": 0, "unsupported_clean_ambiguous": 0,
        "correct_clean_results": 0, "clean_abstentions": 0, "correct_ambiguous_results": 0,
        "protocol_errors": 0, "checks_total": 0,
    }
    counts = []
    for item in diagnoses:
        target = truth[item["sample_id"]]
        verdict, count = item["verdict"], item["checks_run"]
        if verdict not in diagnostics.VERDICTS or type(count) is not int or not 0 <= count <= diagnostics.MAX_CHECKS or count != len(item["history"]):
            raise DatasetError("Некорректный результат или счётчик проверок.")
        result["checks_total"] += count
        counts.append(count)
        result["protocol_errors"] += item["protocol_error"] is not None
        result["all_injected_bugs"] += target["injected_bug"]
        result["masked_injected_bugs"] += target["injected_bug"] and not target["separable"]
        result["injected_bugs_not_confirmed"] += target["injected_bug"] and verdict != "BUG"
        if target["label"] == "BUG":
            result["detectable_bugs"] += 1
            result["bugs_found"] += verdict == "BUG"
            result["missed_detectable_bugs"] += verdict != "BUG"
        elif target["label"] == "CLEAN":
            result["clean_samples"] += 1
            result["false_positives_clean"] += verdict == "BUG"
            result["correct_clean_results"] += verdict == "CLEAN"
            result["clean_abstentions"] += verdict == "INSUFFICIENT_DATA"
        elif target["label"] == "AMBIGUOUS":
            result["ambiguous_samples"] += 1
            result["unsupported_confirmations_ambiguous"] += verdict == "BUG"
            result["unsupported_clean_ambiguous"] += verdict == "CLEAN"
            result["correct_ambiguous_results"] += verdict == "INSUFFICIENT_DATA"
        else:
            raise DatasetError("Неизвестная метка оценщика.")
    result["false_or_unsupported_alarms"] = result["false_positives_clean"] + result["unsupported_confirmations_ambiguous"]
    result["checks_mean"] = result["checks_total"] / len(counts) if counts else None
    result["checks_min"] = min(counts) if counts else None
    result["checks_max"] = max(counts) if counts else None
    result["overall_injected_confirmation_rate"] = result["bugs_found"] / result["all_injected_bugs"] if result["all_injected_bugs"] else None
    return result


def evaluate(public, truth, selector=diagnostics.fixed_order_selector):
    """Одинаковый маршрут для методов. selector получает только публичный контекст."""
    diagnoses = [diagnostics.diagnose(copy.deepcopy(view), selector) for view in public]
    return diagnoses, score(diagnoses, truth)


def create_evaluation():
    public, truth = build_dataset()
    diagnoses, metrics = evaluate(public, truth)
    family_metrics = {}
    for operation in CATALOG:
        partial_truth = {key: value for key, value in truth.items() if value["operation"] == operation}
        family_metrics[operation] = score([item for item in diagnoses if item["sample_id"] in partial_truth], partial_truth)
    source_files = ("benchmark.py", "benchmark_diagnostics.py", "benchmark_fixtures.json",
                    "calculations.py", "reference.py", "catalog.py", "reprocase.py")
    report = {
        "schema_version": 1, "dataset_version": "educational-variations-v1",
        "disclaimer": "Самостоятельно написанные учебные случаи с намеренно внесёнными ошибками.",
        "baseline": {"status": "MEASURED", "name": "fixed_order_v1", "metrics": metrics,
                     "order": list(diagnostics.CHECK_DESCRIPTIONS), "family_metrics": family_metrics, "diagnoses": diagnoses},
        "model": {"status": "NOT_EVALUATED", "metrics": None, "real_api_calls": 0,
                  "reason": "Настоящая модель не запускалась. Искусственные ответы из тестов не считаются её результатами."},
        "observation_runs": len(public), "variations": len(public) // 2, "max_checks_per_sample": diagnostics.MAX_CHECKS,
        "provenance": {"python_version": platform.python_version(),
                       "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
                       "diagnostic_data_sha256": hashlib.sha256(app.json_text(public).encode()).hexdigest(),
                       "scoring_truth_sha256": hashlib.sha256(app.json_text(truth).encode()).hexdigest()},
        "rows": [{"diagnostic": view, "scoring_truth": truth[view["sample_id"]], "result": result}
                 for view, result in zip(public, diagnoses)],
    }
    return report, public, truth


@loc.localized_html
def render_html(report):
    m = report["baseline"]["metrics"]
    metric_rows = [
        ("Наблюдений", m["samples"]),
        ("Подтверждено среди всех намеренных ошибок", f"{m['bugs_found']} из {m['all_injected_bugs']}"),
        ("Найдено различимых ошибок (условная метрика)", f"{m['bugs_found']} из {m['detectable_bugs']}"),
        ("Пропуски различимых ошибок", m["missed_detectable_bugs"]),
        ("Ложные срабатывания на исправных вводах", m["false_positives_clean"]),
        ("Необоснованные заявления об ошибке на неоднозначных вводах", m["unsupported_confirmations_ambiguous"]),
        ("Исправных результатов распознано", f"{m['correct_clean_results']} из {m['clean_samples']}"),
        ("Верное «недостаточно данных»", f"{m['correct_ambiguous_results']} из {m['ambiguous_samples']}"),
        ("Внесённых ошибок всего, включая неразличимые", m["all_injected_bugs"]),
        ("Внесённых ошибок без подтверждения", m["injected_bugs_not_confirmed"]),
        ("Выполнено разрешённых проверок", m["checks_total"]),
        ("Проверок на наблюдение в среднем", m["checks_mean"]),
        ("Ошибки протокола метода", m["protocol_errors"]),
    ]
    summary = "".join(f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td><td>Не проверена</td></tr>" for label, value in metric_rows)
    rows = []
    for row in report["rows"]:
        view, target, result = row["diagnostic"], row["scoring_truth"], row["result"]
        rows.append(
            f"<tr data-sample='{view['sample_id']}'><th>{view['sample_id']}</th>"
            f"<td>{html.escape(CATALOG[view['operation']]['title'])}</td>"
            f"<td>{html.escape(json.dumps(view['inputs'], ensure_ascii=False))}</td>"
            f"<td>{html.escape(app.numeric(view['observation']))}</td><td>{LABELS[target['label']]}</td>"
            f"<td>{LABELS[result['verdict']]}</td><td>{result['checks_run']}</td></tr>"
        )
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>ReproCase — честная оценка обычного метода</title><style>
body{{font:17px/1.6 system-ui,sans-serif;color:#213343;background:#f3f6f8;margin:0}}main{{max-width:1180px;padding:30px 20px;margin:auto}}
h1{{font-size:34px;line-height:1.2}}h2{{font-size:25px}}section,.notice{{background:white;border:1px solid #d6e1e8;border-radius:12px;padding:22px;margin:18px 0}}
.notice{{background:#fff3df;border-left:6px solid #b96713}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{text-align:left;vertical-align:top;border-bottom:1px solid #dce5eb;padding:10px}}thead{{background:#edf2f5}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 monospace;background:#edf2f5;padding:16px}}a{{color:#005c83}}code{{overflow-wrap:anywhere}}
</style></head><body><main><h1>Что показывает обычный подбор проверок</h1>
<div class="notice"><strong>Настоящая модель не проверена.</strong> Здесь измерен только обычный метод с фиксированным порядком проверок. Улучшение от ИИ не заявляется.</div>
<p>{html.escape(report['disclaimer'])}</p>
<section><h2>Общий итог: найдено {m['bugs_found']} из {m['all_injected_bugs']} намеренно неисправных случаев</h2>
<p>Ещё {m['masked_injected_bugs']} неразличимы на выбранных входах. Условный результат {m['bugs_found']} из {m['detectable_bugs']} различимых ошибок нельзя читать как 100% общей полноты.</p></section>
<section><h2>Измеренные результаты</h2><div class="scroll"><table><thead><tr><th>Показатель</th><th>Обычный метод</th><th>Будущая модель</th></tr></thead><tbody>{summary}</tbody></table></div>
<p>Из {m['all_injected_bugs']} намеренно неисправных запусков {m['masked_injected_bugs']} не отличаются от исправного поведения в пределах заданного допуска.
Они показаны отдельно и не скрыты под формулировкой «пропусков нет». Пропуском различимой ошибки считается любой вывод, кроме «ошибка», на метке оценщика BUG.</p>
<p>Число проверок — число вызовов разрешённых диагностических операций, а не время CPU, число API-запросов или цена. Для подготовки наблюдений отдельно выполнено {report['observation_runs']} коротких учебных расчётов.</p></section>
<section><h2>Как устроена оценка</h2><p>{report['variations']} вариации входных данных × два заранее написанных варианта = {report['observation_runs']} наблюдений.
Обычный метод сначала проверяет различимость ввода, затем соответствие правилу задачи, затем сигнатуру известной ошибки. Он останавливается после обоснованного вывода и делает не больше трёх проверок.</p>
<p>Диагностика видит тип и правило задачи, входные числа, допуск и наблюдаемый результат. Она не видит buggy/fixed, эталонную метку, численный эталон оценщика или готовое правильное предложение.
Численные результаты разрешённых проверок появляются только после их выполнения. Оценщик хранит ручные замороженные ответы отдельно и сравнивает с ними вывод метода.</p>
<p>Это логическое разделение данных внутри программы, не файловая песочница. Идентификаторы непрозрачны, порядок фиксирован и перемешан по идентификаторам.
Набор открыт и мал; это не скрытый тест на обобщение. По одному известному типу ошибки на задачу — обычный метод здесь силён. Эти результаты не доказывают пользу модели или работу на произвольном коде.</p>
<p>Прежний запрос с <code>allowed_proposal</code> предназначен только для проверки транспорта. Он не используется в этой оценке, потому что содержит готовую подсказку.</p></section>
<section><h2>Все наблюдения и выводы</h2><div class="scroll"><table><thead><tr><th>ID</th><th>Задача</th><th>Ввод</th><th>Наблюдение</th><th>Отдельный эталон оценщика</th><th>Вывод метода</th><th>Проверок</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section><h2>Повторить и проверить</h2><pre>python3 benchmark.py
python3 verify.py --skip-gui</pre>
<p><a href="benchmark-results.json">Полные измерения и трассы</a> · <a href="benchmark-diagnostics.json">Данные для диагностики</a> · <a href="benchmark-truth.json">Отдельные эталоны оценщика</a> · <a href="benchmark-model-context.json">Контекст будущей модели без ответа</a> · <a href="../BENCHMARK.md">Инструкция и определения</a> · <a href="report.html">Три исходных примера</a></p>
<p>Реальных API-вызовов: 0. Визуальная оценка HTML не выполнялась; прежний запрет file:// не повторялся и не обходился.</p>
<p>Клиент уже адаптирован к этому же маршруту; <a href="benchmark-adapter-synthetic.json">доказательства синтетической проверки адаптера</a> не являются результатами модели.
Следующий шаг — отдельно разрешённая настоящая проба с аккаунтом, ключом и согласованными расходами.</p></section>
<section><details><summary>Фактические результаты JSON</summary><pre id="benchmark-json">{html.escape(app.json_text(report))}</pre></details></section>
</main></body></html>"""


def write_evaluation(output_dir=ROOT / "outputs"):
    report, public, truth = create_evaluation()
    files = {
        "benchmark-report.html": render_html(report),
        "benchmark-results.json": app.json_text(report),
        "benchmark-diagnostics.json": app.json_text(public),
        "benchmark-truth.json": app.json_text(truth),
        "benchmark-model-context.json": app.json_text(diagnostics.model_context(public[0], [])),
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (output_dir / name).write_text(text, encoding="utf-8")
    return report


def main(argv=None):
    argv = loc.cli_args(argv)
    parser = loc.ArgumentParser(description="Измерить обычный диагностический метод на учебном наборе.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    args = parser.parse_args(argv)
    report = write_evaluation(args.output_dir)
    m = report["baseline"]["metrics"]
    print(f"Наблюдений: {m['samples']}; найдено среди всех намеренных ошибок: {m['bugs_found']}/{m['all_injected_bugs']}; неразличимы: {m['masked_injected_bugs']}.")
    print(f"Условная метрика среди различимых: {m['bugs_found']}/{m['detectable_bugs']}; это не 100% общей полноты.")
    print(f"Ложных/необоснованных тревог: {m['false_or_unsupported_alarms']}; пропусков различимых ошибок: {m['missed_detectable_bugs']}.")
    print(f"Неоднозначных вводов: {m['ambiguous_samples']}; неразличимых внесённых ошибок: {m['masked_injected_bugs']}.")
    print(f"Проверок: {m['checks_total']}; в среднем: {m['checks_mean']}. Модель: НЕ ПРОВЕРЕНА.")
    print(args.output_dir / "benchmark-report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
