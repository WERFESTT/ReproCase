"""Две локальные темы Tk: только оформление и одна сохраняемая настройка."""
import json
import os
from pathlib import Path
import tempfile


SETTINGS_PATH = Path(__file__).resolve().parent / "ui_settings.json"
LABELS = {"light": "Светлая", "dark": "Тёмная"}
PALETTES = {
    "light": dict(bg="#f3f4f6", panel="#e5e7eb", field="#ffffff", fg="#111827",
                  muted="#4b5563", border="#6b7280", focus="#1d4ed8",
                  select="#1d4ed8", selected_fg="#ffffff", active="#dbeafe",
                  disabled="#4b5563", disabled_bg="#e5e7eb", warning="#854d0e",
                  fail_bg="#fee2e2", fail_fg="#7f1d1d", pass_bg="#dcfce7", pass_fg="#14532d"),
    "dark": dict(bg="#111827", panel="#273449", field="#182235", fg="#f3f4f6",
                 muted="#cbd5e1", border="#94a3b8", focus="#93c5fd",
                 select="#1d4ed8", selected_fg="#ffffff", active="#374b67",
                 disabled="#cbd5e1", disabled_bg="#273449", warning="#fde68a",
                 fail_bg="#54232e", fail_fg="#fecdd3", pass_bg="#193e31", pass_fg="#bbf7d0"),
}


def load_setting(path=None):
    """Ограниченный JSON; неизвестные поля не принимаются и не используются."""
    path = SETTINGS_PATH if path is None else Path(path)
    try:
        if path.is_symlink():
            raise ValueError("ссылка вместо настройки")
        with path.open("rb") as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError("слишком большой файл")
        def unique_object(pairs):
            if len(dict(pairs)) != len(pairs):
                raise ValueError("повтор поля")
            return dict(pairs)
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
        if type(value) is not dict or set(value) != {"theme"} or value["theme"] not in ("light", "dark"):
            raise ValueError("неверная настройка")
        return value["theme"], None
    except FileNotFoundError:
        return "light", None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return "light", "Настройка темы недоступна или неверна; выбрана светлая тема."


def save_setting(theme, path=None):
    """Атомарно сохранить только тему; содержимое окна сюда не передаётся."""
    if theme not in LABELS:
        raise ValueError("Неизвестная тема")
    path = SETTINGS_PATH if path is None else Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".reprocase-theme-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({"theme": theme}, ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def apply_theme(root, theme):
    """Меняется только этот интерпретатор Tk, не настройки рабочего стола."""
    from tkinter import ttk
    p = PALETTES[theme]
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")  # Встроенная переносимая тема позволяет задать все цвета.
    style.configure(".", background=p["bg"], foreground=p["fg"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], focuscolor=p["focus"],
                    selectbackground=p["select"], selectforeground=p["selected_fg"])
    style.configure("Warning.TLabel", foreground=p["warning"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("TButton", background=p["panel"], padding=(8, 5))
    style.map("TButton", background=[("disabled", p["disabled_bg"]), ("active", p["active"])],
              foreground=[("disabled", p["disabled"])],
              bordercolor=[("focus", p["focus"])])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=p["field"], background=p["panel"],
                        foreground=p["fg"], insertcolor=p["fg"], arrowcolor=p["fg"])
        style.map(name, fieldbackground=[("disabled", p["disabled_bg"]), ("readonly", p["field"])],
                  foreground=[("disabled", p["disabled"]), ("readonly", p["fg"])],
                  selectbackground=[("!disabled", p["select"])],
                  selectforeground=[("!disabled", p["selected_fg"])],
                  bordercolor=[("focus", p["focus"])], arrowcolor=[("disabled", p["disabled"])])
    style.configure("TNotebook.Tab", background=p["panel"], padding=(10, 5))
    style.map("TNotebook.Tab", background=[("selected", p["field"]), ("active", p["active"])],
              foreground=[("disabled", p["disabled"]), ("selected", p["fg"])])
    style.configure("Treeview", fieldbackground=p["field"], background=p["field"],
                    foreground=p["fg"], rowheight=26)
    style.map("Treeview", background=[("selected", p["select"])],
              foreground=[("selected", p["selected_fg"])])
    style.configure("Treeview.Heading", background=p["panel"], foreground=p["fg"])
    style.map("Treeview.Heading", background=[("active", p["active"])])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=p["panel"], troughcolor=p["bg"], arrowcolor=p["fg"])
        style.map(name, background=[("active", p["active"])])

    # Стандартный askstring использует обычные Tk-виджеты. Правила действуют
    # также для будущего окна ввода, не читают его текст и не меняют show="*".
    defaults = dict(background=p["bg"], foreground=p["fg"], activeBackground=p["active"],
                    activeForeground=p["fg"], disabledForeground=p["disabled"],
                    selectBackground=p["select"], selectForeground=p["selected_fg"],
                    insertBackground=p["fg"], highlightBackground=p["border"], highlightColor=p["focus"])
    for option, value in defaults.items():
        root.option_add("*" + option, value)
    for klass in ("Entry", "Text", "Listbox", "TCombobox*Listbox"):
        root.option_add("*" + klass + ".background", p["field"])
    root.option_add("*Button.background", p["panel"])
    root.option_add("*Entry.disabledBackground", p["disabled_bg"])
    root.option_add("*Entry.readonlyBackground", p["field"])

    def recolor(widget):
        if not isinstance(widget, ttk.Widget):
            colors = {key.lower(): value for key, value in defaults.items()}
            klass = widget.winfo_class()
            if klass in ("Entry", "Text", "Listbox"):
                colors["background"] = p["field"]
            elif klass in ("Button", "Menu", "Scrollbar"):
                colors["background"] = p["panel"]
            colors.update(disabledbackground=p["disabled_bg"], readonlybackground=p["field"],
                          troughcolor=p["bg"])
            keys = widget.keys()
            widget.configure(**{k: v for k, v in colors.items() if k in keys})
        for child in widget.winfo_children():
            recolor(child)
    recolor(root)
    return p
