import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import arbitype  # noqa: E402
from arbitype import core, mcp  # noqa: E402


def route_response(choice="inspect"):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": choice,
                "probabilities": {"inspect": 0.9, "test": 0.1},
                "confidence": 0.9,
            }
        },
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


class HostContractTests(unittest.TestCase):
    def test_image_fields_are_only_advertised_on_evaluate(self):
        for tool in mcp.TOOLS:
            properties = tool["inputSchema"]["properties"]
            self.assertEqual("assets" in properties, tool["name"] == "evaluate")
            self.assertEqual("allow_images" in properties, tool["name"] == "evaluate")
        schema = mcp.TOOL_MAP["evaluate"]["inputSchema"]
        self.assertEqual(schema["required"], ["state", "questions"])
        self.assertEqual(schema["properties"]["assets"]["maxProperties"], 4)

    def test_server_instructions_fit_host_prefix_limit(self):
        self.assertLessEqual(len(mcp.SERVER_INSTRUCTIONS), 512)
        self.assertIn("Arbitype", mcp.SERVER_INSTRUCTIONS)
        self.assertIn("route", mcp.SERVER_INSTRUCTIONS)
        self.assertIn("review", mcp.SERVER_INSTRUCTIONS)
        self.assertIn("never edit files", mcp.SERVER_INSTRUCTIONS)

    def test_tools_are_read_only_and_have_structured_output_schema(self):
        self.assertEqual(set(mcp.TOOL_MAP), {tool["name"] for tool in mcp.TOOLS})
        for tool in mcp.TOOLS:
            self.assertTrue(tool["annotations"]["readOnlyHint"], tool["name"])
            self.assertFalse(tool["annotations"]["destructiveHint"], tool["name"])
            self.assertTrue(tool["annotations"]["idempotentHint"], tool["name"])
            self.assertEqual(tool["outputSchema"]["type"], "object")
            self.assertIn("required", tool["outputSchema"])
            self.assertEqual(tool["inputSchema"]["additionalProperties"], False)

    def test_tool_descriptions_explain_selection_boundaries(self):
        descriptions = {tool["name"]: tool["description"] for tool in mcp.TOOLS}
        for name, description in descriptions.items():
            self.assertIn("USE WHEN", description, name)
            self.assertIn("DO NOT USE WHEN", description, name)
        self.assertIn("multiple named claims", descriptions["verify"])
        self.assertIn("threshold", descriptions["gate"])
        self.assertIn("next action", descriptions["route"])
        self.assertIn("whole-object", descriptions["review"])

    def test_canonical_and_legacy_packages_share_exports(self):
        import typesafe_mcp
        import typesafe_codex_mcp
        from arbitype import TypeSafeClient
        from typesafe_mcp import core as typesafe_core
        from typesafe_codex_mcp import core as legacy_core
        from typesafe_codex_mcp import mcp as legacy_mcp

        self.assertEqual(arbitype.__version__, core.SERVER_VERSION)
        self.assertEqual(typesafe_mcp.__version__, arbitype.__version__)
        self.assertEqual(typesafe_codex_mcp.__version__, arbitype.__version__)
        self.assertIs(typesafe_core.Settings, core.Settings)
        self.assertIs(legacy_core.Settings, core.Settings)
        self.assertIs(legacy_mcp.handle_message, mcp.handle_message)
        self.assertIs(typesafe_mcp.TypeSafeClient, TypeSafeClient)
        self.assertIs(typesafe_codex_mcp.TOOLS, mcp.TOOLS)

    def test_server_name_is_arbitype(self):
        self.assertEqual(mcp.SERVER_NAME, "arbitype")
        self.assertEqual(core.SERVER_NAME, "arbitype")

    def test_registry_name_and_pypi_metadata_use_arbitype(self):
        metadata = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], "io.github.Renwang-Huang/arbitype")
        self.assertEqual(metadata["packages"][0]["identifier"], "arbitype")
        self.assertIn('name = "arbitype"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_initialize_advertises_tools_only(self):
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(response["result"]["capabilities"], {"tools": {"listChanged": False}})

    def test_server_discovery_advertises_supported_protocol_contract(self):
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "discover-1",
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
        )
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], list(mcp.SUPPORTED_PROTOCOL_VERSIONS))
        self.assertEqual(result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "arbitype")

    def test_modern_metadata_requests_receive_complete_results(self):
        metadata = {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.MODERN_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        }
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "id": "modern-1", "method": "tools/list", "params": metadata}
        )
        self.assertEqual(response["result"]["resultType"], "complete")
        self.assertEqual(response["result"]["ttlMs"], 300_000)
        self.assertEqual(response["result"]["cacheScope"], "public")
        self.assertIn("route", {tool["name"] for tool in response["result"]["tools"]})

    def test_modern_requests_require_client_capabilities(self):
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "modern-missing-capabilities",
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": mcp.MODERN_PROTOCOL_VERSION
                    }
                },
            }
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("clientCapabilities", response["error"]["message"])

    def test_unsupported_modern_version_returns_negotiation_error(self):
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "version-1",
                "method": "ping",
                "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}},
            }
        )
        self.assertEqual(response["error"]["code"], -32022)
        self.assertIn(mcp.MODERN_PROTOCOL_VERSION, response["error"]["data"]["supported"])

    def test_initialize_preserves_requested_supported_legacy_version(self):
        response = mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")

    def test_invalid_request_ids_are_rejected(self):
        for invalid_id in (None, 1.25, True):
            with self.subTest(invalid_id=invalid_id):
                response = mcp.handle_message(
                    {"jsonrpc": "2.0", "id": invalid_id, "method": "ping", "params": {}}
                )
                self.assertEqual(response["error"]["code"], -32600)

    def test_explicit_null_params_are_invalid(self):
        response = mcp.handle_message(
            {"jsonrpc": "2.0", "id": "null-params", "method": "ping", "params": None}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_tool_schema_is_enforced_before_provider_call(self):
        invalid_calls = (
            ("verify", {"state": "evidence", "claims": {"claim": True}}),
            ("gate", {"state": "patch", "checks": {"tests": True}}),
            ("check", {"state": "evidence", "instructions": "ready?", "true_criteria": False}),
            ("score", {"state": "release", "instructions": "Score it", "levels": [None, "high"]}),
            (
                "evaluate",
                {
                    "state": "evidence",
                    "questions": {"ready": {"type": "noul", "instructions": True}},
                },
            ),
        )
        with patch.object(mcp, "TypeSafeClient", side_effect=AssertionError("provider was called")):
            for name, arguments in invalid_calls:
                with self.subTest(name=name), self.assertRaisesRegex(
                    core.BridgeError, "advertised input schema"
                ):
                    mcp.call_tool(name, arguments)

    def test_health_is_local_and_does_not_require_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = mcp.call_tool("health", {})
        self.assertEqual(result["type"], "health")
        self.assertEqual(result["status"], "missing_api_key")
        self.assertFalse(result["live"])

    def test_health_rejects_implicit_live_requests(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret"}, clear=True):
            with self.assertRaisesRegex(core.BridgeError, "advertised input schema"):
                mcp.call_tool("health", {"live": "yes"})

    def test_route_creates_a_single_choice_question(self):
        class FakeClient:
            settings = core.Settings(api_key="secret")

            def __init__(self):
                self.request = None

            def evaluate(self, request):
                self.request = request
                return route_response()

        fake = FakeClient()
        with patch.object(mcp, "TypeSafeClient", return_value=fake):
            result = mcp.call_tool(
                "route",
                {
                    "state": "The patch changes authentication and has no test output.",
                    "actions": {"inspect": "Read the diff", "test": "Run focused tests"},
                },
            )
        self.assertEqual(result["type"], "route")
        self.assertEqual(result["route"], "inspect")
        self.assertEqual(fake.request["questions"]["next_action"]["type"], "choice")

    def test_review_is_a_named_gate_with_fail_closed_result(self):
        class FakeClient:
            settings = core.Settings(api_key="secret")

            def evaluate(self, request):
                return {
                    "model": "jev-1.13.0",
                    "answers": {
                        "tests": {"type": "noul", "noul": 0.55},
                        "scope": {"type": "noul", "noul": 0.95},
                    },
                }

        with patch.object(mcp, "TypeSafeClient", return_value=FakeClient()):
            result = mcp.call_tool(
                "review",
                {"state": "diff", "checks": {"tests": "Tests cover the change", "scope": "Scope is bounded"}},
            )
        self.assertEqual(result["type"], "review")
        self.assertEqual(result["decision"], "fail")
        self.assertEqual(result["checks"]["tests"]["decision"], "fail")

    def test_legacy_tool_aliases_are_callable_but_not_advertised(self):
        self.assertEqual(mcp.TOOL_ALIASES, {"codex_route": "route", "codex_review": "review"})
        self.assertNotIn("codex_route", {tool["name"] for tool in mcp.TOOLS})
        self.assertNotIn("codex_review", {tool["name"] for tool in mcp.TOOLS})

        class FakeClient:
            settings = core.Settings(api_key="secret")

            def evaluate(self, _request):
                return route_response()

        with patch.object(mcp, "TypeSafeClient", return_value=FakeClient()):
            result = mcp.call_tool(
                "codex_route",
                {"state": "state", "actions": {"inspect": "Inspect", "test": "Test"}},
            )
        self.assertEqual(result["type"], "route")

    def test_unexpected_tool_errors_are_safe_mcp_results(self):
        class BrokenClient:
            settings = core.Settings(api_key="secret")

            def evaluate(self, _request):
                raise RuntimeError("secret-key leaked from provider")

        with patch.object(mcp, "TypeSafeClient", return_value=BrokenClient()):
            response = mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "check",
                        "arguments": {"state": "x", "instructions": "Is x ready?"},
                    },
                }
            )
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn("secret-key", json.dumps(response))
        self.assertEqual(response["result"]["content"][0]["text"], "internal tool error")


class CLISurfaceTests(unittest.TestCase):
    def _assert_module_cli(self, module_name):
        child_env = dict(os.environ)
        child_env.pop("TYPESAFE_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "-m", module_name, "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=child_env,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("0.7.0", completed.stdout)

    def test_arbitype_cli(self):
        self._assert_module_cli("arbitype")

    def test_typesafe_mcp_legacy_cli(self):
        self._assert_module_cli("typesafe_mcp")

    def test_typesafe_codex_mcp_legacy_cli(self):
        self._assert_module_cli("typesafe_codex_mcp")


class StdioIntegrationTests(unittest.TestCase):
    def test_stdio_box_transport_success_and_invalid_response(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from test_images import box_request, box_response

        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                value = box_response()
                if len(received) == 2:
                    value["answers"]["target"]["box"]["left"] = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        invalid = box_request()
        invalid["questions"]["target"]["criteria"] = None
        messages = [{"jsonrpc": "2.0", "id": index, "method": "tools/call",
                     "params": {"name": "evaluate", "arguments": value}}
                    for index, value in enumerate((invalid, box_request(), box_request()), 1)]
        messages.extend([{"jsonrpc": "2.0", "id": 4, "method": "ping"},
                         {"jsonrpc": "2.0", "method": "exit"}])
        try:
            completed = subprocess.run(
                [sys.executable, "-B", "server.py"], cwd=ROOT,
                input="\n".join(json.dumps(message) for message in messages) + "\n",
                text=True, capture_output=True, timeout=10, check=False,
                env={**os.environ, "TYPESAFE_API_KEY": "synthetic-not-a-credential",
                     "TYPESAFE_BASE_URL": f"http://127.0.0.1:{server.server_port}"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        results = [json.loads(line)["result"] for line in completed.stdout.splitlines()]
        self.assertTrue(results[0]["isError"])
        self.assertEqual(results[1]["structuredContent"], box_response())
        self.assertTrue(results[2]["isError"])
        self.assertNotIn("structuredContent", results[2])
        self.assertEqual(results[3], {})
        self.assertEqual(len(received), 2)
        self.assertIn(b'"type":"box"', received[0])

    def test_stdio_image_opt_in_failure_is_local_and_stream_survives(self):
        from test_images import request

        arguments = request()
        arguments["allow_images"] = False
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "evaluate", "arguments": arguments}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        completed = subprocess.run(
            [sys.executable, "-B", "server.py"], cwd=ROOT,
            input="\n".join(json.dumps(message) for message in messages) + "\n",
            text=True, capture_output=True, timeout=5, check=False,
            env={**os.environ, "TYPESAFE_API_KEY": "synthetic-not-a-credential"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertIn("allow_images", responses[0]["result"]["content"][0]["text"])
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual(completed.stderr, "")

    def test_stdio_handshake_tool_catalog_and_shutdown(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"jsonrpc": "2.0", "method": "exit"},
            {"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}},
        ]
        child_env = dict(os.environ)
        child_env.pop("TYPESAFE_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "server.py"],
            cwd=ROOT,
            input="\n".join(json.dumps(message) for message in messages) + "\n",
            text=True,
            capture_output=True,
            env=child_env,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], mcp.SERVER_VERSION)
        self.assertIn("review", {tool["name"] for tool in responses[1]["result"]["tools"]})
        self.assertEqual(responses[2]["result"], {})

    def test_stdio_rejects_malformed_messages_without_tracebacks(self):
        child_env = dict(os.environ)
        child_env.pop("TYPESAFE_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "server.py"],
            cwd=ROOT,
            input="{not-json\n[]\n{\"jsonrpc\":\"2.0\",\"method\":\"exit\"}\n",
            text=True,
            capture_output=True,
            env=child_env,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response["error"]["code"] for response in responses], [-32700, -32600])

    def test_stdio_rejects_invalid_utf8_without_tracebacks(self):
        child_env = dict(os.environ)
        child_env.pop("TYPESAFE_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "server.py"],
            cwd=ROOT,
            input=b"\xff\n[]\n{\"jsonrpc\":\"2.0\",\"method\":\"exit\"}\n",
            capture_output=True,
            env=child_env,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stderr, b"")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response["error"]["code"] for response in responses], [-32700, -32600])

    def test_stdio_rejects_deeply_nested_json_without_tracebacks(self):
        child_env = dict(os.environ)
        child_env.pop("TYPESAFE_API_KEY", None)
        nested = b"[" * 2000 + b"]" * 2000
        completed = subprocess.run(
            [sys.executable, "server.py"],
            cwd=ROOT,
            input=nested + b'\n{"jsonrpc":"2.0","method":"exit"}\n',
            capture_output=True,
            env=child_env,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stderr, b"")
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        # CPython versions differ on whether this depth is rejected by the
        # JSON decoder or accepted as a valid non-object JSON value. Both
        # paths must remain bounded and produce a JSON-RPC error, not a
        # traceback or process termination.
        try:
            json.loads(nested.decode("utf-8"))
        except (RecursionError, ValueError):
            expected_code = -32700
        else:
            expected_code = -32600
        self.assertEqual([response["error"]["code"] for response in responses], [expected_code])

    def test_stdio_reads_and_discards_oversized_lines_with_bounded_calls(self):
        class RecordingReader:
            def __init__(self, payload):
                self.stream = io.BytesIO(payload)
                self.readline_sizes = []

            def readline(self, size=-1):
                self.readline_sizes.append(size)
                return self.stream.readline(size)

        payload = (
            b"x" * (mcp.MAX_INPUT_LINE_BYTES + 1024)
            + b"\n[]\n{\"jsonrpc\":\"2.0\",\"method\":\"exit\"}\n"
        )
        reader = RecordingReader(payload)
        stdout = io.StringIO()
        with patch.object(mcp.sys, "stdin", SimpleNamespace(buffer=reader)), patch.object(
            mcp.sys, "stdout", stdout
        ):
            self.assertEqual(mcp.main_stdio(), 0)

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([response["error"]["code"] for response in responses], [-32600, -32600])
        self.assertEqual(reader.readline_sizes[0], mcp.MAX_INPUT_LINE_BYTES + 1)
        self.assertIn(8192, reader.readline_sizes[1:])
        self.assertTrue(all(size <= mcp.MAX_INPUT_LINE_BYTES + 1 for size in reader.readline_sizes))


if __name__ == "__main__":
    unittest.main()
