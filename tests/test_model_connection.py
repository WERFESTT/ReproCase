"""Проверки на локальном имитаторе. Настоящая модель и платные сервисы не используются."""

import copy
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import benchmark_adapter
import comparison_series
import input_search
import input_search_adapter
import live_search
import model_client as client
import model_settings as settings_module
from model_settings import ConnectionSettings
import property_contract
import property_live
import property_preparation
import reprocase


def envelope(request, action):
    result = benchmark_adapter.artificial_response(action, request["model"],
                                                   {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = request["tools"][0]["function"]["name"]
    return result


def search_response(request):
    context = json.loads(request["messages"][1]["content"])
    return envelope(request, input_search.fixed_order_selector(context))


def property_response(request):
    context = json.loads(request["messages"][1]["content"])
    case = property_contract.OPERATION_CASES[context["operation"]]
    baseline = property_preparation.core.read_json(property_preparation.ROOT / "property_protocol.json")["manual_baseline"][case]
    return envelope(request, property_preparation.proposed(case, baseline["assertion"], baseline["domain"]))


class LocalServer:
    def __init__(self, respond):
        self.requests = []
        self.errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                request = json.loads(raw)
                owner.requests.append({"path": self.path, "headers": dict(self.headers), "body": request, "bytes": len(raw)})
                try:
                    result = respond(request)
                except Exception as error:
                    owner.errors.append(type(error).__name__)
                    result = (500, {}, {})
                status, body, headers = result if isinstance(result, tuple) else (200, result, {})
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/custom/v1/chat/completions"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        if self.errors:
            raise AssertionError("Ошибка имитатора сервера: " + ", ".join(self.errors))

    def settings(self, **kwargs):
        return ConnectionSettings(self.endpoint, "local-test/arbitrary-model", **kwargs)


class SettingsTests(unittest.TestCase):
    def test_supported_addresses_and_immutable_selection(self):
        for url in ("http://localhost:8000/v1/chat/completions", "http://127.0.0.1:9000/complete",
                    "http://[::1]:8000/v1/chat/completions", "https://models.example.net/api/chat/completions"):
            with self.subTest(url=url):
                settings = ConnectionSettings(url, "owner/custom-model:version")
                with self.assertRaises(FrozenInstanceError):
                    settings.model = "changed"

    def test_bad_addresses_are_rejected_locally(self):
        addresses = ("", "ftp://localhost/path", "http://models.example.net/v1/chat/completions",
                     "http://192.168.1.2/v1/chat/completions", "https://user:password@example.net/path",
                     "https://example.net/path?key=secret", "https://example.net/path#secret",
                     "https://model-api.invalid/path", "https://invalid/path", "https://EXAMPLE.INVALID./path",
                     "https://example.net/path\r\nHeader:value", "https://example.net:70000/path",
                     "https://example.net", "https://bad_host/path", "http://127.0.0.1.example.net/path",
                     "http://127.1/path", "http://[::1%25zone]/path", "https://example.net\\other/path")
        with mock.patch.object(client.urllib.request.OpenerDirector, "open") as send:
            for address in addresses:
                with self.subTest(address=address), self.assertRaises(client.ClientError):
                    ConnectionSettings(address, "local-model")
            send.assert_not_called()

    def test_invalid_models_and_modes(self):
        for value in (None, "", "bad model", "bad\nmodel", 3, False, "x" * 257):
            with self.subTest(model=value), self.assertRaises(client.ClientError):
                ConnectionSettings("http://127.0.0.1/v1/chat/completions", value)
        for options in ({"auth": "basic"}, {"token_parameter": "unlimited"}):
            with self.assertRaises(client.ClientError):
                ConnectionSettings("https://models.example.net/path", "test", **options)

    def test_save_load_without_secret_or_request(self):
        settings = ConnectionSettings("https://models.example.net/v1/chat/completions", "my-model", "bearer")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(client.urllib.request.OpenerDirector, "open") as send:
            path = Path(directory) / "settings.json"
            self.assertIsNone(settings_module.load_settings(path))
            settings_module.save_settings(settings, path)
            self.assertEqual(settings, settings_module.load_settings(path))
            self.assertEqual(set(json.loads(path.read_text())), {"endpoint", "model", "auth", "token_parameter"})
            for text in ('{"endpoint":"one","endpoint":"two"}',
                         json.dumps(dict(settings.as_dict(), api_key="must-not-load")), "x" * 5000):
                path.write_text(text)
                with self.assertRaises(client.ClientError):
                    settings_module.load_settings(path)
            send.assert_not_called()

    def test_absent_settings_and_disabled_client_do_not_connect(self):
        with mock.patch.object(client.urllib.request.OpenerDirector, "open") as send:
            with self.assertRaises(client.ClientError):
                client.OneShotClient(allow_network=True)
            with self.assertRaises(client.ClientError):
                live_search.LiveJob()
            with self.assertRaises(client.NetworkDisabled):
                client.OneShotClient().request_proposal(reprocase.load_case(reprocase.DEFAULT_CASE))
            send.assert_not_called()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.case = reprocase.load_case(reprocase.DEFAULT_CASE)

    def response(self, request):
        result = client.artificial_response(self.case)
        result["model"] = request["model"]
        return result

    def test_real_loopback_http_with_both_auth_and_token_modes(self):
        for auth, key in (("none", None), ("bearer", "test-key-only-in-memory")):
            for parameter in settings_module.TOKEN_PARAMETERS:
                with self.subTest(auth=auth, parameter=parameter), LocalServer(self.response) as server:
                    settings = server.settings(auth=auth, token_parameter=parameter)
                    session = client.OneShotClient(allow_network=True, settings=settings)
                    reply = session.request_proposal(self.case, key)
                    self.assertEqual(reply.model, settings.model)
                    self.assertIsNone(reply.usage)
                    self.assertEqual(len(server.requests), 1)
                    request = server.requests[0]
                    self.assertEqual(request["path"], "/custom/v1/chat/completions")
                    self.assertEqual(request["body"][parameter], client.MAX_COMPLETION_TOKENS)
                    self.assertNotIn(next(p for p in settings_module.TOKEN_PARAMETERS if p != parameter), request["body"])
                    self.assertEqual(request["headers"].get("Authorization"), "Bearer " + key if key else None)
                    with self.assertRaises(client.ClientError):
                        session.request_proposal(self.case, key)
                    self.assertEqual(len(server.requests), 1)

    def test_https_retains_certificate_verification(self):
        settings = ConnectionSettings("https://models.example.net/v1/chat/completions", "configured", "bearer")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"{}"
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(client.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(client._https_once(b"{}", "test-key", settings=settings), b"{}")
        handler = next(h for h in build.call_args.args if isinstance(h, client.urllib.request.HTTPSHandler))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, settings.endpoint)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

    def test_bad_keys_do_not_consume_attempt_or_send(self):
        with LocalServer(self.response) as server:
            for key in (None, "", "space key", "line\nbreak", "x" * 513):
                session = client.OneShotClient(allow_network=True, settings=server.settings(auth="bearer"))
                with self.assertRaises(client.ClientError):
                    session.request_proposal(self.case, key)
                self.assertFalse(session.attempted)
            session = client.OneShotClient(allow_network=True, settings=server.settings())
            with self.assertRaises(client.ClientError):
                session.request_proposal(self.case, "unexpected-key")
            self.assertEqual(server.requests, [])

    def test_redirect_does_not_receive_a_second_request(self):
        with LocalServer(lambda _: (302, {}, {"Location": "/second-target"})) as server:
            session = client.OneShotClient(allow_network=True, settings=server.settings(auth="bearer"))
            with self.assertRaises(client.ClientError):
                session.request_proposal(self.case, "test-key")
            with self.assertRaises(client.ClientError):
                session.request_proposal(self.case, "test-key")
            self.assertEqual(len(server.requests), 1)

    def test_http_failures_stop_without_retry_or_raw_error_leak(self):
        for code in (401, 429, 500):
            with self.subTest(code=code), LocalServer(lambda _: (code, b"test-key secret response", {})) as server:
                session = client.OneShotClient(allow_network=True, settings=server.settings(auth="bearer"))
                with self.assertRaises(client.ClientError) as error:
                    session.request_proposal(self.case, "test-key")
                self.assertNotIn("test-key", str(error.exception))
                self.assertNotIn("secret response", str(error.exception))
                with self.assertRaises(client.ClientError):
                    session.request_proposal(self.case, "test-key")
                self.assertEqual(len(server.requests), 1)

    def test_oversized_response_stops_without_retry(self):
        with LocalServer(lambda _: b"x" * (client.MAX_RESPONSE_BYTES + 1)) as server:
            session = client.OneShotClient(allow_network=True, settings=server.settings())
            with self.assertRaises(client.ClientError):
                session.request_proposal(self.case)
            self.assertTrue(session.attempted)
            self.assertEqual(len(server.requests), 1)

    def test_response_contract_and_local_action_validation_remain_strict(self):
        original = client.artificial_response(self.case)
        bad = []
        item = copy.deepcopy(original); item["model"] = "other-model"; bad.append(item)
        item = copy.deepcopy(original); item["choices"] *= 2; bad.append(item)
        item = copy.deepcopy(original); item["choices"][0]["finish_reason"] = "stop"; bad.append(item)
        item = copy.deepcopy(original); item["choices"][0]["message"]["content"] = "extra text"; bad.append(item)
        item = copy.deepcopy(original); item["choices"][0]["message"]["tool_calls"] *= 2; bad.append(item)
        item = copy.deepcopy(original); item["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "execute_code"; bad.append(item)
        item = copy.deepcopy(original); item["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"command":"ignored"}'; bad.append(item)
        item = copy.deepcopy(original); item["usage"] = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 0}; bad.append(item)
        for number, payload in enumerate(bad):
            with self.subTest(number=number), self.assertRaises(client.ClientError):
                client.decode_response(json.dumps(payload), self.case)
        with self.assertRaises(client.ClientError):
            client.decode_response('{"model":"one","model":"two"}', self.case)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        (reprocase.ROOT / "outputs").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=reprocase.ROOT / "outputs")
        self.addCleanup(self.directory.cleanup)
        self.case = reprocase.load_case(reprocase.ROOT / "examples/search_units_ambiguous.json")

    def outcome(self, job):
        job.thread.join(10)
        self.assertFalse(job.thread.is_alive())
        outcome = job.poll()
        self.assertIsNotNone(outcome)
        self.assertIsNone(outcome["save_error"])
        return outcome["record"]

    def test_live_search_records_selected_connection_usage_and_local_replay(self):
        with LocalServer(search_response) as server:
            job = live_search.LiveJob(self.directory.name, settings=server.settings(auth="bearer"))
            job.start(self.case, "test-key-only-in-memory", confirmed=True)
            record = self.outcome(job)
            self.assertEqual(record["result"]["status"], "REPRODUCED")
            self.assertTrue(record["local_replay"])
            self.assertEqual(record["connection"], job.settings.as_dict())
            self.assertEqual(record["accounting"]["network_attempts"], len(server.requests))
            self.assertLessEqual(len(server.requests), 4)
            self.assertIsNone(record["cost"]["estimate_usd_range"])
            for request in server.requests:
                self.assertEqual(request["body"]["model"], job.settings.model)
                self.assertEqual(request["body"]["max_tokens"], 1024)
                self.assertLessEqual(request["bytes"], 16000)
            for path in Path(self.directory.name).rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"test-key-only-in-memory", path.read_bytes())

    def test_no_consent_or_prestart_cancellation_sends_nothing(self):
        with LocalServer(search_response) as server:
            job = live_search.LiveJob(self.directory.name, settings=server.settings())
            with self.assertRaises(client.ClientError):
                job.start(self.case)
            self.assertFalse(job.started)
            job.cancel()
            job.start(self.case, confirmed=True)
            record = self.outcome(job)
            self.assertEqual(record["accounting"]["network_attempts"], 0)
            self.assertEqual(server.requests, [])
            with self.assertRaises(client.ClientError):
                job.start(self.case, confirmed=True)

    def test_series_uses_the_same_settings_and_stops_at_first_error(self):
        with LocalServer(lambda _: (503, {}, {})) as server:
            settings = server.settings()
            job = comparison_series.SeriesJob(self.directory.name, settings=settings)
            job.start(comparison_series.load_plan(settings), confirmed=True)
            record = self.outcome(job)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(record["state"], "ERROR")
            self.assertEqual(sum(row["state"] == "NOT_RUN" for row in record["rows"]), 4)
            self.assertEqual(record["connection"], settings.as_dict())

    def test_full_five_case_series_keeps_request_budget(self):
        with LocalServer(search_response) as server:
            settings = server.settings(token_parameter="max_completion_tokens")
            job = comparison_series.SeriesJob(self.directory.name, settings=settings)
            job.start(comparison_series.load_plan(settings), confirmed=True)
            record = self.outcome(job)
            self.assertEqual(record["state"], "COMPLETED")
            self.assertEqual(len(record["rows"]), 5)
            self.assertLessEqual(len(server.requests), 20)
            self.assertEqual(record["accounting"]["network_attempts"], len(server.requests))
            self.assertTrue(all(r["body"]["model"] == settings.model and r["body"]["max_completion_tokens"] == 1024
                                for r in server.requests))

    def test_series_rejects_a_plan_for_a_different_connection(self):
        settings = ConnectionSettings("http://127.0.0.1:8000/v1/chat/completions", "selected")
        changed = ConnectionSettings(settings.endpoint, "different")
        job = comparison_series.SeriesJob(self.directory.name, settings=settings)
        with self.assertRaises(client.ClientError):
            job.start(comparison_series.load_plan(changed), confirmed=True)
        self.assertFalse(job.started)

    def test_property_session_accepts_selected_model_once(self):
        with LocalServer(property_response) as server:
            settings = server.settings()
            budget = property_live.Budget(1)
            session = property_live.PropertySession(threading.Event(), budget, settings)
            context = property_contract.context("cpython_correlation")
            proposal = session.request_action(context)
            self.assertTrue(property_contract.semantic_validation("cpython_correlation", proposal)["accepted"])
            self.assertTrue(session.response_received)
            self.assertEqual(budget.used, 1)
            with self.assertRaises(client.ClientError):
                session.request_action(context)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(server.requests[0]["body"]["model"], settings.model)
            preview = property_live.preview(property_live.make_plan(), settings)
            self.assertIn(reprocase.json_text(session._build_request(context)), preview)
            self.assertIn(settings.endpoint, preview)

    def test_property_job_cancellation_preserves_six_request_plan(self):
        def respond(request):
            job.cancel()
            return property_response(request)
        with LocalServer(respond) as server:
            job = property_live.PropertyJob(self.directory.name, settings=server.settings())
            # Только зависимости поиска заменены: проверяются настоящий HTTP и остановка до поиска.
            with mock.patch.object(property_live, "check_requirements"):
                job.start(property_live.make_plan(2), None, confirmed=True, approved_requests=6)
            record = self.outcome(job)
            self.assertEqual(record["state"], "CANCELLED")
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(record["metrics"]["not_started"], 5)
            self.assertEqual(record["connection"], job.settings.as_dict())

    def test_missing_search_dependency_fails_before_network(self):
        with mock.patch.dict(sys.modules, {"hypothesis": None}), mock.patch.object(client, "_https_once") as send:
            with self.assertRaises(client.ClientError):
                property_live.check_requirements()
            send.assert_not_called()

    def test_new_portable_export_includes_connection_module_and_replays(self):
        case = "cpython_correlation"
        proposal = property_preparation.proposed(case, {"op": "equal_number", "value": 1})
        run = property_preparation.run_search(case, proposal, "boundary_grid", "before", 0)
        self.assertEqual(run["outcome"], "REGRESSION_CONFIRMED")
        destination = Path(self.directory.name) / "portable"
        result = property_live.export_test({"origin": "SYNTHETIC", "case_id": case, "seed": 0, "proposal": proposal}, run, destination)
        self.assertTrue((destination / "model_settings.py").is_file())
        self.assertTrue(result["confirmed"], result)


class LauncherTests(unittest.TestCase):
    def test_default_launcher_enforces_offline_audit_hook(self):
        code = ("import runpy,sys,socket; sys.argv=['judge.py','verify']\n"
                "try: runpy.run_path('judge.py',run_name='__main__')\n"
                "except SystemExit as error:\n"
                " if error.code: raise\n"
                "try: socket.socket().connect(('127.0.0.1',1))\n"
                "except PermissionError: print('OFFLINE_BLOCK_CONFIRMED')\n"
                "else: raise AssertionError('network audit hook missing')\n"
                "import desktop,tkinter\nfrom unittest import mock\n"
                "with mock.patch.object(desktop.importlib.util,'find_spec',return_value=None), "
                "mock.patch('pathlib.Path.is_file',return_value=True), "
                "mock.patch.object(desktop.subprocess,'run',side_effect=AssertionError('unexpected interpreter restart')), "
                "mock.patch('tkinter.Tk',side_effect=tkinter.TclError('headless')):\n"
                " assert desktop.main([])==2\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE_BLOCK_CONFIRMED", result.stdout)

    def test_network_flag_cannot_enable_other_modes(self):
        result = subprocess.run([sys.executable, "-I", "-B", "judge.py", "verify", "--allow-network"],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
