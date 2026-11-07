"""Редактор параметров подключения; не запрашивает ключ и не проверяет сервер по сети."""

import localization as loc
import model_settings


def edit_settings(parent, current=None):
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    class ConnectionDialog(simpledialog.Dialog):
        def body(self, master):
            master.columnconfigure(1, weight=1)
            self.endpoint = tk.StringVar(value=current.endpoint if current else "")
            self.model = tk.StringVar(value=current.model if current else "")
            labels = ("Без ключа", "Ключ доступа (Bearer)")
            self.auth_labels = tuple(loc.text(label) for label in labels)
            self.auth = tk.StringVar(value=self.auth_labels[bool(current and current.requires_key)])
            self.tokens = tk.StringVar(value=current.token_parameter if current else "max_tokens")
            fields = (("Полный адрес сервера:", self.endpoint), ("Идентификатор модели:", self.model))
            first = None
            for row, (label, variable) in enumerate(fields):
                ttk.Label(master, text=loc.text(label)).grid(row=row, column=0, sticky="w", padx=8, pady=6)
                entry = ttk.Entry(master, textvariable=variable, width=62)
                entry.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
                first = first or entry
            for row, label, variable, choices in (
                    (2, "Авторизация:", self.auth, self.auth_labels),
                    (3, "Параметр лимита ответа:", self.tokens, model_settings.TOKEN_PARAMETERS)):
                ttk.Label(master, text=loc.text(label)).grid(row=row, column=0, sticky="w", padx=8, pady=6)
                ttk.Combobox(master, textvariable=variable, values=choices, state="readonly", width=30).grid(
                    row=row, column=1, sticky="w", padx=8, pady=6)
            note = ("HTTPS для удалённого сервера; HTTP только на этом компьютере.\n"
                    "Пример адреса: http://127.0.0.1:8000/v1/chat/completions\n"
                    "Модель должна поддерживать вызов функций в формате chat/completions.\n"
                    "Ключ вводится перед запуском и не сохраняется. Сохранение настроек не отправляет запрос.")
            ttk.Label(master, text=loc.text(note), wraplength=650).grid(row=4, column=0, columnspan=2,
                                                                       sticky="w", padx=8, pady=12)
            return first

        def buttonbox(self):
            box = ttk.Frame(self)
            ttk.Button(box, text=loc.text("Сохранить"), command=self.ok).pack(side="left", padx=6, pady=8)
            ttk.Button(box, text=loc.text("Отмена"), command=self.cancel).pack(side="left", padx=6, pady=8)
            self.bind("<Return>", self.ok)
            self.bind("<Escape>", self.cancel)
            box.pack()

        def validate(self):
            try:
                settings = model_settings.ConnectionSettings(
                    endpoint=self.endpoint.get().strip(), model=self.model.get().strip(),
                    auth="bearer" if self.auth.get() == self.auth_labels[1] else "none",
                    token_parameter=self.tokens.get())
                model_settings.save_settings(settings)
            except model_settings.ClientError as error:
                messagebox.showerror(loc.text("Подключение модели"), loc.text(str(error)), parent=self)
                return False
            except OSError:
                messagebox.showerror(loc.text("Подключение модели"),
                                     loc.text("Не удалось сохранить настройки подключения. Проверьте права записи."), parent=self)
                return False
            self.result = settings
            return True

    return ConnectionDialog(parent, loc.text("Подключение модели")).result
