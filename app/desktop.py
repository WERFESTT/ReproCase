#!/usr/bin/env python3
"""Окно ReproCase. Сеть включается только после отдельного подтверждения пользователя."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

from catalog import CATALOG, VERDICTS
from benchmark_adapter import build_request
import benchmark_diagnostics as diagnostics
import input_search
import live_search
import comparison_series
import property_live
import desktop_theme
import localization as loc
from localization import print
import reprocase as app


LABELS = {
    "distance_mm": "Путь, мм", "speed_m_s": "Скорость, м/с", "seed": "Начальное значение",
    "count": "Число элементов", "initial": "Начальное значение", "rate": "Скорость изменения",
    "duration_s": "Длительность", "steps": "Число шагов",
}
INTEGER_FIELDS = {"seed", "count", "steps"}


def result_text(record):
    """Уточнить подписи только для показа; исходное свидетельство не изменять."""
    text = record.get("summary_ru", "")
    if record.get("kind") != "PROPERTY_PREPARATION":
        return text
    metrics = record["metrics"]
    files = sum(len(row.get("exports", [])) for row in record["rows"])
    attempts = metrics.get('export_test_process_attempts')
    if attempts is None:
        attempts = sum(sum(v in e for v in ('before','after')) for row in record['rows'] for e in row.get('exports',[]))
        if not attempts:
            attempts = metrics.get('export_test_extra_sut_calls')
    calculations = metrics.get('export_test_extra_sut_calls')
    clarification = (
        f"Исполнений расчёта для свойства модели: {metrics['model_property_sut_calls']}; "
        f"для ручного контроля: {metrics['manual_baseline_sut_calls']}. "
        "Включены поиск, сокращение и подтверждающие повторы до/после.\n"
        f"Экспортировано файлов тестов: {files}; попыток запуска этих файлов до/после: {attempts}.\n"
        f"Дополнительные исполнения расчёта: {calculations if calculations is not None else 'неизвестно' }.")
    paragraphs = text.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        if paragraph.startswith("Исполнения для свойства модели:"):
            paragraphs[index] = clarification
            break
    else:
        paragraphs.append(clarification)
    return "\n\n".join(paragraphs)


def request_log(record):
    """Журнал отправки без ключа, сырых ответов и непроверенного текста поставщика."""
    count = record.get("accounting", {}).get("network_attempts")
    if type(count) is not int or count < 0:
        return "Запросы — статус неизвестен", "Нет достоверного счётчика обращений к API; число отправок не установлено."
    events = record.get("events", [])
    received = sum(e.get("network_attempted") is True and e.get("status") == "validated" for e in events)
    cancelled = record.get("cancel_requested") is True
    failed = bool(record.get("failure")) or any(e.get("network_attempted") is True and e.get("status") != "validated" for e in events)
    failed = failed or (record.get("kind") == "PROPERTY_PREPARATION" and record.get("state") in
                        ("REJECTED", "FALSE_PROPERTY", "ERROR", "UNCONFIRMED", "INVALID_MODEL_RESPONSE", "TRANSPORT_ERROR",
                         "LOCAL_EXPORT_ERROR", "LOCAL_CHECK_ERROR", "STALE_RUNTIME"))
    if count == 0:
        title = "Запросы — отменено до отправки" if cancelled else "Запросы — отправок нет"
        text = "API не вызывался. Попыток отправки: 0."
    elif cancelled:
        title = f"Запросы — отмена; попыток: {count}"
        text = f"Отмена запрошена после начала обращения к API. Попыток отправки: {count}.\nДальнейшие запросы остановлены."
    elif record.get('state') in ('LOCAL_EXPORT_ERROR','LOCAL_CHECK_ERROR','STALE_RUNTIME'):
        title = f"Запросы — отправлено: {count}; локальная ошибка"
        text = f"Попыток отправки: {count}. Серия остановлена на локальной проверке/экспорте или смене версии кода.\nЭто не отказ ответа модели."
    elif failed:
        title = f"Запросы — отказ; попыток: {count}"
        text = f"Запуск остановлен после попытки обращения к API. Попыток отправки: {count}.\nПолучение и обработка запроса поставщиком могут оставаться неизвестными."
    else:
        title = f"Запросы — отправлено: {count}"
        text = f"Отправлено запросов: {count}. Завершён подтверждённый запуск."
    text += f"\nОтветов с допустимым действием: {received}."
    for index, event in enumerate(events, 1):
        if event.get("network_attempted") is not True:
            continue
        status = "ответ прошёл локальную проверку" if event.get("status") == "validated" else "допустимое действие не получено или не принято"
        text += f"\nПопытка {index}: {status}."
        action = event.get("action") or {}
        if action.get("action") == "try_input" and action.get("candidate_id") in ("c01", "c02", "c03", "c04"):
            text += " Выбран вход " + action["candidate_id"] + "."
        elif action.get("action") == "finish":
            text += " Выбрано завершение поиска."
    if count:
        text += "\nОтмена, отказ или тайм-аут не доказывают отсутствие списания. Расход указан на вкладке результата."
    text += "\nЭто журнал состояния отправки. Ключ и сырые ответы поставщика здесь не показываются."
    return title, text


class Session:
    """Логика окна отдельно от виджетов; использует существующий проверяющий контур."""

    def __init__(self):
        self.case = app.load_case(app.DEFAULT_CASE)
        self.evidence = None
        self.search_result = None
        self.replay_command = None

    def invalidate(self):
        self.evidence = None
        self.search_result = None
        self.replay_command = None

    def choose(self, operation):
        self.invalidate()
        if operation not in CATALOG:
            raise app.InputError("Неизвестный учебный случай.")
        self.case = app.load_case(app.ROOT / "examples" / CATALOG[operation]["file"])

    def load(self, path):
        self.invalidate()
        self.case = app.load_case(path)

    def update(self, values):
        self.invalidate()
        if set(values) != set(self.case["inputs"]):
            raise app.InputError("Набор полей не соответствует выбранному примеру.")
        inputs = {}
        for key, value in values.items():
            try:
                text = value.strip().replace(",", ".")
                inputs[key] = int(text) if key in INTEGER_FIELDS else float(text)
            except (ValueError, AttributeError, OverflowError):
                raise app.InputError(f"{LABELS[key]}: введите {'целое ' if key in INTEGER_FIELDS else ''}число.") from None
        candidate = dict(self.case, inputs=inputs)
        self.case = app.validate_case(candidate)

    def run(self, values):
        self.update(values)
        self.evidence = app.make_evidence(self.case)
        return self.evidence

    def save(self):
        if self.search_result is not None:
            saved = input_search.write_result(self.search_result, "buggy", app.ROOT / "outputs/search-current")
            self.replay_command = saved["command"]
            return saved["report"]
        if self.evidence is None:
            raise app.InputError("Сначала выполните проверку текущих данных.")
        app.write_report(self.evidence, app.ROOT / "outputs")
        return app.ROOT / "outputs/report.html"

    def find_input(self, values):
        self.update(values)
        self.search_result = input_search.run_search(self.case, "buggy")
        return self.search_result

    def draft_request(self, values):
        """Одно учебное наблюдение, без эталона и готового предложения проверки."""
        self.update(values)
        operation = self.case["operation"]
        observation = app.CALCULATIONS[operation]["buggy"](**self.case["inputs"])
        public = {"schema_version": 1, "operation": operation, "task": CATALOG[operation]["task"],
                  "inputs": self.case["inputs"], "check": self.case["check"], "observation": observation}
        public["sample_id"] = hashlib.sha256(app.json_text(public).encode("utf-8")).hexdigest()[:16]
        return build_request(diagnostics.model_context(public, []))


class Window:
    def __init__(self, root):
        import tkinter as tk
        from localization_gui import Binder, TextVariable
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        self.root, self.tk, self.ttk = root, tk, ttk
        self.language, language_warning = loc.load_setting()
        self.language = loc.cli_language or self.language
        loc.set_language(self.language)
        self._display_sources = {}
        self.theme, theme_warning = desktop_theme.load_setting()
        desktop_theme.apply_theme(root, self.theme)
        self.session = Session()
        self.entries = {}
        self.live_pending = False
        self.live_job = None
        self.live_outcome = None
        self.closing_after_live = False
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.title("ReproCase — локальная проверка")
        root.geometry("1120x820")
        root.minsize(820, 650)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        body = ttk.Frame(root, padding=20)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(7, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="ReproCase", font=("TkDefaultFont", 23, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Тема:").grid(row=0, column=1, padx=(16, 6))
        self.theme_choice = TextVariable(root, value=desktop_theme.LABELS[self.theme], choices=desktop_theme.LABELS.values())
        self.theme_selector = ttk.Combobox(header, textvariable=self.theme_choice, state="readonly",
                                         values=list(desktop_theme.LABELS.values()), width=10)
        self.theme_selector.grid(row=0, column=2, sticky="e")
        self.theme_selector.bind("<<ComboboxSelected>>", self.change_theme)
        ttk.Label(header, text="Язык:").grid(row=0, column=3, padx=(16, 6))
        self.language_choice = tk.StringVar(value=self.language.upper())
        self.language_selector = ttk.Combobox(header, textvariable=self.language_choice, state="readonly",
                                             values=("RU", "EN"), width=4)
        self.language_selector.grid(row=0, column=4, sticky="e")
        self.language_selector.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Label(body, text="Учебные и исторические проверки • отправка модели только после подтверждения",
                  style="Warning.TLabel").grid(row=1, sticky="w", pady=(3, 16))
        self.selection = TextVariable(root, choices=[x["title"] for x in CATALOG.values()])
        self.selector = ttk.Combobox(body, textvariable=self.selection, state="readonly",
                                    values=[x["title"] for x in CATALOG.values()])
        self.selector.grid(row=2, sticky="ew")
        self.selector.bind("<<ComboboxSelected>>", self.select)
        self.task_text = TextVariable(root)
        ttk.Label(body, textvariable=self.task_text, wraplength=1000).grid(row=3, sticky="ew", pady=10)
        self.form = ttk.Frame(body)
        self.form.grid(row=4, sticky="ew")
        buttons = ttk.Frame(body)
        buttons.grid(row=5, sticky="ew", pady=14)
        self.run_button = ttk.Button(buttons, text="Проверить оба варианта", command=self.run)
        self.run_button.grid(row=0, column=0, padx=(0, 8))
        self.reset_button = ttk.Button(buttons, text="Вернуть пример", command=self.reset)
        self.reset_button.grid(row=0, column=1, padx=8)
        self.load_button = ttk.Button(buttons, text="Загрузить JSON…", command=self.load)
        self.load_button.grid(row=0, column=2, padx=8)
        self.save_button = ttk.Button(buttons, text="Сохранить отчёт", command=self.save, state="disabled")
        self.save_button.grid(row=0, column=3, padx=8)
        self.copy_button = ttk.Button(buttons, text="Копировать результат", command=self.copy_result, state="disabled")
        self.copy_button.grid(row=0, column=4, padx=8)
        self.prepare_button = ttk.Button(buttons, text="Запрос для модели (офлайн)", command=self.prepare)
        self.prepare_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.find_button = ttk.Button(buttons, text="Подобрать новый ввод", command=self.find_input)
        self.find_button.grid(row=1, column=2, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.live_button = ttk.Button(buttons, text="Поиск с реальной моделью…", command=self.start_live)
        self.live_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.series_button = ttk.Button(buttons, text="Сравнить пять случаев…", command=lambda: self.start_live(series=True))
        self.series_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.property_choice = TextVariable(root, value=property_live.OPTIONS[0], choices=property_live.OPTIONS)
        self.property_selector = ttk.Combobox(buttons, textvariable=self.property_choice,
                                             values=property_live.OPTIONS, state="readonly", width=40)
        self.property_selector.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.property_selector.bind("<<ComboboxSelected>>", self.clear)
        self.property_preview_button = ttk.Button(buttons, text="Данные для подготовки теста", command=self.prepare_property)
        self.property_preview_button.grid(row=4, column=2, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.property_live_button = ttk.Button(buttons, text="Подготовить тест с моделью…",
                                               command=lambda: self.start_live(property_mode=True))
        self.property_live_button.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.cancel_button = ttk.Button(buttons, text="Остановить дальнейшие запросы", command=self.cancel_live, state="disabled")
        self.cancel_button.grid(row=2, column=2, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self.status = TextVariable(root, value="Выберите пример, измените данные и нажмите «Проверить оба варианта».")
        ttk.Label(body, textvariable=self.status, wraplength=1000).grid(row=6, sticky="ew", pady=(0, 8))
        self.tabs = ttk.Notebook(body)
        self.tabs.grid(row=7, sticky="nsew")
        result_page = ttk.Frame(self.tabs, padding=8)
        result_page.columnconfigure(0, weight=1)
        result_page.rowconfigure(1, weight=1)
        self.tabs.add(result_page, text="Результат")
        self.table = ttk.Treeview(result_page, columns=("variant", "actual", "expected", "verdict"), show="headings", height=4)
        for key, label, width in (("variant", "Вариант", 165), ("actual", "Получено", 250),
                                  ("expected", "Эталон", 250), ("verdict", "Вывод", 235)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=120, stretch=True)
        self.table.grid(row=0, column=0, sticky="ew")
        self.explanation = ScrolledText(result_page, wrap="word", height=12, font=("TkDefaultFont", 11))
        self.explanation.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.copy_menu = tk.Menu(self.explanation, tearoff=False)
        self.copy_menu.add_command(label="Копировать выделенное", command=self.copy_selection)
        self.copy_menu.add_command(label="Копировать результат целиком", command=self.copy_result)
        self.explanation.bind("<Button-3>", self.show_copy_menu)
        self.raw = ScrolledText(self.tabs, wrap="word", font=("TkFixedFont", 10))
        self.tabs.add(self.raw, text="Данные проверки")
        self.draft = ScrolledText(self.tabs, wrap="word", font=("TkFixedFont", 10))
        self.tabs.add(self.draft, text="Запросы — нет текущего запуска")
        ttk.Label(body, text="Намеренные учебные ошибки. Результаты относятся только к заданным правилам расчёта.",
                  wraplength=1000).grid(row=8, sticky="ew", pady=(10, 0))
        self.populate()
        self.localizer = Binder(self)
        self.localizer.refresh()
        self.apply_theme()
        root.update_idletasks()
        root.minsize(max(820, root.winfo_reqwidth()), 650)
        if theme_warning or language_warning:
            self.status.set(theme_warning or language_warning)

    def change_language(self, event=None):
        language = self.language_choice.get().lower()
        if language not in loc.LANGUAGES:
            self.language_choice.set(self.language.upper())
            return
        self.language = language
        loc.set_language(language)
        self.localizer.refresh()
        self.root.update_idletasks()
        self.root.minsize(max(820, self.root.winfo_reqwidth()), 650)
        try:
            loc.save_setting(language)
        except (OSError, ValueError):
            self.status.set("Язык применён, но сохранить настройку не удалось.")

    def apply_theme(self):
        colors = desktop_theme.apply_theme(self.root, self.theme)
        for tag in ("pass", "fail"):
            self.table.tag_configure(tag, background=colors[tag + "_bg"], foreground=colors[tag + "_fg"])

    def change_theme(self, event=None):
        theme = next((key for key, label in desktop_theme.LABELS.items() if label == self.theme_choice.get()), None)
        if theme is None:
            self.theme_choice.set(desktop_theme.LABELS[self.theme])
            return
        self.theme = theme
        self.apply_theme()
        try:
            desktop_theme.save_setting(theme)
        except OSError:
            self.status.set("Тема применена, но сохранить настройку не удалось. При следующем запуске выбор может сброситься.")
        else:
            self.status.set("Тема «" + desktop_theme.LABELS[theme] + "» сохранена. Системные диалоги могут использовать тему ОС.")

    def put(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if not hasattr(self, "_display_sources"):
            self._display_sources = {}
        self._display_sources[widget] = text
        if widget is getattr(self, "raw", None):
            shown = loc.text("Исходные данные: язык и поля сохранены без изменения.\n\n") + text if text else ""
        else:
            shown = loc.text(text)
            if loc.get_language() == "en" and widget is getattr(self, "draft", None) and text and ("{\n" in text or "{\r\n" in text):
                shown = loc.text("Предпросмотр исходного запроса. JSON ниже сохранён на исходном языке; переключатель его не меняет.\n\n") + shown
        widget.insert("1.0", shown)
        widget.configure(state="disabled")
        if widget is self.explanation:
            self.copy_button.configure(state="normal" if text.strip() and not self.live_pending else "disabled")

    def copy_status(self, text):
        try:
            self.status.set(text)
        except self.tk.TclError:
            pass  # Окно могло быть закрыто до обработки события.

    def write_clipboard(self, text):
        try:
            if not self.root.winfo_exists():
                return False
            if self.live_pending or not text.strip():
                self.copy_status("Нет готового результата для копирования.")
                return False
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except self.tk.TclError:
            self.copy_status("Не удалось записать в буфер обмена. Результат остался в окне; можно использовать Ctrl+C или сохранённый отчёт.")
            return False
        self.copy_status("Результат скопирован в буфер обмена. API не вызывался.")
        return True

    def copy_result(self):
        try:
            text = self.explanation.get("1.0", "end-1c")
        except self.tk.TclError:
            return False
        return self.write_clipboard(text)

    def copy_selection(self):
        try:
            text = self.explanation.get("sel.first", "sel.last")
        except self.tk.TclError:
            self.copy_status("Сначала выделите текст или выберите копирование результата целиком.")
            return False
        return self.write_clipboard(text)

    def show_copy_menu(self, event):
        try:
            available = bool(self.explanation.get("1.0", "end-1c").strip()) and not self.live_pending
            selected = bool(self.explanation.tag_ranges("sel")) and available
            self.copy_menu.entryconfigure(0, state="normal" if selected else "disabled")
            self.copy_menu.entryconfigure(1, state="normal" if available else "disabled")
            self.copy_menu.tk_popup(event.x_root, event.y_root)
        except self.tk.TclError:
            pass
        finally:
            try:
                self.copy_menu.grab_release()
            except self.tk.TclError:
                pass
        return "break"

    def clear(self, *unused):
        if getattr(self, "live_pending", False):
            return
        self.live_outcome = None
        self.session.invalidate()
        self.save_button.configure(state="disabled")
        for row in self.table.get_children():
            self.table.delete(row)
        for widget in (self.explanation, self.raw, self.draft):
            self.put(widget, "")
        self.tabs.tab(self.draft, text="Запросы — нет текущего запуска")
        self.status.set("Данные изменены. Для актуального результата запустите проверку.")

    def values(self):
        return {key: variable.get() for key, variable in self.entries.items()}

    def populate(self):
        self.clear()
        for child in self.form.winfo_children():
            child.destroy()
        self.entries = {}
        info = CATALOG[self.session.case["operation"]]
        self.selection.set(info["title"])
        self.task_text.set(info["task"])
        for index, (key, value) in enumerate(self.session.case["inputs"].items()):
            column, row = (index % 2) * 2, index // 2
            self.ttk.Label(self.form, text=LABELS[key]).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=6)
            variable = self.tk.StringVar(value=app.numeric(value))
            self.ttk.Entry(self.form, textvariable=variable, width=21).grid(row=row, column=column + 1, sticky="w", padx=(0, 28), pady=6)
            variable.trace_add("write", self.clear)
            self.entries[key] = variable
        if hasattr(self, "localizer"):
            self.localizer.refresh()

    def fail(self, error):
        self.clear()
        self.status.set("Проверка не запущена: " + str(error))

    def select(self, unused=None):
        if getattr(self, "live_pending", False):
            return
        for operation, info in CATALOG.items():
            if info["title"] == self.selection.get():
                self.session.choose(operation)
                self.populate()
                return

    def reset(self):
        if getattr(self, "live_pending", False):
            return
        self.session.choose(self.session.case["operation"])
        self.populate()

    def load(self):
        if getattr(self, "live_pending", False):
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title=loc.text("Выберите настройки ReproCase"),
                                         initialdir=app.ROOT / "examples", filetypes=[(loc.text("Настройки JSON"), "*.json")])
        if not path:
            return
        try:
            self.session.load(path)
            self.populate()
        except (app.InputError, OSError) as error:
            self.fail(error)

    def run(self):
        if getattr(self, "live_pending", False):
            return
        values = self.values()
        self.clear()
        try:
            evidence = self.session.run(values)
        except (ValueError, OSError) as error:
            self.fail(error)
            return
        info = evidence["description"]
        text = [info["task"], "Гипотеза: " + info["hypothesis_text"], "Эталон: " + info["reference_text"]]
        for variant, label in (("buggy", "Намеренно ошибочный"), ("fixed", "Исправный")):
            result = evidence["results"][variant]
            self.table.insert("", "end", values=(label, app.numeric(result["actual"]), app.numeric(result["expected"]), VERDICTS[result["verdict"]]),
                              tags=("pass" if result["passed"] else "fail",))
            test = "прошёл" if result["regression"]["passed"] else "провален"
            text.append(f"{label}: {result['reason']} Регрессионный тест {test}.")
        text.append("Что не доказано: " + info["not_proven"])
        self.put(self.explanation, "\n\n".join(text))
        self.put(self.raw, app.json_text(evidence))
        self.save_button.configure(state="normal")
        self.status.set("Проверка выполнена локально. Сетевых вызовов: 0. Можно сохранить отчёт.")
        self.tabs.select(0)

    def save(self):
        if getattr(self, "live_pending", False):
            return
        try:
            if getattr(self, "live_outcome", None) is not None:
                outcome = self.live_outcome
                kind = outcome["record"].get("kind")
                writer = (property_live.save_report if kind == "PROPERTY_PREPARATION" else
                          comparison_series.save_report if kind == "FIVE_CASE_COMPARISON" else live_search.save_report)
                path = writer(outcome["record"], outcome["directory"])
                outcome.update(report=str(path), save_error=None)
                self.status.set("Отдельный отчёт сохранён: " + str(path))
                return
            path = self.session.save()
            self.status.set("Отчёт сохранён: " + str(path))
            if self.session.replay_command:
                self.status.set("Отчёт сохранён: " + str(path) + "\nПовтор: " + self.session.replay_command)
        except (ValueError, OSError) as error:
            self.status.set("Отчёт не сохранён: " + str(error))

    def find_input(self):
        if getattr(self, "live_pending", False):
            return
        values = self.values()
        self.clear()
        try:
            result = self.session.find_input(values)
        except (ValueError, OSError) as error:
            self.fail(error)
            return
        initial = result["initial_check"]
        first = "Недостаточно данных" if initial["passed"] else "Расхождение"
        self.table.insert("", "end", values=("Исходное наблюдение", app.numeric(initial["actual"]),
                                               app.numeric(initial["expected"]), first))
        text = ["Офлайн-подбор для учебного ошибочного варианта. Настоящая модель не используется.",
                "Исходно: " + first + ".", "Правило: " + result["initial"]["task"]]
        for trial in result["attempts"]:
            check = trial["check"]
            label = "Расхождение" if trial["status"] == "REPRODUCED" else "Не воспроизведено"
            self.table.insert("", "end", values=("Проба " + trial["candidate_id"],
                              app.numeric(check["actual"]) if check else "Ошибка выполнения",
                              app.numeric(check["expected"]) if check else "Не получен", label))
            text.append("Проба " + trial["candidate_id"] + ": " + app.json_text(trial["inputs"]) + label + ". " + (trial["error"] or ""))
        text.append("Выполнено новых проб: " + str(result["probe_attempts"]) + "/3. Совпадение в этих пробах не доказывает исправность.")
        text.append("Сохраните отчёт: он содержит все исходы, а при доказанном расхождении — пример и команду повторения.")
        self.put(self.explanation, "\n\n".join(text))
        self.put(self.raw, app.json_text(result))
        self.save_button.configure(state="normal")
        self.status.set("Расхождение воспроизведено. Можно сохранить пример." if result["status"] == "REPRODUCED"
                        else "В пределах проб расхождение не найдено. Исправность не доказана.")
        self.tabs.select(0)

    def prepare(self):
        if getattr(self, "live_pending", False):
            return
        values = self.values()
        self.clear()
        try:
            draft = self.session.draft_request(values)
        except ValueError as error:
            self.fail(error)
            return
        self.put(self.draft, "ЗАПРОС НЕ ОТПРАВЛЕН. КЛЮЧ НЕ ИСПОЛЬЗОВАН.\n"
                 "Наблюдение получено локально из учебного ошибочного варианта. "
                 "Его название, эталон и готовый ответ в запрос не входят.\n\n" + app.json_text(draft))
        self.tabs.tab(self.draft, text="Предпросмотр — не отправлен")
        self.status.set("Подготовлен диагностический запрос с одним наблюдением. Отправки не было.")
        self.tabs.select(2)

    def set_live_busy(self, busy):
        if busy:
            widgets = [self.selector, self.run_button, self.reset_button, self.load_button, self.save_button,
                       self.copy_button,
                       self.prepare_button, self.find_button, self.live_button, self.series_button,
                       self.property_selector, self.property_preview_button, self.property_live_button]
            widgets += [widget for widget in self.form.winfo_children() if widget.winfo_class() == "TEntry"]
            self._live_states = [(widget, str(widget.cget("state"))) for widget in widgets]
            for widget, _ in self._live_states:
                widget.configure(state="disabled")
        else:
            for widget, state in self._live_states:
                widget.configure(state=state)
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def prepare_property(self):
        if self.live_pending:
            return
        self.clear()
        try:
            plan = property_live.make_plan(property_live.OPTIONS.index(self.property_choice.get()))
            self.show_request_state("Предпросмотр теста — не отправлен", property_live.preview(plan))
            self.status.set("Это полный предпросмотр подготовки свойства. Отправки и ввода ключа не было.")
            self.tabs.select(2)
        except ValueError as error:
            self.fail(error)

    def start_live(self, *, series=False, property_mode=False):
        if self.live_pending:
            return
        from tkinter import messagebox, simpledialog
        try:
            if property_mode:
                property_live.ensure_runtime_current()
                case = property_live.make_plan(property_live.OPTIONS.index(self.property_choice.get()))
            elif series:
                case = comparison_series.load_plan()
            else:
                self.session.update(self.values())
                case = self.session.case
        except (ValueError, OSError) as error:
            self.fail(error)
            return
        # Флаг ставится до диалогов: повторное событие не создаёт второй запуск.
        self.clear()
        self.live_pending = True
        self.live_job = None
        self.live_start_cancelled = False
        self.closing_after_live = False
        self.set_live_busy(True)
        self.show_request_state("Запросы — ожидают разрешения", "Отправки ещё не было. Ожидается подтверждение пользователя и ввод ключа.")
        started = False
        key = None
        try:
            consent = (property_live.consent_text(case) if property_mode else
                       comparison_series.consent_text(case) if series else live_search.consent_text(case))
            if not messagebox.askyesno(loc.text("Разрешение на реальный запрос"), loc.text(consent), parent=self.root):
                self.status.set("Отправка отменена пользователем. Запросов не было.")
                self.show_request_state("Запросы — отменено до отправки", "Подтверждение не получено. Попыток отправки: 0.")
                return
            if self.live_start_cancelled:
                return
            key = simpledialog.askstring(loc.text("Ключ Nebius — только в памяти"), loc.text("Введите ключ. Он не будет сохранён:"), show="*", parent=self.root)
            if key is None or self.live_start_cancelled:
                self.status.set("Ввод ключа отменён. Запросов не было.")
                self.show_request_state("Запросы — отменено до отправки", "Ввод ключа или запуск отменён. Попыток отправки: 0.")
                return
            self.live_job = (property_live.PropertyJob() if property_mode else
                             comparison_series.SeriesJob() if series else live_search.LiveJob())
            if property_mode:
                self.live_job.start(case, key, confirmed=True, approved_requests=case["requests"])
            else:
                self.live_job.start(case, key, confirmed=True)
            started = True
            self.status.set("Выполняется подтверждённый поиск. Окно доступно; можно остановить дальнейшие запросы.")
            self.show_request_state("Запросы — выполняются", "Подтверждённый поиск выполняется. Итоговое число попыток и состояние ответов появятся после завершения. Это не офлайн-предпросмотр.")
            self.root.after(50, self.poll_live)
        except property_live.StaleRuntime as error:
            self.status.set(str(error))
            self.show_request_state("Запросы — нужен перезапуск", "Код обновлён после загрузки окна. Запуск остановлен до отправки; перезапустите ReproCase.")
        except (ValueError, OSError, ImportError):
            self.status.set("Запуск не начат: проверьте формат ключа и доступность папки отчётов. Запросов не было.")
            self.show_request_state("Запросы — отправок нет", "Запуск не начат. Попыток отправки: 0.")
        finally:
            key = None
            if not started:
                if self.live_start_cancelled:
                    self.show_request_state("Запросы — отменено до отправки", "Запуск отменён до начала работы. Попыток отправки: 0.")
                self.live_pending = False
                self.set_live_busy(False)
                if self.closing_after_live:
                    self.root.destroy()

    def cancel_live(self):
        self.live_start_cancelled = True
        if self.live_pending and self.live_job is not None:
            self.live_job.cancel()
            self.cancel_button.configure(state="disabled")
            self.status.set("Останавливаю следующие запросы. Жду исход текущего и сохраняю отчёт; списание за отправленный запрос возможно.")
            self.show_request_state("Запросы — остановка", "Остановка запрошена. Исход текущего обращения ещё уточняется; нельзя утверждать, что отправки не было. Итог будет сохранён после завершения.")

    def show_request_state(self, title, text):
        self.tabs.tab(self.draft, text=title)
        self.put(self.draft, text)

    def poll_live(self):
        outcome = self.live_job.poll()
        if outcome is None:
            progress = getattr(self.live_job, "progress_text", None)
            if type(progress) is str and not self.live_start_cancelled:
                self.status.set(progress)
            self.root.after(50, self.poll_live)
            return
        self.live_pending = False
        self.set_live_busy(False)
        self.live_outcome = outcome
        record = outcome["record"]
        self.show_request_state(*request_log(record))
        self.put(self.explanation, result_text(record))
        self.put(self.raw, app.json_text(record))
        self.save_button.configure(state="normal")
        result = record.get("result")
        if record.get("kind") == "PROPERTY_PREPARATION":
            for row in record["rows"]:
                self.table.insert("", "end", values=(property_live.prep.search.LABELS[row["case_id"]],
                    "Свойство принято" if row.get("semantic_validation", {}).get("accepted") else "Не принято / не запускалось",
                    "До / после: " + str(sum(x["confirmed"] for x in row.get("exports", []))) + " тестов",
                    property_live.STATES[row["state"]]))
        elif result:
            checks = [("Исходно", result["initial_check"])] + [("Проба " + t["candidate_id"], t["check"]) for t in result["attempts"] if t["check"]]
            for label, check in checks:
                self.table.insert("", "end", values=(label, app.numeric(check["actual"]), app.numeric(check["expected"]),
                                                      "Не выявлено расхождения" if check["passed"] else "Расхождение"))
        self.status.set(outcome["save_error"] or "Готово. Отдельный отчёт: " + outcome["report"])
        self.tabs.select(0)
        if self.closing_after_live:
            if outcome["save_error"]:
                self.closing_after_live = False
            else:
                self.root.destroy()

    def close(self):
        if self.live_pending:
            from tkinter import messagebox
            if messagebox.askyesno(loc.text("Завершение запуска"), loc.text("Остановить следующие запросы и закрыть окно после сохранения отчёта? Уже отправленный запрос может быть оплачен."), parent=self.root):
                self.closing_after_live = True
                self.cancel_live()
            return
        self.root.destroy()


def main(argv=None):
    argv = loc.cli_args(argv)
    parser = loc.ArgumentParser(description="Локальное окно ReproCase")
    parser.add_argument("--self-test", action="store_true", help="проверить собственные виджеты в скрытом окне")
    args = parser.parse_args(argv)
    # Для нового локального поиска используем уже подготовленное окружение; ключ ещё не вводился.
    isolated = app.ROOT / ".venv-historical/bin/python"
    if importlib.util.find_spec("hypothesis") is None and isolated.is_file():
        return subprocess.run([str(isolated), "-B", str(Path(__file__).resolve()),
                               *argv, *(["--language", loc.cli_language] if loc.cli_language else [])], cwd=app.ROOT).returncode
    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter недоступен. Командный запуск работает через reprocase.py; ничего не установлено.", file=sys.stderr)
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print("Графическое окно недоступно в этой сессии. Используйте reprocase.py; настройки системы не менялись.", file=sys.stderr)
        print("Причина: " + str(error), file=sys.stderr)
        return 2
    if args.self_test:
        loc.cli_language = loc.cli_language or "ru"
        root.withdraw()
    try:
        window = Window(root)
        if args.self_test:
            assert str(window.copy_button["text"]) == loc.text("Копировать результат")
            assert window.copy_button.winfo_manager() == "grid"
            assert int(window.copy_button.grid_info()["column"]) == 4
            assert list(window.theme_selector["values"]) == [loc.text("Светлая"), loc.text("Тёмная")]
            assert window.theme_selector.winfo_manager() == "grid"
            assert int(window.theme_selector.grid_info()["column"]) == 2
            assert window.copy_button.winfo_x() >= window.save_button.winfo_x() + window.save_button.winfo_width()
            assert window.copy_button.winfo_x() + window.copy_button.winfo_reqwidth() <= window.copy_button.master.winfo_reqwidth()
            print("Созданы: «Тема» справа в заголовке; «Копировать результат» справа от «Сохранить отчёт». Раскладка проверена в скрытом окне.")
            for operation in CATALOG:
                window.session.choose(operation)
                window.populate()
                window.run_button.invoke()
                root.update_idletasks()
                assert window.session.evidence is not None
                assert len(window.table.get_children()) == 2
                assert window.session.evidence["results"]["fixed"]["passed"]
                window.prepare_button.invoke()
                root.update_idletasks()
                raw_draft = window.draft.get("1.0", "end-1c")
                draft = json.loads(raw_draft[raw_draft.index("{"):])
                context = json.loads(draft["messages"][1]["content"])
                assert draft["tool_choice"]["function"]["name"] == "diagnostic_action"
                assert context["sample"]["operation"] == operation and context["history"] == []
                assert window.session.evidence is None
                assert str(window.save_button["state"]) == "disabled"
                zero_field = {"travel_time": "distance_mm", "seeded_sequence": "seed", "constant_rate_steps": "rate"}[operation]
                window.entries[zero_field].set("0")
                window.find_button.invoke()
                root.update_idletasks()
                assert window.session.search_result["initial_status"] == "INSUFFICIENT_DATA"
                assert window.session.search_result["status"] == "REPRODUCED"
                assert window.session.search_result["probe_attempts"] <= 3
                first_search = window.session.search_result
                window.find_button.invoke()
                assert window.session.search_result == first_search
                from unittest import mock
                import tempfile
                with mock.patch("tkinter.filedialog.askopenfilename", return_value=""):
                    window.load()
                assert window.session.search_result == first_search
                assert str(window.save_button["state"]) == "normal"
                with tempfile.TemporaryDirectory(dir=app.ROOT) as temp, mock.patch.object(app, "ROOT", Path(temp)):
                    window.save_button.invoke()
                    assert window.session.replay_command
                    before = {str(p.relative_to(temp)): p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()}
                    window.save_button.invoke()
                    assert before == {str(p.relative_to(temp)): p.read_bytes() for p in Path(temp).rglob("*") if p.is_file()}
            window.entries["steps"].set("1")
            window.run_button.invoke()
            window.prepare_button.invoke()
            window.find_button.invoke()
            assert window.session.evidence is None
            assert window.session.search_result is None
            assert len(window.table.get_children()) == 0
            assert not window.draft.get("1.0", "end-1c")
            print("Виджеты проверены: три случая, запуск, предпросмотр, подбор, повторные нажатия и сохранение, отмена выбора файла и отказ при неверном вводе. Визуальная оценка не выполнялась.")
        else:
            root.update_idletasks()
            print("Окно ReproCase запущено. Режим локальный; сеть не включена.", flush=True)
            root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass  # Пользователь мог уже закрыть окно стандартной кнопкой.
    return 0


if __name__ == "__main__":
    sys.exit(main())
