"""Offline safeguards for the isolated M01 recipe."""

import copy
from contextlib import ExitStack, redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import image_feasibility_probe as probe


def response():
    return {"model": probe.MODEL, "answers": {
        "visible": {"type": "noul", "noul": 0.99},
        "target": {"type": "box", "box": dict(probe.RECT), "confidence": 0.95}},
        "usage": {"input_tokens": 100, "output_tokens": 20}}


class ImageFeasibilityTests(unittest.TestCase):
    def test_fixture_and_multipart(self):
        png = probe.synthetic_png()
        probe.verify_fixture(png)
        body, boundary = probe.multipart(png)
        self.assertIn(png, body)
        self.assertIn(b'name="request"', body.split(boundary.encode())[1])
        self.assertIn(b'"type": "box"', body)
        self.assertIn(b'"type": "noul"', body)
        with self.assertRaises(ValueError):
            probe.verify_fixture(png[:-20])

    def test_opt_in_missing_auth_and_cli(self):
        with patch.object(probe, "send") as send:
            self.assertEqual(probe.main([]), 0)
            send.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(probe.run("unused", live=True), 2)

    def test_strict_response_validation(self):
        self.assertTrue(all(probe.selected_response(response())["checks"].values()))
        mutations = [
            lambda r: r.update(model="wrong"),
            lambda r: r["answers"].pop("visible"),
            lambda r: r["answers"]["visible"].update(noul=True),
            lambda r: r["answers"]["target"].update(confidence=float("nan")),
            lambda r: r["answers"]["target"]["box"].update(left=-0.1),
            lambda r: r["answers"]["target"]["box"].update(right=0.1),
            lambda r: r["answers"]["target"]["box"].update(top=float("inf")),
            lambda r: r["usage"].update(input_tokens=True),
        ]
        for mutate in mutations:
            value = copy.deepcopy(response())
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.selected_response(value)

    def test_formally_valid_wrong_geometry_and_absence_fail(self):
        value = response()
        value["answers"]["target"]["box"] = {"left": 0, "top": 0, "right": 1, "bottom": 1}
        self.assertFalse(probe.selected_response(value)["checks"]["geometry_pass"])
        value = response()
        value["answers"]["visible"]["noul"] = 0.1
        self.assertFalse(probe.selected_response(value)["checks"]["presence_pass"])

    def test_missing_posix_capabilities_stop_before_transport(self):
        for missing in ("posix", "dir_fd", "O_DIRECTORY", "O_NOFOLLOW"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "report.json"
                transport = unittest.mock.Mock()
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, TYPESAFE_API_KEY="synthetic"))
                    if missing == "posix":
                        with patch.object(probe.os, "name", "nt"):
                            self.assertFalse(probe.supports_safe_output())
                        stack.enter_context(patch.object(probe, "supports_safe_output", return_value=False))
                    elif missing == "dir_fd":
                        stack.enter_context(patch.object(probe.os, "supports_dir_fd", set()))
                    else:
                        stack.enter_context(patch.dict(probe.os.__dict__))
                        probe.os.__dict__.pop(missing, None)
                    stack.enter_context(patch.object(probe, "run", partial(probe.run, transport=transport)))
                    with redirect_stdout(io.StringIO()) as message:
                        self.assertEqual(probe.main(["--live", "--output", str(output)]), 2)
                    self.assertIn("Do not retry", message.getvalue())
                    transport.assert_not_called()
                self.assertFalse(output.exists())

    @unittest.skipUnless(probe.supports_safe_output(), "safe POSIX output required")
    def test_post_send_fsync_failure_keeps_marker_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, TYPESAFE_API_KEY="synthetic-private"):
            output = Path(directory) / "report.json"
            transport = unittest.mock.Mock(return_value=(200, json.dumps(response()).encode()))
            with patch.object(probe, "run", partial(probe.run, transport=transport)), \
                    patch.object(probe.os, "fsync", side_effect=[None, None, OSError("synthetic-private")]), \
                    redirect_stdout(io.StringIO()) as message:
                self.assertEqual(probe.main(["--live", "--output", str(output)]), 2)
            self.assertEqual(transport.call_count, 1)
            self.assertTrue(output.exists())
            self.assertNotIn("not sent", message.getvalue().lower())
            self.assertNotIn("synthetic-private", message.getvalue() + output.read_text())
            self.assertIn("unknown", message.getvalue())
            self.assertIn("Do not retry", message.getvalue())
            with patch.object(probe, "run", partial(probe.run, transport=transport)), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(["--live", "--output", str(output)]), 2)
            self.assertEqual(transport.call_count, 1)

    @unittest.skipUnless(probe.supports_safe_output(), "safe POSIX output required")
    def test_exclusive_attempt_and_safe_report(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TYPESAFE_API_KEY="fake-private-key"):
            output = Path(directory) / "report.json"

            def transport(body, boundary, key):
                calls.append(1)
                self.assertEqual(json.loads(output.read_text())["status"], "attempt_reserved_outcome_unknown")
                value = response()
                value["secret"] = key
                return 200, json.dumps(value).encode()

            self.assertEqual(probe.run(output, live=True, transport=transport), 0)
            self.assertNotIn("fake-private-key", output.read_text())
            with self.assertRaises(FileExistsError):
                probe.run(output, live=True, transport=transport)
            self.assertEqual(len(calls), 1)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            link = Path(directory) / "link"
            link.symlink_to(output)
            with self.assertRaises(OSError):
                probe.run(link, live=True, transport=transport)
            parent = Path(directory) / "parent"
            parent.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                probe.run(parent / "other", live=True, transport=transport)

    @unittest.skipUnless(probe.supports_safe_output(), "safe POSIX output required")
    def test_failure_and_interruption_never_retry(self):
        scenarios = [(302, b"private"), (400, b"private"), (429, b"private"),
                     (500, b"private"), (200, b"not json"),
                     (200, b"x" * (probe.MAX_RESPONSE_BYTES + 1))]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TYPESAFE_API_KEY="fake-private-key"):
            for index, result in enumerate(scenarios):
                output = Path(directory) / str(index)
                with patch.object(probe, "send", return_value=result) as transport:
                    self.assertEqual(probe.run(output, live=True, transport=transport), 1)
                    self.assertEqual(transport.call_count, 1)
                    self.assertNotIn("private", output.read_text())
            output = Path(directory) / "exception"
            def fail(*args):
                raise RuntimeError("fake-private-key")
            self.assertEqual(probe.run(output, live=True, transport=fail), 1)
            self.assertNotIn("fake-private-key", output.read_text())
            output = Path(directory) / "interrupt"
            def interrupt(*args):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                probe.run(output, live=True, transport=interrupt)
            self.assertEqual(json.loads(output.read_text())["status"], "attempt_reserved_outcome_unknown")
            with self.assertRaises(FileExistsError):
                probe.run(output, live=True, transport=interrupt)

    def test_transport_fixed_endpoint_header_and_redirect_block(self):
        with patch.object(probe, "build_opener") as build:
            opened = build.return_value.open
            opened.side_effect = HTTPError(probe.ENDPOINT, 302, "private", {}, None)
            self.assertEqual(probe.send(b"body", "boundary", "fake"), (302, b""))
            self.assertEqual(opened.call_count, 1)
            req = opened.call_args.args[0]
            self.assertEqual(req.full_url, probe.ENDPOINT)
            self.assertEqual(req.get_header("Typesafe-danger-zone-images"), "true")
            self.assertEqual(opened.call_args.kwargs["timeout"], probe.TIMEOUT_SECONDS)
            self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org"))


if __name__ == "__main__":
    unittest.main()
