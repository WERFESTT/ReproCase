"""Tk presentation bindings: refresh displayed text without invoking actions."""
import tkinter as tk
import localization as loc


class TextVariable(tk.StringVar):
    def __init__(self, master=None, value='', choices=()):
        self.source = value
        self.choices = tuple(choices)
        super().__init__(master, value=loc.text(value))

    def set(self, value):
        self.source = value
        super().set(loc.text(value))

    def get(self):
        displayed = super().get()
        for original in self.choices:
            if displayed in (original, loc.text(original, 'en')):
                self.source = original
                return original
        return self.source if displayed == loc.text(self.source) else displayed

    def refresh(self):
        if self.choices:
            self.get()  # Capture a selection made by Tk, even after the language changed.
        super().set(loc.text(self.source))


class Binder:
    def __init__(self, window):
        self.window = window
        self.widgets = {}
        self.tab_titles = {}
        self.rows = {}
        self.headings = {}
        self.title = window.root.title()
        table = window.table
        self._insert = table.insert
        self._item = table.item
        self._heading = table.heading
        self._tab = window.tabs.tab
        table.insert = self.insert
        window.tabs.tab = self.tab
        for name in table['columns']:
            self.headings[name] = table.heading(name, 'text')
        for tab in window.tabs.tabs():
            self.tab_titles[str(tab)] = window.tabs.tab(tab, 'text')
        self.scan(window.root)

    def scan(self, widget):
        if 'text' in widget.keys() and widget not in self.widgets:
            self.widgets[widget] = str(widget['text'])
        for child in widget.winfo_children():
            self.scan(child)

    def insert(self, parent, index, iid=None, **kwargs):
        values = kwargs.get('values', ())
        kwargs['values'] = tuple(loc.text(v) for v in values)
        result = self._insert(parent, index, iid, **kwargs)
        self.rows[result] = values
        return result

    def tab(self, tab_id, option=None, **kwargs):
        if 'text' in kwargs:
            # Resolve index/widget to stable Tcl path.
            path = str(tab_id) if str(tab_id) in self.window.tabs.tabs() else self.window.tabs.tabs()[self.window.tabs.index(tab_id)]
            self.tab_titles[path] = kwargs['text']
            kwargs['text'] = loc.text(kwargs['text'])
        return self._tab(tab_id, option, **kwargs)

    def refresh(self):
        w = self.window
        self.scan(w.root)
        w.root.title(loc.text(self.title))
        for widget, source in self.widgets.items():
            if widget.winfo_exists():
                widget.configure(text=loc.text(source))
        for variable in (w.selection, w.property_choice, w.theme_choice, w.task_text, w.status):
            variable.refresh()
        for selector, variable in ((w.selector,w.selection),(w.property_selector,w.property_choice),(w.theme_selector,w.theme_choice)):
            selector.configure(values=tuple(loc.text(v) for v in variable.choices))
        for column, source in self.headings.items():
            self._heading(column, text=loc.text(source))
        for tab, source in self.tab_titles.items():
            self._tab(tab, text=loc.text(source))
        for iid, values in list(self.rows.items()):
            if w.table.exists(iid):
                self._item(iid, values=tuple(loc.text(v) for v in values))
            else:
                del self.rows[iid]
        for i, source in enumerate(('Копировать выделенное','Копировать результат целиком')):
            w.copy_menu.entryconfigure(i, label=loc.text(source))
        for widget, source in list(w._display_sources.items()):
            # Keep scroll position; a language switch does not rerun any task.
            y = widget.yview()
            w.put(widget, source)
            if y:
                widget.yview_moveto(y[0])
