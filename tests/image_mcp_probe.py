"""One-shot synthetic STDIO-MCP experiment; offline unless explicitly --live.

An existing output, even empty, blocks reuse. Never retry at another path.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arbitype import core  # noqa: E402
import image_feasibility_probe as fixture  # noqa: E402
from test_images import jpeg  # noqa: E402

MODEL = fixture.MODEL
PROTOCOL = "2025-06-18"
IO_TIMEOUT = 75
CLEANUP_TIMEOUT = 5
FRAME_LIMIT = 2 * 1024 * 1024
SOURCE_PATHS = ("server.py", "arbitype/core.py", "arbitype/mcp.py", "arbitype/images.py",
                "tests/image_feasibility_probe.py", "tests/test_images.py",
                "tests/image_mcp_probe.py", "tests/test_image_mcp_probe.py")


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def cases():
    png = fixture.synthetic_png()
    fixture.verify_fixture(png)
    # The existing JPEG generator has a single luminance component, zero DC
    # deltas from initial predictor 0 and EOB-only AC blocks. Inverse DCT is
    # therefore 0 everywhere; the JPEG level shift yields gray 128. This is
    # a construction proof for this fixed fixture, not a general JPEG decoder.
    result = []
    for fmt, data, color in (("png", png, "red"), ("jpeg", jpeg(), "gray")):
        questions = {
            "visible": {"type": "noul", "instructions": (
                "Is a red rectangle visible in `photo`?" if fmt == "png" else
                "Is `photo` a uniform medium-gray image?")},
            "color": {"type": "choice", "instructions": (
                "What color is the central rectangle in `photo`?" if fmt == "png" else
                "What is the uniform color of `photo`?"),
                "criteria": {"red": "red", "gray": "medium gray", "white": "white"}},
            "brightness": {"type": "score", "instructions": (
                "Rate the brightness of the center of `photo`."),
                "criteria": ["black or very dark", "medium brightness", "white or very bright"]},
        }
        if fmt == "png":
            questions["target"] = {"type": "box", "instructions": "The red rectangle in `photo`."}
        payload = {"model": MODEL, "state": {"photo": "synthetic-photo"}, "allow_images": True,
                   "assets": {"synthetic-photo": {"format": fmt,
                              "data_base64": base64.b64encode(data).decode("ascii")}},
                   "questions": questions}
        result.append((payload, {"format": fmt, "sha256": hashlib.sha256(data).hexdigest(),
                                "width": 512 if fmt == "png" else 280,
                                "height": 512 if fmt == "png" else 280,
                                "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                                "expected_color": color, "expected_presence": True,
                                "score_policy": "raw_only_no_threshold",
                                "pixel_verified": fmt == "png"}))
    return result


def child_environment(key):
    # No inherited proxy, Python injection, endpoint, TLS or retry overrides.
    env = {"PATH": os.defpath, "NO_PROXY": "*", "TYPESAFE_API_KEY": key,
            "TYPESAFE_BASE_URL": "https://api.typesafe.ai", "TYPESAFE_MODEL": MODEL,
            "TYPESAFE_TIMEOUT_SECONDS": "60", "TYPESAFE_MAX_RETRIES": "0",
            "TYPESAFE_MAX_RESPONSE_BYTES": str(fixture.MAX_RESPONSE_BYTES)}
    if sys.platform == "win32" and "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    return env


class StdioClient:
    """Bounded one-request-at-a-time client; stderr is discarded, never logged."""

    async def __aenter__(self):
        self.sequence = 0
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", str(ROOT / "server.py"), cwd=ROOT,
            env=child_environment(os.environ["TYPESAFE_API_KEY"]),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=FRAME_LIMIT)
        return self

    async def send(self, message):
        raw = json.dumps(message, allow_nan=False).encode() + b"\n"
        if len(raw) > FRAME_LIMIT:
            raise ValueError("frame")
        self.process.stdin.write(raw)
        await asyncio.wait_for(self.process.stdin.drain(), IO_TIMEOUT)

    async def call(self, method, params):
        self.sequence += 1
        await self.send({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        raw = await asyncio.wait_for(self.process.stdout.readline(), IO_TIMEOUT)
        if not raw.endswith(b"\n") or len(raw) > FRAME_LIMIT:
            raise ValueError("frame")
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
                or type(value.get("id")) is not int or value["id"] != self.sequence
                or "error" in value or not isinstance(value.get("result"), dict)):
            raise ValueError("protocol")
        return value["result"]

    async def __aexit__(self, *_args):
        # EOF is the normal server shutdown path; terminate/kill only this child.
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), CLEANUP_TIMEOUT)
        except asyncio.TimeoutError:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), CLEANUP_TIMEOUT)
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.wait_for(self.process.wait(), CLEANUP_TIMEOUT)
        if self.process.returncode != 0:
            raise ValueError("child exit")


def select_result(value, payload, expected):
    if value.get("isError", False) is not False:
        raise ValueError("tool error")
    response = core.validate_api_response(value.get("structuredContent"), payload["questions"])
    if response["model"] != MODEL:
        raise ValueError("model")
    answers = {}
    for name, question in payload["questions"].items():
        answer = response["answers"][name]
        fields = {"noul": ("noul",), "choice": ("choice", "probabilities", "confidence"),
                  "score": ("score", "probabilities", "confidence"),
                  "box": ("box", "confidence")}[question["type"]]
        answers[name] = {"type": question["type"], **{field: answer[field] for field in fields}}
    checks = {"color_matches": answers["color"]["choice"] == expected["expected_color"]}
    if "target" in answers:
        checks["geometry_matches"] = all(abs(answers["target"]["box"][k] - edge)
                                          <= fixture.EDGE_TOLERANCE for k, edge in fixture.RECT.items())
    return {"model": MODEL, "answers": answers,
            "usage": {k: response["usage"][k] for k in ("input_tokens", "output_tokens")},
            "fixture_checks": checks}


async def exchange(report, stream, prepared, client_factory):
    async with client_factory() as client:
        initialized = await client.call("initialize", {"protocolVersion": PROTOCOL,
                                        "capabilities": {}, "clientInfo": {"name": "synthetic-probe", "version": "1"}})
        if initialized.get("protocolVersion") != PROTOCOL or "tools" not in initialized.get("capabilities", {}):
            raise ValueError("initialize")
        await client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        catalog = await client.call("tools/list", {})
        tools = [tool for tool in catalog["tools"] if tool["name"] == "evaluate"]
        if len(tools) != 1 or not {"assets", "allow_images"} <= tools[0]["inputSchema"]["properties"].keys():
            raise ValueError("image schema")
        report["mcp"] = {"protocol": PROTOCOL, "initialized": True, "evaluate_image_schema": True}
        for payload, expected in prepared:
            report["calls_started"] += 1
            fixture.write_report(stream, report)
            value = await client.call("tools/call", {"name": "evaluate", "arguments": payload})
            selected = select_result(value, payload, expected)
            report["cases"].append({"fixture": expected, **selected})
            fixture.write_report(stream, report)
            if not all(selected["fixture_checks"].values()):
                report["status"] = "fixture_mismatch"
                return
        report["status"] = "pass"


def run(output, *, live=False, client_factory=StdioClient):
    if not live:
        return 0
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        return 2
    prepared = cases()
    report = {"status": "attempt_reserved_outcome_unknown", "calls_started": 0, "cases": [],
              "source_sha256": hashes(), "endpoint": fixture.ENDPOINT, "model": MODEL,
              "expectations": [expected for _, expected in prepared],
              "box_rule": {"expected": fixture.RECT, "edge_tolerance": fixture.EDGE_TOLERANCE},
              "presence_policy": "raw_only_no_threshold"}
    with fixture.reserve_output(output) as stream:
        fixture.write_report(stream, report)
        try:
            asyncio.run(exchange(report, stream, prepared, client_factory))
        except Exception:
            report["status"] = "transport_protocol_validation_or_persistence_error"
        fixture.write_report(stream, report)
    return 0 if report["status"] == "pass" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.live and args.output is None:
        parser.error("--live requires --output")
    try:
        return run(args.output, live=args.live)
    except (OSError, ValueError):
        print("Incomplete; retained marker may mean unknown outcome. Do not retry at any path.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
