"""One authorized synthetic M03 product-path probe; offline unless --live.

An existing output is an attempt marker, not permission to retry elsewhere.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arbitype import core  # noqa: E402
import image_feasibility_probe as fixture  # noqa: E402

MODEL = "jev-1.14.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 1024 * 1024
EDGE_TOLERANCE = 0.04
PRODUCT_PATHS = ("arbitype/core.py", "arbitype/mcp.py", "arbitype/images.py")


def request(png):
    reference = "962276f8-2657-4adc-a668-70bad5f37194"
    return {
        "model": MODEL,
        "state": {"photo": reference},
        "allow_images": True,
        "assets": {reference: {"format": "png", "data_base64": base64.b64encode(png).decode("ascii")}},
        "questions": {
            "visible": {"type": "noul", "instructions": "Is there a red rectangle in `photo`?"},
            "target": {"type": "box", "instructions": "The red rectangle in `photo`."},
        },
    }


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, *, live=False, opener=None):
    if not live:
        return 0
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        print("Missing authentication; no request sent.")
        return 2
    png = fixture.synthetic_png()
    fixture.verify_fixture(png)
    payload = request(png)
    settings = core.Settings(api_key=key, base_url="https://api.typesafe.ai", model=MODEL,
                             timeout_seconds=TIMEOUT_SECONDS, max_retries=0,
                             max_response_bytes=MAX_RESPONSE_BYTES)
    core.validate_request(payload, settings)
    report = {
        "status": "attempt_reserved_outcome_unknown",
        "utc": datetime.now(timezone.utc).isoformat(),
        "http_status": None,
        "request": {"endpoint": ENDPOINT, "model": MODEL},
        "fixture": {"sha256": hashlib.sha256(png).hexdigest(), "width": fixture.SIDE,
                    "height": fixture.SIDE, "expected_box": fixture.RECT, "pixel_verified": True},
        "source": {"probe_sha256": digest(Path(__file__)),
                   "fixture_source_sha256": digest(Path(fixture.__file__)),
                   "product_sha256": {name: digest(ROOT / name) for name in PRODUCT_PATHS},
                   "docs": fixture.SOURCES, "retrieved": "2026-11-10"},
        "rule": {"max_edge_error": EDGE_TOLERANCE,
                 "scope": "synthetic localization only; presence raw; no calibration or action authorization"},
    }
    # Reuse the existing exclusive/no-follow reservation and fsync implementation.
    with fixture.reserve_output(output) as stream:
        fixture.write_report(stream, report)
        network = opener if opener is not None else build_opener(ProxyHandler({}), core._NoRedirectHandler())

        def open_once(sent, timeout):
            response = network.open(sent, timeout=timeout)
            report["http_status"] = response.status
            if response.status != 200:
                response.close()
                raise core.APIError(response.status, "unexpected HTTP status")
            return response

        try:
            # Only the existing transport seam changes; validation, multipart,
            # bounded reads and typed response processing are the product path.
            with patch.object(core, "urlopen", open_once):
                result = core.TypeSafeClient(settings).evaluate(payload)
            if result["model"] != MODEL:
                raise ValueError("unexpected model")
            visible, target = result["answers"]["visible"], result["answers"]["target"]
            report.update({
                "model": MODEL,
                "answers": {
                    "visible": {"type": "noul", "noul": visible["noul"]},
                    "target": {"type": "box", "box": target["box"], "confidence": target["confidence"]},
                },
                "usage": {name: result["usage"][name] for name in ("input_tokens", "output_tokens")},
                "geometry_pass": all(abs(target["box"][name] - edge) <= EDGE_TOLERANCE
                                     for name, edge in fixture.RECT.items()),
            })
            report["status"] = "pass" if report["geometry_pass"] else "fixture_mismatch"
        except core.APIError as error:
            if type(error.status) is int and 100 <= error.status <= 599:
                report["http_status"] = error.status
            report["status"] = "transport_or_validation_error"
        except Exception:
            report["status"] = "transport_or_validation_error"
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
        print("Probe incomplete; outcome may be unknown. Inspect the retained marker. "
              "Do not retry, including at a new path.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
