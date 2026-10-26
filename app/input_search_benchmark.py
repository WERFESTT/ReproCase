#!/usr/bin/env python3
"""Отдельный оценщик ограниченного поиска. Замороженные ответы не идут селектору."""

import copy
import hashlib
import html
import json

import benchmark
from catalog import CATALOG
import input_search as search
import reprocase as app

ROOT = app.ROOT
FILES = ("input-search-results.json", "input-search-truth.json", "input-search-context.json", "input-search-report.html")


def load_manifest():
    manifest = json.loads((ROOT / "input_search_evaluation.json").read_text())
    if type(manifest) is not dict or manifest.get("version") != "input-search-evaluation-v1":
        raise search.SearchError("Неизвестная версия схемы оценки.")
    for name, field in (("input_search_plan.json", "plan_sha256"), ("benchmark_fixtures.json", "fixtures_sha256")):
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != manifest[field]:
            raise search.SearchError("План или контрольный набор изменён после фиксации; оценка остановлена.")
    fixtures = json.loads((ROOT / "benchmark_fixtures.json").read_text())["variations"]
    tuning, control = manifest["tuning"], manifest["control"]
    if (len(tuning) != 6 or len(control) != 18 or set(tuning) & set(control)
            or len(set(tuning + control)) != 24 or set(tuning + control) != {x["id"] for x in fixtures}):
        raise search.SearchError("Настроечная и контрольная части должны быть раздельными и полными.")
    return manifest, {item["id"]: item for item in fixtures}


def score(records, truth):
    if len({r["id"] for r in records}) != len(records) or {r["id"] for r in records} != set(truth):
        raise search.SearchError("Нужны все исходы ровно по одному разу.")
    counts = {"samples": len(records), "injected_defects": 0, "reproduced_defects": 0,
              "initially_reproduced_defects": 0, "recovered_by_new_input": 0,
              "unreproduced_defects": 0, "false_alarms": 0, "initially_unresolved_defects": 0,
              "unresolved_results": 0, "initial_runs": 0, "probe_attempts": 0,
              "unsuccessful_probe_attempts": 0, "contract_checks": 0, "protocol_errors": 0, "operation_errors": 0}
    for record in records:
        result, target = record["result"], truth[record["id"]]
        if (result["status"] not in ("REPRODUCED", "INSUFFICIENT_DATA")
                or result["probe_attempts"] != len(result["attempts"])
                or not 0 <= result["probe_attempts"] <= search.MAX_TRIALS):
            raise search.SearchError("Некорректный исход или число проб.")
        injected, found = target["injected_defect"], result["status"] == "REPRODUCED"
        initial_found = result["initial_status"] == "REPRODUCED"
        counts["injected_defects"] += injected
        counts["reproduced_defects"] += injected and found
        counts["initially_reproduced_defects"] += injected and initial_found
        counts["recovered_by_new_input"] += injected and found and not initial_found
        counts["initially_unresolved_defects"] += injected and not initial_found
        counts["unreproduced_defects"] += injected and not found
        counts["false_alarms"] += not injected and found
        counts["unresolved_results"] += not found
        for key in ("initial_runs", "probe_attempts", "contract_checks"):
            counts[key] += result[key]
        counts["unsuccessful_probe_attempts"] += sum(t["status"] != "REPRODUCED" for t in result["attempts"])
        counts["operation_errors"] += sum(t["error"] is not None for t in result["attempts"])
        counts["protocol_errors"] += result["stop_reason"] in ("REJECTED_ACTION", "SELECTOR_ERROR")
    counts["reproduced_fraction_of_all_defects"] = counts["reproduced_defects"] / counts["injected_defects"] if counts["injected_defects"] else None
    counts["total_target_runs"] = counts["initial_runs"] + counts["probe_attempts"]
    return counts


def evaluate(selector=search.fixed_order_selector):
    manifest, fixtures = load_manifest()  # До запуска обеих частей; план не подстраивается по результатам.
    records, truth, contexts = [], {}, []
    plan, _ = search.load_plan()
    for split in ("tuning", "control"):
        for identifier in manifest[split]:
            item = fixtures[identifier]
            case = search.make_case(item["operation"], item["inputs"], item["check"])
            for variant in ("buggy", "fixed"):
                def select(context):
                    if not contexts:
                        contexts.append(copy.deepcopy(context))
                    return selector(context)
                result = search.run_search(case, variant, select)
                anchor = item["faulty"] if variant == "buggy" else item["correct"]
                if (not benchmark.declared_equal(result["initial"]["observation"], anchor, case)
                        or not benchmark.declared_equal(result["initial_check"]["expected"], item["correct"], case)):
                    raise search.SearchError("Исходное наблюдение или контроль не совпал с замороженным ответом.")
                for trial in result["attempts"]:
                    if trial["check"] is None:
                        continue
                    frozen = manifest["anchors"][item["operation"]][trial["candidate_id"]]
                    if (not benchmark.declared_equal(trial["check"]["expected"], frozen["correct"], case)
                            or not benchmark.declared_equal(trial["check"]["actual"], frozen["faulty" if variant == "buggy" else "correct"], case)):
                        raise search.SearchError("Новая проба или контроль не совпал с заранее записанным ответом.")
                run_id = hashlib.sha256(("input-search-v1|" + identifier + "|" + variant).encode()).hexdigest()[:16]
                records.append({"id": run_id, "split": split, "result": result})
                truth[run_id] = {"variation": identifier, "injected_defect": variant == "buggy", "implementation": variant}
    by_split = {split: score([r for r in records if r["split"] == split],
                            {r["id"]: truth[r["id"]] for r in records if r["split"] == split}) for split in ("tuning", "control")}
    return {"version": "input-search-evaluation-v1", "method": "fixed_candidates_v1", "primary_split": "control",
            "metrics": by_split, "all_samples_for_reference": score(records, truth), "runs": records,
            "model": {"status": "NOT_EVALUATED", "metrics": None, "real_api_calls": 0},
            "max_trials": search.MAX_TRIALS, "plan_sha256": manifest["plan_sha256"],
            "source_sha256": {**search.source_hashes(), **{name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                             for name in ("input_search_benchmark.py", "input_search_evaluation.json", "benchmark_fixtures.json", "benchmark.py")}}}, truth, contexts[0]


def write_evaluation(output_dir=ROOT / "outputs"):
    directory = search.local_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report, truth, context = evaluate()
    commands, replayed = {}, 0
    for record in report["runs"]:
        if record["result"]["status"] == "REPRODUCED":
            path, command = search.save_reproducer(record["result"], truth[record["id"]]["implementation"], directory / "input-search-reproducers")
            assert search.replay_file(path)["status"] == "REPRODUCED"
            commands[record["id"]] = command
            replayed += 1
    report["reproducers"] = commands
    report["saved_replays_passed"] = replayed
    rows = []
    for split, label in (("control", "Контрольная часть — основной результат"), ("tuning", "Настроечная часть")):
        m = report["metrics"][split]
        rows.append(f"<tr><td>{label}</td><td>{m['samples']}</td><td>{m['reproduced_defects']} из {m['injected_defects']}</td>"
                    f"<td>{m['recovered_by_new_input']}</td><td>{m['false_alarms']}</td><td>{m['probe_attempts']}</td><td>{m['unsuccessful_probe_attempts']}</td></tr>")
    overall = report["all_samples_for_reference"]
    body = ("<p>Заранее фиксированы четыре кандидата на класс и максимум три новые пробы. Настроечные входы и контрольные входы разделены. "
            "Разделены только стартовые входы: кандидаты проб и реализации общие. "
            "Кандидат c04 задачи времени совпадает со стартовым входом u6 контрольной части. "
            "Этот открытый учебный набор не является независимым закрытым тестом новых типов ошибок.</p>"
            "<table><tr><th>Часть</th><th>Запусков</th><th>Воспроизведено от всех внесённых дефектов</th><th>Найдено новым вводом</th>"
            "<th>Ложных тревог</th><th>Новых проб</th><th>Без воспроизведения</th></tr>" + "".join(rows) + "</table>"
            f"<p>Весь набор, справочно: {overall['reproduced_defects']} из {overall['injected_defects']} намеренных дефектов; "
            f"исходно видны {overall['initially_reproduced_defects']}, новым вводом выявлены ещё {overall['recovered_by_new_input']}. "
            f"Исходных запусков: {overall['initial_runs']}; новых попыток: {overall['probe_attempts']}. "
            f"Все {replayed} сохранённых воспроизведения повторены отдельно; эти повторы не входят в бюджет поиска.</p>"
            "<p>Если расхождение не найдено, исправность не доказана. Все попытки, отказы и неудачные исходы сохранены. "
            "Точная причина дефекта не устанавливается: доказано только нарушение правила задачи.</p>"
            f"<p>SHA-256 заранее заданного плана: <code>{report['plan_sha256']}</code>.</p>"
            "<p><a href='../INPUT_SEARCH.md'>Методика и ограничения</a> · <a href='input-search-results.json'>Все исходы и метрики</a> · "
            "<a href='input-search-context.json'>Контекст селектора без эталона оценщика</a></p>")
    for record in report["runs"]:
        target = truth[record["id"]]
        label = "намеренно ошибочный" if target["injected_defect"] else "исправный учебный"
        title = CATALOG[record["result"]["initial"]["operation"]]["title"]
        body += (f"<details><summary>{html.escape(title)} — {record['split']} — {label} — {record['id']}</summary>"
                 + search.render_result(record["result"], commands.get(record["id"])) + "</details>")
    for filename, value in ((FILES[0], report), (FILES[1], truth), (FILES[2], context)):
        (directory / filename).write_text(app.json_text(value), encoding="utf-8")
    (directory / FILES[3]).write_text(search.html_page("ReproCase — поиск воспроизводящего входа", body), encoding="utf-8")
    return report


def main():
    report = write_evaluation()
    print("Реально измерен только обычный метод; настоящая модель не проверена.")
    for split in ("control", "tuning"):
        m = report["metrics"][split]
        print(f"{split}: воспроизведено {m['reproduced_defects']}/{m['injected_defects']}; ложных тревог {m['false_alarms']}; новых проб {m['probe_attempts']}.")
    print(ROOT / "outputs/input-search-report.html")


if __name__ == "__main__":
    main()
