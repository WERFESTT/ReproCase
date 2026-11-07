"""Решения контроллера окна без графического дисплея; это не проверка отрисовки."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import desktop
import comparison_series
import live_search
from model_settings import ClientError, ConnectionSettings
import property_live


def window(settings):
    case = desktop.app.load_case(desktop.app.DEFAULT_CASE)
    return SimpleNamespace(
        live_pending=False, connection_settings=settings, root=mock.Mock(), status=mock.Mock(),
        session=SimpleNamespace(case=case, update=mock.Mock()),
        property_choice=SimpleNamespace(get=lambda: property_live.OPTIONS[0]),
        values=lambda: {name: str(value) for name, value in case["inputs"].items()},
        clear=mock.Mock(), fail=mock.Mock(), set_live_busy=mock.Mock(),
        show_request_state=mock.Mock(), poll_live=mock.Mock())


class WindowFlowTests(unittest.TestCase):
    def test_selected_settings_and_optional_key_reach_every_workflow(self):
        for auth in ("none", "bearer"):
            settings = ConnectionSettings("http://127.0.0.1:8000/v1/chat/completions", "my-model", auth)
            for module, name, options in ((live_search, "LiveJob", {}),
                                          (comparison_series, "SeriesJob", {"series": True}),
                                          (property_live, "PropertyJob", {"property_mode": True})):
                with self.subTest(auth=auth, workflow=name), \
                     mock.patch("tkinter.messagebox.askyesno", return_value=True) as consent, \
                     mock.patch("tkinter.simpledialog.askstring", return_value="memory-key") as key, \
                     mock.patch.object(property_live, "check_requirements"), \
                     mock.patch.object(module, name) as factory:
                    current = window(settings)
                    desktop.Window.start_live(current, **options)
                    current.fail.assert_not_called()
                    self.assertTrue(current.live_pending)
                    self.assertIn(settings.endpoint, consent.call_args.args[1])
                    self.assertIn(settings.model, consent.call_args.args[1])
                    factory.assert_called_once_with(settings=settings)
                    start = factory.return_value.start.call_args
                    self.assertEqual(start.args[1], "memory-key" if settings.requires_key else None)
                    self.assertTrue(start.kwargs["confirmed"])
                    if options.get("property_mode"):
                        self.assertEqual(start.kwargs["approved_requests"], 1)
                    if settings.requires_key:
                        key.assert_called_once()
                        self.assertEqual(key.call_args.kwargs["show"], "*")
                    else:
                        key.assert_not_called()

    def test_declined_confirmation_creates_no_job_and_asks_for_no_key(self):
        settings = ConnectionSettings("https://models.example.net/v1/chat/completions", "selected", "bearer")
        current = window(settings)
        with mock.patch("tkinter.messagebox.askyesno", return_value=False), \
             mock.patch("tkinter.simpledialog.askstring") as key, mock.patch.object(live_search, "LiveJob") as factory:
            desktop.Window.start_live(current)
            key.assert_not_called()
            factory.assert_not_called()
            self.assertFalse(current.live_pending)

    def test_cancelled_key_entry_creates_no_job(self):
        current = window(ConnectionSettings("https://models.example.net/v1/chat/completions", "selected", "bearer"))
        with mock.patch("tkinter.messagebox.askyesno", return_value=True), \
             mock.patch("tkinter.simpledialog.askstring", return_value=None), \
             mock.patch.object(live_search, "LiveJob") as factory:
            desktop.Window.start_live(current)
            factory.assert_not_called()
            self.assertFalse(current.live_pending)

    def test_missing_settings_fail_before_consent_or_key(self):
        current = window(None)
        with mock.patch("tkinter.messagebox.askyesno") as consent, \
             mock.patch("tkinter.simpledialog.askstring") as key:
            desktop.Window.start_live(current)
            consent.assert_not_called()
            key.assert_not_called()
            current.fail.assert_called_once()
            self.assertFalse(current.live_pending)

    def test_missing_local_dependencies_fail_before_consent_or_key(self):
        current = window(ConnectionSettings("http://127.0.0.1:8000/v1/chat/completions", "selected"))
        with mock.patch("tkinter.messagebox.askyesno") as consent, \
             mock.patch("tkinter.simpledialog.askstring") as key, \
             mock.patch.object(property_live, "check_requirements", side_effect=ClientError("Локальная зависимость недоступна.")):
            desktop.Window.start_live(current, property_mode=True)
            consent.assert_not_called()
            key.assert_not_called()
            current.fail.assert_called_once()


if __name__ == "__main__":
    unittest.main()
