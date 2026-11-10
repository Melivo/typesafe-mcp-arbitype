"""One-shot synthetic image experiment, not a production image API.

No network without --live. The exclusive output file is also the durable
attempt marker: inspect it after interruption; never delete it to retry.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import zlib
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"
SIDE = 512
RECT = {"left": 0.25, "top": 0.25, "right": 0.75, "bottom": 0.75}
EDGE_TOLERANCE = 0.04
MIN_PRESENCE = 0.9
TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 1024 * 1024
SOURCES = ["https://docs.typesafe.ai/images", "https://docs.typesafe.ai/primitives/box.md"]


def synthetic_png():
    """RGB white square, red pixels at x/y in [128, 384)."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    rows = []
    for y in range(SIDE):
        row = b"".join(b"\xff\x00\x00" if 128 <= x < 384 and 128 <= y < 384
                       else b"\xff\xff\xff" for x in range(SIDE))
        rows.append(b"\x00" + row)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", SIDE, SIDE, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def verify_fixture(png):
    """Independently decode and check every generated pixel before sending."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("fixture")
    offset, compressed = 8, b""
    while offset < len(png):
        if offset + 12 > len(png):
            raise ValueError("fixture truncation")
        size = struct.unpack(">I", png[offset:offset + 4])[0]
        if offset + 12 + size > len(png):
            raise ValueError("fixture truncation")
        kind = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", png[offset + 8 + size:offset + 12 + size])[0]
        if zlib.crc32(kind + data) != crc:
            raise ValueError("fixture CRC")
        if kind == b"IHDR" and data != struct.pack(">IIBBBBB", 512, 512, 8, 2, 0, 0, 0):
            raise ValueError("fixture dimensions")
        if kind == b"IDAT":
            compressed += data
        offset += 12 + size
    raw = zlib.decompress(compressed)
    if len(raw) != 512 * 1537:
        raise ValueError("fixture size")
    for y in range(512):
        row = raw[y * 1537:(y + 1) * 1537]
        if row[0] != 0:
            raise ValueError("fixture filter")
        for x in range(512):
            expected = bytes((255, 0, 0)) if 128 <= x < 384 and 128 <= y < 384 else bytes((255, 255, 255))
            if row[1 + x * 3:4 + x * 3] != expected:
                raise ValueError("fixture pixels")


def multipart(png):
    reference = "962276f8-2657-4adc-a668-70bad5f37194"
    payload = {
        "model": MODEL,
        "state": {"photo": reference},
        "assets": {reference: {"type": "image", "part": "fixture"}},
        "questions": {
            "visible": {"type": "noul", "instructions": "Is there a red rectangle in `photo`?"},
            "target": {"type": "box", "instructions": "The red rectangle in `photo`."},
        },
    }
    boundary = "arbitype-image-probe-962276f8"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\n'
            'Content-Type: application/json\r\n\r\n').encode() + json.dumps(payload).encode()
    body += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="fixture"; '
             'filename="synthetic.png"\r\nContent-Type: image/png\r\n\r\n').encode()
    return body + png + f"\r\n--{boundary}--\r\n".encode(), boundary


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(body, boundary, key):
    """Exactly one POST; no proxy, redirect, retry or raw error persistence."""
    request = Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "TypeSafe-Danger-Zone-Images": "true",
        "Accept": "application/json",
    })
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        return status, b""


def probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def selected_response(value):
    """Whitelist typed numbers only; provider strings never enter the report."""
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("model")
    answers, usage = value.get("answers"), value.get("usage")
    if not isinstance(answers, dict) or not isinstance(usage, dict):
        raise ValueError("response objects")
    visible, target = answers.get("visible"), answers.get("target")
    if not isinstance(visible, dict) or visible.get("type") != "noul" or not probability(visible.get("noul")):
        raise ValueError("presence")
    if not isinstance(target, dict) or target.get("type") != "box" or not probability(target.get("confidence")):
        raise ValueError("box answer")
    box = target.get("box")
    if not isinstance(box, dict) or set(box) != set(RECT) or not all(probability(v) for v in box.values()):
        raise ValueError("box edges")
    if not box["left"] < box["right"] or not box["top"] < box["bottom"]:
        raise ValueError("box order")
    for name in ("input_tokens", "output_tokens"):
        if type(usage.get(name)) is not int or usage[name] < 0:
            raise ValueError("usage")
    checks = {
        "presence_pass": visible["noul"] >= MIN_PRESENCE,
        "geometry_pass": all(abs(box[k] - RECT[k]) <= EDGE_TOLERANCE for k in RECT),
    }
    return {
        "model": MODEL,
        "usage": {k: usage[k] for k in ("input_tokens", "output_tokens")},
        "answers": {"visible": {"type": "noul", "noul": visible["noul"]},
                    "target": {"type": "box", "box": box, "confidence": target["confidence"]}},
        "checks": checks,
    }


def supports_safe_output():
    """This recipe requires POSIX descriptor-relative no-follow creation."""
    return (os.name == "posix"
            and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
            and os.open in os.supports_dir_fd)


def reserve_output(path):
    """Reject symlink components; O_EXCL marker survives abort and blocks reuse."""
    if not supports_safe_output():
        raise OSError("safe POSIX output unavailable")
    path = Path(os.path.abspath(path))
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        try:
            os.fsync(parent_fd)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "w")
    finally:
        os.close(parent_fd)


def write_report(stream, report):
    stream.seek(0)
    json.dump(report, stream, indent=2, allow_nan=False)
    stream.write("\n")
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def run(output, *, live=False, transport=send):
    if not live:
        return 0
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return 2
    png = synthetic_png()
    verify_fixture(png)
    body, boundary = multipart(png)
    report = {
        "status": "attempt_reserved_outcome_unknown",
        "utc": datetime.now(timezone.utc).isoformat(),
        "http_status": None,
        "request": {"endpoint": ENDPOINT, "model": MODEL, "sha256": hashlib.sha256(body).hexdigest()},
        "fixture": {"sha256": hashlib.sha256(png).hexdigest(), "width": SIDE, "height": SIDE,
                    "expected_box": RECT, "pixel_verified": True},
        "source": {"probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "docs": SOURCES, "retrieved": "2026-11-10"},
        "rule": {"min_presence": MIN_PRESENCE, "max_edge_error": EDGE_TOLERANCE,
                 "scope": "this synthetic fixture only; not general calibration"},
    }
    with reserve_output(output) as stream:
        write_report(stream, report)
        try:
            status, raw = transport(body, boundary, key)
            report["http_status"] = status
            report["status"] = "http_error"
            if status == 200:
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("response limit")
                selected = selected_response(json.loads(raw))
                report.update(selected)
                report["status"] = "pass" if all(selected["checks"].values()) else "fixture_mismatch"
        except Exception:
            # Never serialize exception messages, response bodies or headers.
            report["status"] = "transport_or_validation_error"
        write_report(stream, report)
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
        print("Probe incomplete: outcome may be unknown; inspect existing state. "
              "Safe POSIX output is required. Do not retry, including at a new path.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
