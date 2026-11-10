"""Offline safeguards for the one-shot M03 product-path recipe."""

from contextlib import redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

import box_live_probe as probe
from test_images import box_response
from test_server import FakeResponse


def response():
    value = box_response()
    value["model"] = probe.MODEL
    value["answers"]["visible"] = value["answers"].pop("ready")
    value["answers"]["visible"]["noul"] = 0.1
    value["answers"]["target"]["box"] = dict(probe.fixture.RECT)
    return value


def opened(value=None):
    result = FakeResponse(response() if value is None else value)
    result.status = 200
    return result


class BoxProbeTests(unittest.TestCase):
    def test_offline_and_missing_auth_never_open_or_reserve(self):
        with patch.object(probe, "build_opener") as build, patch.object(probe.fixture, "reserve_output") as reserve:
            self.assertEqual(probe.main([]), 0)
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
                self.assertEqual(probe.run("unused", live=True), 2)
            build.assert_not_called()
            reserve.assert_not_called()

    def test_fixed_transport_no_proxy_redirect_and_product_settings(self):
        def write_report(stream, report):
            stream.seek(0)
            json.dump(report, stream, allow_nan=False)
            stream.truncate()

        with io.StringIO() as stream, patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"), \
                patch.object(probe.fixture, "reserve_output", return_value=stream) as reserve, \
                patch.object(probe.fixture, "write_report", side_effect=write_report) as write, \
                patch.object(probe, "build_opener") as build:
            build.return_value.open.return_value = opened()
            self.assertEqual(probe.run("unused", live=True), 0)
            reserve.assert_called_once_with("unused")
            self.assertEqual(write.call_count, 2)
            proxy, redirect = build.call_args.args
            self.assertIsInstance(proxy, ProxyHandler)
            self.assertEqual(proxy.proxies, {})
            with self.assertRaises(HTTPError):
                redirect.redirect_request(Request(probe.ENDPOINT), None, 302, "", {}, "https://example.test")
            sent = build.return_value.open.call_args.args[0]
            self.assertEqual(sent.full_url, probe.ENDPOINT)
            self.assertEqual(sent.get_header("Typesafe-danger-zone-images"), "true")
            self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 60})
            self.assertEqual(build.return_value.open.call_count, 1)
            self.assertIn(b'"model":"jev-1.14.0"', sent.data)

    def test_fixed_transport_without_safe_output_capability(self):
        with patch.object(probe.fixture, "supports_safe_output", return_value=False):
            self.test_fixed_transport_no_proxy_redirect_and_product_settings()

    @unittest.skipUnless(probe.fixture.supports_safe_output(), "safe POSIX output required")
    def test_reservation_fsync_before_send_whitelist_hashes_and_raw_presence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TYPESAFE_API_KEY="synthetic-private"):
            directory = Path(directory).resolve()
            output = directory / "report"
            network = Mock()
            fsync = os.fsync
            syncs = []

            def sync(fd):
                syncs.append(fd)
                return fsync(fd)

            def send(sent, timeout):
                self.assertGreaterEqual(len(syncs), 2)
                marker = json.loads(output.read_text())
                self.assertEqual(marker["status"], "attempt_reserved_outcome_unknown")
                value = response()
                value["extra"] = "synthetic-private"
                value["usage"]["extra"] = "synthetic-private"
                value["answers"]["visible"]["extra"] = "synthetic-private"
                return opened(value)

            network.open.side_effect = send
            with patch.object(os, "fsync", side_effect=sync):
                self.assertEqual(probe.run(output, live=True, opener=network), 0)
            report = json.loads(output.read_text())
            self.assertEqual(report["answers"]["visible"]["noul"], 0.1)
            self.assertNotIn("presence_pass", report)
            self.assertNotIn("synthetic-private", output.read_text())
            self.assertEqual(report["source"]["product_sha256"], {name: probe.digest(probe.ROOT / name) for name in probe.PRODUCT_PATHS})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                probe.run(output, live=True, opener=network)
            self.assertEqual(network.open.call_count, 1)
            link = Path(directory) / "link"
            link.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                probe.run(link / "other", live=True, opener=network)
            self.assertEqual(network.open.call_count, 1)

    @unittest.skipUnless(probe.fixture.supports_safe_output(), "safe POSIX output required")
    def test_failures_interruption_and_persistence_do_not_retry(self):
        invalid = response()
        invalid["answers"]["target"]["box"]["left"] = float("nan")
        wrong_model = response()
        wrong_model["model"] = "wrong-private"
        wrong_geometry = response()
        wrong_geometry["answers"]["target"]["box"]["left"] = 0
        scenarios = [HTTPError(probe.ENDPOINT, 429, "private", {}, io.BytesIO(b"private")),
                     URLError("private"), opened(invalid), opened(wrong_model), opened(wrong_geometry),
                     io.BytesIO(b"not json"), io.BytesIO(b"x" * (probe.MAX_RESPONSE_BYTES + 1))]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"):
            directory = Path(directory).resolve()
            for index, scenario in enumerate(scenarios):
                network = Mock()
                if isinstance(scenario, Exception):
                    network.open.side_effect = scenario
                else:
                    scenario.status = 200
                    network.open.return_value = scenario
                output = Path(directory) / str(index)
                self.assertEqual(probe.run(output, live=True, opener=network), 1)
                self.assertEqual(network.open.call_count, 1)
                self.assertNotIn("private", output.read_text())
            output = Path(directory) / "interrupted"
            network = Mock()
            network.open.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                probe.run(output, live=True, opener=network)
            self.assertEqual(json.loads(output.read_text())["status"], "attempt_reserved_outcome_unknown")
            with self.assertRaises(FileExistsError):
                probe.run(output, live=True, opener=network)
            self.assertEqual(network.open.call_count, 1)
            output = Path(directory) / "fsync"
            network = Mock()
            network.open.return_value = opened()
            with patch.object(probe, "run", partial(probe.run, opener=network)), \
                    patch.object(os, "fsync", side_effect=[None, None, OSError("private")]), \
                    redirect_stdout(io.StringIO()) as text:
                self.assertEqual(probe.main(["--live", "--output", str(output)]), 2)
            self.assertIn("unknown", text.getvalue())
            self.assertIn("Do not retry", text.getvalue())
            self.assertNotIn("private", text.getvalue())
            self.assertTrue(output.exists())
            self.assertEqual(network.open.call_count, 1)

    def test_missing_safe_output_and_bad_fixture_never_send(self):
        network = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"):
            with self.assertRaises(OSError):
                probe.run(Path(directory).resolve() / "missing-parent" / "report", live=True, opener=network)
            network.open.assert_not_called()
        with patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"), \
                patch.object(probe.fixture, "supports_safe_output", return_value=False):
            with self.assertRaises(OSError):
                probe.run("unused", live=True, opener=network)
        with patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"), \
                patch.object(probe.fixture, "verify_fixture", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError):
                probe.run("unused", live=True, opener=network)
        network.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
