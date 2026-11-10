"""Credential-free client and one-shot boundary checks; no live provider."""

import asyncio
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import image_mcp_probe as probe


def response(payload):
    answers = {}
    for name, question in payload["questions"].items():
        kind = question["type"]
        answer = {"type": kind}
        if kind == "noul":
            answer["noul"] = 0.87
        elif kind == "box":
            answer.update(box=dict(probe.fixture.RECT), confidence=0.8)
        elif kind == "choice":
            color = "red" if "target" in payload["questions"] else "gray"
            answer.update(choice=color, probabilities={k: int(k == color) for k in question["criteria"]}, confidence=1)
        else:
            answer.update(score=1, legend={"0": "dark", "1": "medium", "2": "bright"},
                          probabilities={"0": 0, "1": 1, "2": 0}, confidence=1)
        answers[name] = answer
    return {"isError": False, "structuredContent": {"model": probe.MODEL, "answers": answers,
                                                   "usage": {"input_tokens": 10, "output_tokens": 4}}}


class FakeClient:
    def __init__(self, *, fail=None):
        self.calls = []
        self.closed = False
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def send(self, value):
        self.notification = value

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == self.fail:
            raise TimeoutError("untrusted secret provider error")
        if method == "initialize":
            return {"protocolVersion": probe.PROTOCOL, "capabilities": {"tools": {}}}
        if method == "tools/list":
            return {"tools": [{"name": "evaluate", "inputSchema": {
                "properties": {"assets": {}, "allow_images": {}}}}]}
        return response(params["arguments"])


class ProbeTests(unittest.TestCase):
    def memory_run(self, client, writer=None):
        stream = io.StringIO()
        captured = []

        def write(_stream, report):
            captured.append(copy.deepcopy(report))
            if writer:
                writer(report)

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic"}, clear=True), patch.object(
                probe.fixture, "reserve_output", return_value=stream), patch.object(
                probe.fixture, "write_report", side_effect=write):
            code = probe.run("unused", live=True, client_factory=lambda: client)
        return code, captured

    def test_offline_default_and_missing_auth_never_start(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(probe, "cases") as fixtures:
            self.assertEqual(probe.run(None), 0)
            self.assertEqual(probe.run(None, live=True), 2)
            fixtures.assert_not_called()

    def test_both_cases_handshake_whitelist_and_raw_presence(self):
        client = FakeClient()
        with patch.object(probe.fixture, "supports_safe_output", return_value=False):
            code, reports = self.memory_run(client)
        self.assertEqual(code, 0)
        self.assertTrue(client.closed)
        self.assertEqual([m for m, _ in client.calls], ["initialize", "tools/list", "tools/call", "tools/call"])
        report = reports[-1]
        self.assertEqual(report["calls_started"], 2)
        self.assertEqual([c["fixture"]["format"] for c in report["cases"]], ["png", "jpeg"])
        self.assertEqual(report["cases"][0]["answers"]["visible"]["noul"], 0.87)
        self.assertNotIn("legend", report["cases"][0]["answers"]["brightness"])
        self.assertTrue(report["cases"][0]["fixture_checks"]["geometry_matches"])

    def test_failures_stop_no_second_call_cleanup_and_redaction(self):
        for fail in ("initialize", "tools/list", "tools/call"):
            with self.subTest(fail=fail):
                client = FakeClient(fail=fail)
                code, reports = self.memory_run(client)
                self.assertEqual(code, 1)
                self.assertTrue(client.closed)
                self.assertLessEqual(reports[-1]["calls_started"], 1)
                self.assertNotIn("untrusted", json.dumps(reports))

    def test_persistence_before_start_and_after_call_stop(self):
        for when in (0, 1):
            client = FakeClient()

            def writer(report):
                if report["calls_started"] == when and (when == 0 or report["cases"]):
                    raise OSError("disk")

            with self.assertRaises(OSError):
                self.memory_run(client, writer)
            sends = [m for m, _ in client.calls if m == "tools/call"]
            self.assertEqual(len(sends), when)
            if when:
                self.assertTrue(client.closed)

    def test_missing_capability_never_starts(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic"}, clear=True), patch.object(
                probe.fixture, "supports_safe_output", return_value=False), self.assertRaises(OSError):
            probe.run("unused", live=True, client_factory=lambda: self.fail("started"))

    @unittest.skipUnless(probe.fixture.supports_safe_output(), "POSIX output required")
    def test_marker_before_start_duplicate_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            client = FakeClient()

            def start():
                report = json.loads(output.read_text())
                self.assertEqual(report["calls_started"], 0)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                return client

            with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic"}, clear=True):
                self.assertEqual(probe.run(output, live=True, client_factory=start), 0)
                with self.assertRaises(FileExistsError):
                    probe.run(output, live=True, client_factory=lambda: self.fail("duplicate"))
                link = Path(directory) / "link"
                link.symlink_to(directory, target_is_directory=True)
                with self.assertRaises(OSError):
                    probe.run(link / "other", live=True, client_factory=lambda: self.fail("symlink"))

    def test_malformed_answers_and_fixture_mismatch(self):
        payload, expected = probe.cases()[0]
        for mutate in (
            lambda v: v.update(isError=True),
            lambda v: v["structuredContent"].update(model="other"),
            lambda v: v["structuredContent"]["answers"]["target"]["box"].update(left=True),
            lambda v: v["structuredContent"]["answers"]["color"].update(choice="provider-secret"),
            lambda v: v["structuredContent"]["answers"]["brightness"].update(score=float("nan")),
        ):
            value = response(payload)
            mutate(value)
            with self.assertRaises((ValueError, probe.core.APIError)):
                probe.select_result(value, payload, expected)
        value = response(payload)
        value["structuredContent"]["answers"]["target"]["box"]["left"] = 0.1
        self.assertFalse(probe.select_result(value, payload, expected)["fixture_checks"]["geometry_matches"])

    def test_environment_fixed_and_proxy_free(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "secret", "PYTHONPATH": "evil"}):
            env = probe.child_environment("synthetic")
        self.assertEqual(env["TYPESAFE_BASE_URL"], "https://api.typesafe.ai")
        self.assertEqual(env["TYPESAFE_MAX_RETRIES"], "0")
        self.assertEqual(env["TYPESAFE_TIMEOUT_SECONDS"], "60")
        self.assertEqual(env["TYPESAFE_MAX_RESPONSE_BYTES"], "1048576")
        self.assertEqual(env["NO_PROXY"], "*")
        self.assertFalse(any(k.lower() in ("http_proxy", "https_proxy", "all_proxy") for k in env))
        self.assertNotIn("PYTHONPATH", env)


class StdioTests(unittest.TestCase):
    def test_real_stdio_both_images_with_loopback_provider(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        prepared = probe.cases()
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                payload = prepared[len(received) - 1][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response(payload)["structuredContent"]).encode())

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = probe.child_environment

        def local_environment(key):
            env = original(key)
            env["TYPESAFE_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
            return env

        try:
            stream = io.StringIO()
            report = {"calls_started": 0, "cases": []}

            async def check():
                with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic"}, clear=True), patch.object(
                        probe, "child_environment", side_effect=local_environment), patch.object(
                        probe.fixture, "write_report"):
                    await probe.exchange(report, stream, prepared, probe.StdioClient)

            asyncio.run(check())
            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(received), 2)
            self.assertIn(b"image/png", received[0])
            self.assertIn(b"image/jpeg", received[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_real_child_handshake_catalog_cleanup_without_provider(self):
        async def check():
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic"}, clear=True):
                async with probe.StdioClient() as client:
                    initialized = await client.call("initialize", {"protocolVersion": probe.PROTOCOL})
                    self.assertEqual(initialized["protocolVersion"], probe.PROTOCOL)
                    catalog = await client.call("tools/list", {})
                    self.assertIn("evaluate", [t["name"] for t in catalog["tools"]])
                self.assertEqual(client.process.returncode, 0)
        asyncio.run(check())

    def test_bad_protocol_eof_timeout_and_frame_limit(self):
        async def check():
            for raw in (b"", b"{}\n", b"not-json\n", b"x" * (probe.FRAME_LIMIT + 1) + b"\n"):
                client = probe.StdioClient()
                client.sequence = 0
                reader = asyncio.StreamReader(limit=probe.FRAME_LIMIT)
                reader.feed_data(raw)
                reader.feed_eof()
                client.process = type("Process", (), {"stdout": reader})()

                async def send(_message):
                    pass

                client.send = send
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    await client.call("ping", {})
            reader = asyncio.StreamReader()
            client.process.stdout = reader
            with patch.object(probe, "IO_TIMEOUT", 0.001), self.assertRaises(asyncio.TimeoutError):
                await client.call("ping", {})
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
