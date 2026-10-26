#!/usr/bin/env python3
"""Ограниченный офлайн-поиск входа. Ответ селектора никогда не является кодом."""

from localization import print
import localization as loc

import argparse
import copy
import hashlib
import html
import json
import math
from pathlib import Path
import shlex

from calculations import CALCULATIONS
from catalog import CATALOG
from reference import REFERENCES, compare_value
import benchmark_diagnostics as diagnostic
import reprocase as app

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / "input_search_plan.json"
MAX_TRIALS = 3
SOURCE_FILES = ("input_search.py", "input_search_plan.json", "calculations.py", "reference.py",
                "catalog.py", "reprocase.py", "benchmark_diagnostics.py")


class SearchError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(app.json_text(value).encode()).hexdigest()


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def local_path(path):
    result = Path(path).resolve()
    if not result.is_relative_to(ROOT):
        raise SearchError("Для этого маршрута разрешены только файлы внутри ReproCase.")
    return result


def make_case(operation, inputs, check):
    if type(operation) is not str or operation not in CATALOG:
        raise SearchError("Неизвестная учебная операция.")
    return app.validate_case({"schema_version": 1, "operation": operation,
                              "hypothesis": CATALOG[operation]["hypothesis"], "inputs": inputs, "check": check})


def load_plan():
    raw = PLAN.read_bytes()
    plan = json.loads(raw, object_pairs_hook=app.unique_object, parse_constant=app.reject_constant)
    if (set(plan) != {"version", "max_trials", "candidates"} or plan["version"] != "bounded-input-search-v1"
            or type(plan["max_trials"]) is not int or plan["max_trials"] != MAX_TRIALS
            or set(plan["candidates"]) != set(CATALOG)):
        raise SearchError("План поиска изменяет версию, операции или бюджет.")
    for operation, entries in plan["candidates"].items():
        check = app.load_case(ROOT / "examples" / CATALOG[operation]["file"])["check"]
        if type(entries) is not list or len(entries) != 4:
            raise SearchError("В плане нужны четыре заранее заданных входа на класс.")
        for index, entry in enumerate(entries, 1):
            if set(entry) != {"id", "inputs"} or entry["id"] != f"c{index:02}":
                raise SearchError("Некорректный идентификатор кандидата.")
            entry["inputs"] = make_case(operation, entry["inputs"], check)["inputs"]
        if len({digest(entry["inputs"]) for entry in entries}) != 4:
            raise SearchError("В плане повторяются входные данные.")
    return plan, hashlib.sha256(raw).hexdigest()


def observe(case, variant):
    """Только одна из уже написанных функций; variant не передаётся селектору."""
    case = app.validate_case(case)
    if type(variant) is not str or variant not in ("buggy", "fixed"):
        raise SearchError("Неизвестный вариант учебного расчёта.")
    operation = case["operation"]
    view = {"schema_version": 1, "operation": operation, "task": CATALOG[operation]["task"],
            "inputs": case["inputs"], "check": case["check"],
            "observation": CALCULATIONS[operation][variant](**case["inputs"])}
    view["sample_id"] = digest(view)[:16]
    return diagnostic.validate_view(view)


def check_contract(view):
    """Независимый расчёт по правилу задачи, без сигнатуры внесённого дефекта."""
    operation = view["operation"]
    tolerance = view["check"].get("absolute_tolerance_s", view["check"].get("absolute_tolerance", 0))
    return compare_value(operation, view["observation"], REFERENCES[operation](**view["inputs"]), tolerance)


def action_schema(candidates):
    common = {"session_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
    return {"oneOf": [
        {"type": "object", "additionalProperties": False,
         "required": ["action", "candidate_id", "session_sha256"],
         "properties": dict(common, action={"const": "try_input"}, candidate_id={"enum": [c["id"] for c in candidates]})},
        {"type": "object", "additionalProperties": False, "required": ["action", "session_sha256"],
         "properties": dict(common, action={"const": "finish"})},
    ]}


def model_context(result, candidates):
    public = {"initial": result["initial"], "initial_check": result["initial_check"],
              "history": result["attempts"], "candidates": candidates,
              "plan_sha256": result["plan_sha256"], "max_trials": MAX_TRIALS,
              "remaining_trials": MAX_TRIALS - len(result["attempts"])}
    public["session_sha256"] = digest(public)
    public["action_schema"] = action_schema(candidates)
    public["instruction"] = ("Выберите один разрешённый новый вход или завершите поиск. "
                             "Тип дефекта и вариант реализации не переданы. "
                             "Нельзя добавлять команды, код, пути, входы или менять допуск. "
                             "Совпадение в конечном наборе проб не доказывает исправность.")
    return copy.deepcopy(public)


def fixed_order_selector(context):
    tried = {item["candidate_id"] for item in context["history"]}
    if context["remaining_trials"]:
        for candidate in context["candidates"]:
            if candidate["id"] not in tried and candidate["inputs"] != context["initial"]["inputs"]:
                return {"action": "try_input", "candidate_id": candidate["id"],
                        "session_sha256": context["session_sha256"]}
    return {"action": "finish", "session_sha256": context["session_sha256"]}


def validate_action(proposal, context):
    try:
        if type(proposal) is str:
            if len(proposal.encode()) > 4096:
                raise SearchError("Предложение больше 4096 байт.")
            proposal = json.loads(proposal, object_pairs_hook=app.unique_object, parse_constant=app.reject_constant)
        if type(proposal) is not dict or proposal.get("action") not in ("try_input", "finish"):
            raise SearchError("Неизвестное действие поиска.")
        fields = {"action", "session_sha256"} | ({"candidate_id"} if proposal["action"] == "try_input" else set())
        if set(proposal) != fields or proposal["session_sha256"] != context["session_sha256"]:
            raise SearchError("Лишние поля или устаревшее предложение.")
        if proposal["action"] == "try_input":
            candidates = {c["id"]: c for c in context["candidates"]}
            identifier = proposal["candidate_id"]
            if type(identifier) is not str or identifier not in candidates:
                raise SearchError("Неизвестный кандидат.")
            if context["remaining_trials"] <= 0:
                raise SearchError("Лимит проб исчерпан.")
            if (any(t["candidate_id"] == identifier for t in context["history"])
                    or candidates[identifier]["inputs"] == context["initial"]["inputs"]):
                raise SearchError("Повтор уже наблюдавшегося входа запрещён.")
        return dict(proposal)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise SearchError(str(error) if isinstance(error, SearchError) else "Предложение не прошло локальную проверку.") from None


def run_search(case, variant, selector=fixed_order_selector):
    plan, plan_hash = load_plan()
    initial = observe(case, variant)
    initial_check = check_contract(initial)
    initial_status = "INSUFFICIENT_DATA" if initial_check["passed"] else "REPRODUCED"
    result = {"schema_version": 1, "initial": initial, "initial_check": initial_check,
              "initial_status": initial_status, "status": initial_status, "plan_sha256": plan_hash,
              "attempts": [], "decisions": [], "proof": None, "stop_reason": None,
              "initial_runs": 1, "probe_attempts": 0, "contract_checks": 1}
    if not initial_check["passed"]:
        result.update(proof={"candidate_id": None, "inputs": initial["inputs"], "check": initial_check},
                      stop_reason="INITIAL_MISMATCH")
        return result
    candidates = plan["candidates"][initial["operation"]]
    for _ in range(MAX_TRIALS + 1):
        context = model_context(result, candidates)
        try:
            action = validate_action(selector(copy.deepcopy(context)), context)
        except (ValueError, TypeError) as error:
            result["decisions"].append({"accepted": False, "error": str(error)})
            result["stop_reason"] = "REJECTED_ACTION"
            break
        except Exception:
            result["decisions"].append({"accepted": False, "error": "Селектор завершился ошибкой; повтора нет."})
            result["stop_reason"] = "SELECTOR_ERROR"
            break
        result["decisions"].append({"accepted": True, "action": action})
        if action["action"] == "finish":
            result["stop_reason"] = "BUDGET_EXHAUSTED" if len(result["attempts"]) == MAX_TRIALS else "SELECTOR_STOPPED"
            break
        candidate = next(c for c in candidates if c["id"] == action["candidate_id"])
        trial = {"candidate_id": candidate["id"], "inputs": copy.deepcopy(candidate["inputs"]),
                 "status": "INSUFFICIENT_DATA", "check": None, "error": None}
        result["attempts"].append(trial)
        result["probe_attempts"] += 1
        try:
            new_case = make_case(initial["operation"], candidate["inputs"], initial["check"])
            observed = observe(new_case, variant)
            trial["check"] = check_contract(observed)
            result["contract_checks"] += 1
        except Exception:
            trial["error"] = "Разрешённая операция или независимая проверка завершилась ошибкой; повтора нет."
            result["stop_reason"] = "OPERATION_ERROR"
            break
        if not trial["check"]["passed"]:
            trial["status"] = result["status"] = "REPRODUCED"
            result["proof"] = {key: copy.deepcopy(trial[key]) for key in ("candidate_id", "inputs", "check")}
            result["stop_reason"] = "CONTRACT_MISMATCH"
            break
    return result


def make_artifact(result, variant):
    if result["status"] != "REPRODUCED" or result["proof"] is None:
        raise SearchError("Нет доказанного расхождения для сохранения воспроизведения.")
    data = {"schema_version": 1, "target": {"operation": result["initial"]["operation"], "variant": variant},
            "initial": result["initial"], "proof": result["proof"], "plan_sha256": result["plan_sha256"],
            "source_sha256": source_hashes()}
    replay_data(data)  # Повторная проверка до записи артефакта.
    return data


def validate_proof_check(check, case, initial):
    """Проверить типы доказательства до повторного запуска известного расчёта."""
    if (type(check) is not dict or set(check) != {"actual", "expected", "passed", "absolute_error", "allowed_error"}
            or check["passed"] is not False):
        raise SearchError("Нужны точные поля доказательства и логическое passed=false.")
    for key in ("actual", "expected"):
        diagnostic.validate_view(dict(initial, inputs=case["inputs"], observation=check[key]))
    def finite_nonnegative(value):
        try:
            return type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            return False
    if not finite_nonnegative(check["allowed_error"]):
        raise SearchError("Допуск доказательства должен быть конечным неотрицательным числом.")
    if case["operation"] == "seeded_sequence":
        if check["absolute_error"] is not None or check["allowed_error"] != 0:
            raise SearchError("Для последовательности нужны точное сравнение и absolute_error=null.")
    elif not finite_nonnegative(check["absolute_error"]):
        raise SearchError("Отклонение должно быть конечным неотрицательным числом.")


def replay_data(data):
    try:
        if (type(data) is not dict or set(data) != {"schema_version", "target", "initial", "proof", "plan_sha256", "source_sha256"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1):
            raise SearchError("Неизвестная схема воспроизведения.")
        plan, plan_hash = load_plan()
        if data["plan_sha256"] != plan_hash or data["source_sha256"] != source_hashes():
            raise SearchError("Изменился план или код; прежнее доказательство нельзя считать актуальным.")
        target = data["target"]
        if (type(target) is not dict or set(target) != {"operation", "variant"}
                or type(target["operation"]) is not str or target["operation"] not in CATALOG
                or type(target["variant"]) is not str or target["variant"] not in ("buggy", "fixed")):
            raise SearchError("Неизвестная цель воспроизведения.")
        initial = diagnostic.validate_view(data["initial"])
        if target["operation"] != initial["operation"]:
            raise SearchError("Операция не соответствует исходному наблюдению.")
        proof = data["proof"]
        if type(proof) is not dict or set(proof) != {"candidate_id", "inputs", "check"}:
            raise SearchError("Неизвестные поля доказательства.")
        candidates = {c["id"]: c["inputs"] for c in plan["candidates"][target["operation"]]}
        selected = initial["inputs"] if proof["candidate_id"] is None else candidates.get(proof["candidate_id"])
        if selected is None or proof["inputs"] != selected:
            raise SearchError("Ввод не входит в сохранённый план.")
        case = make_case(target["operation"], proof["inputs"], initial["check"])
        validate_proof_check(proof["check"], case, initial)
        original = observe(make_case(target["operation"], initial["inputs"], initial["check"]), target["variant"])
        if original != initial:
            raise SearchError("Исходное наблюдение изменилось.")
        actual_check = check_contract(observe(case, target["variant"]))
        if actual_check != proof["check"] or actual_check["passed"]:
            raise SearchError("Сохранённое расхождение не повторилось в точности.")
        return {"status": "REPRODUCED", "inputs": case["inputs"], "check": actual_check}
    except SearchError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SearchError("Артефакт не прошёл проверку; произвольные действия не выполняются.") from None


def replay_file(path):
    with local_path(path).open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise SearchError("Артефакт превышает 65536 байт.")
    return replay_data(json.loads(raw, object_pairs_hook=app.unique_object, parse_constant=app.reject_constant))


def render_result(result, command=None):
    escape = lambda value: html.escape(app.json_text(value))
    rows = []
    for trial in result["attempts"]:
        rows.append(f"<tr><td>{html.escape(trial['candidate_id'])}</td><td><pre>{escape(trial['inputs'])}</pre></td>"
                    f"<td><pre>{escape(trial['check'])}</pre></td><td>{'Расхождение' if trial['status'] == 'REPRODUCED' else 'Не воспроизведено'} {html.escape(trial['error'] or '')}</td></tr>")
    return (f"<h2>Исходно: {'недостаточно данных' if result['initial_status'] == 'INSUFFICIENT_DATA' else 'расхождение уже видно'}</h2>"
            f"<p>{html.escape(result['initial']['task'])}</p><pre>{escape(result['initial'])}</pre>"
            f"<p>Исходная независимая проверка:</p><pre>{escape(result['initial_check'])}</pre>"
            "<table><thead><tr><th>Проба</th><th>Новый ввод</th><th>Фактическое сравнение</th><th>Исход</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
            + (f"<h3>Расхождение воспроизведено</h3><pre>{escape(result['proof'])}</pre><p>Повторить из каталога ReproCase:</p><pre>{html.escape(command or 'Артефакт ещё не сохранён.')}</pre>"
               if result["status"] == "REPRODUCED" else "<h3>Недостаточно данных</h3><p>В пределах выполненных проб ошибка не воспроизведена. Исправность не доказана.</p>")
            + f"<p>Новых попыток: {result['probe_attempts']} из {MAX_TRIALS}; остановка: {html.escape(result['stop_reason'] or '')}.</p>")


@loc.localized_html
def html_page(title, body):
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>body{{font:17px/1.5 system-ui;margin:32px auto;padding:0 20px;max-width:1100px;color:#202b38;background:#f7f8fa}}"
            "table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccd3dd;padding:10px;text-align:left;vertical-align:top}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:12px}section{margin:28px 0;padding:16px;border:1px solid #ccd3dd}"
            "aside{background:#fff0cf;padding:16px}</style>"
            f"<h1>{html.escape(title)}</h1><aside>Самостоятельные учебные примеры с намеренными ошибками. Настоящая нейросеть не подключена. Выполняется только фиксированный локальный подбор входов.</aside>{body}</html>\n")


def save_reproducer(result, variant, output_dir):
    directory = local_path(output_dir)
    data = make_artifact(result, variant)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest(data)[:16] + ".json")
    path.write_text(app.json_text(data), encoding="utf-8")
    command = "python3 -B input_search.py replay --artifact " + shlex.quote(str(path.relative_to(ROOT)))
    return path, command


def write_result(result, variant, output_dir=ROOT / "outputs/search-current"):
    directory = local_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path, command = None, None
    if result["status"] == "REPRODUCED":
        path, command = save_reproducer(result, variant, directory / "reproducers")
    (directory / "result.json").write_text(app.json_text(result), encoding="utf-8")
    report = directory / "report.html"
    report.write_text(html_page("ReproCase — подбор нового входа", render_result(result, command)), encoding="utf-8")
    return {"report": report, "artifact": path, "command": command}


def main(argv=None):
    argv = loc.cli_args(argv)
    parser = loc.ArgumentParser(description="Ограниченный поиск входа и повторение сохранённого учебного сбоя.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    search.add_argument("--input", default="examples/search_units_ambiguous.json")
    search.add_argument("--variant", choices=("buggy", "fixed"), default="buggy")
    replay = sub.add_parser("replay")
    replay.add_argument("--artifact", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            print(app.json_text(replay_file(args.artifact)))
            return 0
        result = run_search(app.load_case(local_path(args.input)), args.variant)
        files = write_result(result, args.variant)
        print("Исходно: " + result["initial_status"])
        print(f"Итог: {result['status']}; новых проб: {result['probe_attempts']}/{MAX_TRIALS}.")
        print(files["report"])
        print(files["command"] or "Ошибка не воспроизведена в пределах проб; исправность не доказана.")
        return 0 if result["status"] == "REPRODUCED" else 1
    except (ValueError, OSError) as error:
        print("Проверка остановлена: " + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
