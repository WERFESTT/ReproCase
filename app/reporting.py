"""Самодостаточный русский HTML: все результаты берутся из фактических наблюдений."""

import html
import json

from catalog import VERDICTS


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def numeric(value):
    if value is None:
        return "не определено"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return f"{value:.12g}"


def field(name, value):
    return f'<span data-field="{html.escape(name)}">{html.escape(str(value))}</span>'


def case_list(evidence):
    return evidence.get("cases", [evidence])


def case_section(case, batch):
    info = case["description"]
    case_id = info["case_id"]
    prefix = case_id + "." if batch or case_id != "units" else ""
    unit_case = case_id == "units"
    suffix = "_s" if unit_case else ""
    inputs = "".join(
        f"<li><code>{html.escape(key)}</code>: {field(prefix + 'inputs.' + key, numeric(value))}</li>"
        for key, value in case["case"]["inputs"].items()
    )
    expected = case["results"]["fixed"]["expected"]
    rows = []
    conclusions = []
    for variant, label in (("buggy", "Намеренно ошибочный"), ("fixed", "Исправный")):
        result = case["results"][variant]
        base = prefix + variant
        test = result["regression"]
        badge = "Прошёл" if result["passed"] else "Не прошёл"
        rows.append(
            f"<tr><th scope='row'>{label}</th>"
            f"<td>{field(base + '.actual' + suffix, numeric(result['actual']))}</td>"
            f"<td>{field(base + '.absolute_error' + suffix, numeric(result['absolute_error']))}</td>"
            f"<td class='{'pass' if result['passed'] else 'fail'}'>{field(base + '.passed', badge)}</td>"
            f"<td>{field(base + '.regression.tests_run', test['tests_run'])} тест; "
            f"{field(base + '.regression.failures', test['failures'])} провал; "
            f"{field(base + '.regression.errors', test['errors'])} ошибка запуска</td>"
            f"<td>{field(base + '.verdict', VERDICTS[result['verdict']])}</td></tr>"
        )
        conclusions.append(f"<p><strong>{label}:</strong> {html.escape(result['reason'])}</p>")
    ratio = (
        f"<p>Отношение ошибочного времени к эталонному: {field(prefix + 'ratio', numeric(case['buggy_to_expected_ratio']))}.</p>"
        if unit_case else ""
    )
    proposal = case["results"]["buggy"]["proposal"]
    return f"""<section id="case-{case_id}">
<div class="eyebrow">Учебный случай · {case_id}</div><h2>{html.escape(info['title'])}</h2>
<h3>1. Задача и входные данные</h3><p>{html.escape(info['task'])}</p><ul>{inputs}</ul>
<p>Настройки проверки: <code>{html.escape(json.dumps(case['case']['check'], ensure_ascii=False))}</code>.</p>
<h3>2. Предложение проверки</h3><p>{html.escape(info['hypothesis_text'])}</p>
<p class="muted">Источник: детерминированный демонстрационный режим. Настоящая нейросеть не участвовала.</p>
<details><summary>Строгое предложение JSON</summary><pre>{html.escape(json_text(proposal))}</pre></details>
<h3>3. Разрешённая проверка</h3><p>Идентификатор: <code>{html.escape(case['checker']['id'])}</code>.
Предложение проверено по разрешённому списку и привязано к отпечатку этих входных данных.</p>
<p>{html.escape(info['reference_text'])}</p>
<p class="expected">Эталон: <strong>{field(prefix + 'expected' + suffix, numeric(expected))} {html.escape(info['unit'])}</strong>.
Допуск: {field(prefix + 'allowed_error' + suffix, numeric(case['results']['fixed']['allowed_error']))}.
Для последовательности требуется точное совпадение.</p>
<h3>4. Фактическое наблюдение</h3><div class="table-wrap"><table>
<thead><tr><th>Вариант</th><th>Получено</th><th>Абсолютная ошибка</th><th>Проверка</th><th>Регрессионный тест</th><th>Вывод о гипотезе</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>{ratio}
<p class="muted">Один и тот же регрессионный контракт применён к обоим вариантам. Провал намеренно ошибочного расчёта ожидается. Для последовательности числовая абсолютная ошибка не определяется.</p>
<h3>5. Вывод и границы доказательства</h3>{''.join(conclusions)}
<p>Статус пары: {field(prefix + 'classification', case['classification'])}.</p>
<p><strong>Что не доказано:</strong> {html.escape(info['not_proven'])}</p></section>"""


def render_html(evidence):
    cases = case_list(evidence)
    batch = "cases" in evidence
    confirmed = sum(c["results"]["buggy"]["verdict"] == "CONFIRMED" for c in cases)
    clean = sum(c["results"]["fixed"]["verdict"] == "REFUTED" for c in cases)
    if batch:
        title = "Три учебные проверки ReproCase"
    else:
        title = "Учебный сбой воспроизведён" if cases[0]["classification"] == "REPRODUCED" else "На этих данных сбой не подтверждён"
    summary_rows = "".join(
        f"<tr><th><a href='#case-{c['description']['case_id']}'>{html.escape(c['description']['title'])}</a></th>"
        f"<td>{html.escape(numeric(c['results']['buggy']['actual']))}</td>"
        f"<td>{html.escape(numeric(c['results']['fixed']['actual']))}</td>"
        f"<td>{html.escape(numeric(c['results']['fixed']['expected']))}</td>"
        f"<td>{VERDICTS[c['results']['buggy']['verdict']]}</td></tr>" for c in cases
    )
    sections = "\n".join(case_section(c, batch) for c in cases)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>ReproCase — офлайн-основа, этап 2</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:#192c3b;font:17px/1.65 system-ui,sans-serif}}
main{{max-width:1180px;margin:auto;padding:40px 24px 60px}}h1{{font-size:clamp(30px,4vw,43px);line-height:1.2}}
h2{{font-size:26px;margin:8px 0 20px}}h3{{font-size:19px;margin:24px 0 8px}}p{{margin:10px 0 15px}}
section,.notice{{background:white;border:1px solid #d5e0e7;border-radius:14px;padding:25px;margin:20px 0}}
.notice{{border-left:6px solid #c47420;background:#fff5e7}}.notice strong{{font-size:21px}}
.eyebrow{{font-size:13px;letter-spacing:.1em;text-transform:uppercase;font-weight:700;color:#496576}}
.muted,footer{{color:#496273;font-size:15px}}.expected{{background:#edf6f2;padding:15px;border-radius:8px}}
.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:15px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #dbe5eb;vertical-align:top}}
thead{{background:#eef3f6}}.pass{{color:#166446;font-weight:700}}.fail{{color:#923b0e;font-weight:700}}
code,pre{{font:14px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}pre{{background:#edf2f5;padding:16px;white-space:pre-wrap;border-radius:9px}}
summary{{cursor:pointer;font-weight:650}}a{{color:#005b85}}li{{margin:4px 0}}.route{{font-weight:650;color:#126652}}
@media(max-width:680px){{main{{padding:20px 12px}}section{{padding:17px}}h2{{font-size:23px}}}}
@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}details{{display:none}}}}
</style></head><body><main><header><div class="eyebrow">ReproCase · локально · этап 2</div>
<h1>{title}</h1><div class="notice"><strong>Настоящая нейросеть ещё не подключена.</strong>
<p>Предложения выбирает детерминированный демонстрационный режим. API-запросов: 0. Нет измеренного улучшения от ИИ.</p></div>
<p>{html.escape(evidence['disclaimer'])} Все три случая написаны специально для обучения.</p>
<p class="route">Входные данные → предложение → разрешённая проверка → наблюдение → вывод</p></header>
<section><h2>Краткая сводка</h2>
<p>Примеров: {field('summary.cases', len(cases))}. Подтверждений намеренной ошибки: {field('summary.confirmed_buggy', confirmed)}.
Исправных вариантов, опровергающих гипотезу на выбранном вводе: {field('summary.refuted_fixed', clean)}.</p>
<div class="table-wrap"><table><thead><tr><th>Задача</th><th>Неправильный результат</th><th>Исправный результат</th><th>Эталон</th><th>Гипотеза об ошибочном варианте</th></tr></thead><tbody>{summary_rows}</tbody></table></div>
<p>«Подтверждено» означает совпадение отклонения с заданной причиной. «Опровергнуто на этом вводе» означает прохождение различающей проверки.
«Недостаточно данных» означает, что ввод не различает варианты или наблюдаемая ошибка не соответствует этой гипотезе. Это не доказательство правильности всего кода.</p></section>
{sections}
<section><h2>Как повторить</h2><pre>python3 reprocase.py demo-all
python3 reprocase.py check --input examples/seed_mismatch.json --variant buggy
python3 reprocase.py check --input examples/seed_mismatch.json --variant fixed
python3 verify.py</pre>
<p>Для проверки одного варианта код 0 означает прохождение теста, 1 — провал, 2 — отклонение ввода или предложения до запуска.
Команда <code>demo-all</code> возвращает 0, когда три намеренные ошибки подтверждены, а три исправных варианта проходят.</p>
<p><a href="../README.md">Инструкция</a> · <a href="../MORNING_HANDOFF.md">Передача на утро</a> · <a href="bug-reproducer-evidence.json">JSON результатов</a> · <a href="bug-reproducer-report.md">Текстовый отчёт</a> · <a href="../proposal.schema.json">Схема предложения</a></p>
<p><a href="../BENCHMARK.md">Сравнение методов на вариациях случаев</a>: результаты обычного метода измеряются отдельно, настоящая модель пока не проверена.</p>
<p><a href="input-search-report.html">Подбор нового входа для неоднозначного наблюдения</a>: ограниченный локальный поиск, все попытки и команды повторения.</p>
<p>Журнал общей проверки: <code>outputs/verification.txt</code>. Он обновляется командой <code>python3 verify.py</code>, отдельно от отчёта.
HTML — снимок результатов. Изменили код или данные — запустите отчёт заново.</p></section>
<section><h2>Граница будущей модели</h2><p>Разрешено только действие <code>run_check</code> для одного из трёх известных сочетаний задачи, гипотезы и проверки.
Предложение привязано к SHA-256 нормализованных входных данных. Произвольные команды, код, пути, параметры запуска, дополнительные поля и пачки действий не принимаются.</p>
<p>Будущая модель может предложить только данные этой схемы. Ответ не является разрешением на действие: локальная проверка выполняется до расчёта.
Офлайн-тесты искусственных ответов не проверяют настоящую NVIDIA.</p>
<p><strong>Визуальная проверка в браузере не выполнена:</strong> ранее инструмент запретил <code>file://</code>. Запрет не обходился.
Проверены структура HTML, ссылки и соответствие сохранённым результатам.</p></section>
<section><details><summary>Полные фактические результаты и контрольные суммы</summary><pre id="evidence-json">{html.escape(json_text(evidence))}</pre></details></section>
<footer>Учебная офлайн-основа на стандартной библиотеке Python. Не готовая конкурсная заявка. Публикации нет.</footer>
</main></body></html>
"""


def render_markdown(evidence):
    chunks = ["# ReproCase — офлайн-основа, этап 2\n", evidence["disclaimer"],
              "\n**Настоящая нейросеть ещё не подключена.** Выбор проверки детерминированный.\n"]
    for case in case_list(evidence):
        chunks.extend([f"## {case['description']['title']}\n", case["description"]["task"],
                       "\n| Вариант | Получено | Ожидается | Регрессионный тест |",
                       "| --- | --- | --- | --- |"])
        for variant, result in case["results"].items():
            chunks.append(f"| {variant} | {numeric(result['actual'])} | {numeric(result['expected'])} | "
                          f"{'прошёл' if result['passed'] else 'не прошёл'} |")
        chunks.extend([f"\nСтатус пары: {case['classification']}.", case["description"]["reference_text"],
                       "Не доказано: " + case["description"]["not_proven"]])
    chunks.extend(["\nЗапуск: `python3 reprocase.py demo-all`. Общие проверки: `python3 verify.py`.",
                   "[JSON](bug-reproducer-evidence.json) · [HTML](report.html) · [Журнал](verification.txt).",
                   "Визуальный просмотр не выполнен из-за запрета file://; обходов не было. Проверка HTML текстовая и структурная."])
    return "\n\n".join(chunks) + "\n"
