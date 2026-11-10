"""Synthetic, provider-free image contract and transport regressions."""

import base64
import copy
import io
import json
import struct
import threading
import unittest
import zlib
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from arbitype import core, images, mcp
from test_server import FakeResponse, noul_response


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width=280, height=280, raw=None, extra=b"", depth=8, color=0, interlace=0):
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    if raw is None:
        raw = (b"\x00" + b"\x7f" * width) * height
    return images.PNG_SIGNATURE + chunk(b"IHDR", header) + extra + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def segment(marker, data):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(data) + 2) + data


def jpeg(width=280, height=280):
    # Constant grayscale: each block has zero DC delta and AC EOB. Both
    # one-symbol tables use code 0. Pad the last entropy byte with ones.
    blocks = ((width + 7) // 8) * ((height + 7) // 8)
    bits = "00" * blocks
    bits += "1" * (-len(bits) % 8)
    entropy = bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8)).replace(b"\xff", b"\xff\x00")
    table = b"\x01" + b"\x00" * 15 + b"\x00"
    return (
        b"\xff\xd8"
        + segment(0xDB, b"\x00" + b"\x01" * 64)
        + segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00")
        + segment(0xC4, b"\x00" + table + b"\x10" + table)
        + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + entropy + b"\xff\xd9"
    )


def request(data=None, fmt="png", reference="image-reference-1"):
    return {
        "state": {"photo": reference},
        "questions": {"ready": {"type": "noul", "instructions": "Is the photo visible?"}},
        "allow_images": True,
        "assets": {reference: {"format": fmt, "data_base64": base64.b64encode(png() if data is None else data).decode()}},
    }


def box_request():
    value = request()
    value["questions"]["target"] = {"type": "box", "instructions": "The rectangle in photo"}
    return value


def box_response():
    value = noul_response()
    value["answers"]["target"] = {
        "type": "box", "box": {"left": 0, "top": 0, "right": 1, "bottom": 1}, "confidence": 0,
    }
    return value


class BoxTests(unittest.TestCase):
    def test_combined_box_and_presence_schema_runtime_transport(self):
        value = box_request()
        with patch.dict("os.environ", TYPESAFE_API_KEY="synthetic"), patch.object(
            core, "urlopen", return_value=FakeResponse(box_response())
        ) as opener:
            result = mcp.call_tool("evaluate", value)
        self.assertEqual(result, box_response())
        self.assertEqual(opener.call_count, 1)
        sent = opener.call_args.args[0]
        self.assertEqual(sent.get_header("Typesafe-danger-zone-images"), "true")
        self.assertIn(b'"type":"box"', sent.data)
        self.assertIn(b'"type":"noul"', sent.data)
        for confidence in (0, 1, 0.5):
            response = box_response()
            response["answers"]["target"]["confidence"] = confidence
            core.validate_api_response(response, value["questions"])

    def test_invalid_questions_never_send(self):
        mutations = [
            lambda r: r.pop("assets"), lambda r: r.pop("allow_images"),
            lambda r: r.update(assets={}), lambda r: r.update(allow_images=False),
        ]
        for criteria in (None, {}, [], "forbidden"):
            mutations.append(lambda r, c=criteria: r["questions"]["target"].update(criteria=c))
        for instructions in (None, "", "  ", {}, [], True, 1):
            mutations.append(lambda r, i=instructions: r["questions"]["target"].update(instructions=i))
        mutations.extend([
            lambda r: r["questions"]["target"].pop("instructions"),
            lambda r: r["questions"]["target"].update(extra="bad"),
        ])
        for mutate in mutations:
            value = box_request()
            mutate(value)
            with self.subTest(value=value), patch.object(core, "urlopen") as opener:
                with self.assertRaises(core.BridgeError):
                    core.post_to_typesafe(value, settings=core.Settings(api_key="synthetic"))
                with patch.dict("os.environ", TYPESAFE_API_KEY="synthetic"), self.assertRaises(core.BridgeError):
                    mcp.call_tool("evaluate", value)
                opener.assert_not_called()

    def test_invalid_box_answers_fail_closed_through_mcp(self):
        mutations = [
            lambda a: a.pop("box"), lambda a: a.update(box=None),
            lambda a: a.update(box=[]), lambda a: a["box"].update(extra=0),
            lambda a: a.update(type="noul"), lambda a: a.pop("confidence"),
            lambda a: a["box"].update(left=1, right=0),
            lambda a: a["box"].update(top=1, bottom=0),
            lambda a: a["box"].update(left=0.5, right=0.5),
            lambda a: a["box"].update(top=0.5, bottom=0.5),
        ]
        for edge in ("left", "top", "right", "bottom"):
            mutations.append(lambda a, e=edge: a["box"].pop(e))
            for bad in (None, True, False, "0.5", [], {}, -0.1, 1.1, float("nan"), float("inf"), -float("inf")):
                mutations.append(lambda a, e=edge, b=bad: a["box"].update({e: b}))
        for bad in (None, True, False, "0.5", [], {}, -0.1, 1.1, float("nan"), float("inf"), -float("inf")):
            mutations.append(lambda a, b=bad: a.update(confidence=b))
        for index, mutate in enumerate(mutations):
            response = box_response()
            mutate(response["answers"]["target"])
            with self.subTest(index=index), patch.dict("os.environ", TYPESAFE_API_KEY="synthetic"), patch.object(
                core, "urlopen", return_value=FakeResponse(response)
            ) as opener:
                result = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "evaluate", "arguments": box_request()}})
                self.assertTrue(result["result"]["isError"])
                self.assertNotIn("structuredContent", result["result"])
                self.assertEqual(opener.call_count, 1)


class ImageValidationTests(unittest.TestCase):
    def test_valid_formats_and_dimensions(self):
        for fmt, data in (("png", png()), ("jpg", jpeg()), ("jpeg", jpeg()), ("png", png(1568, 1568))):
            with self.subTest(fmt=fmt, size=len(data)):
                self.assertIn("assets", core.validate_request(request(data, fmt)))

    def assert_local_failure(self, payload):
        with patch.object(core, "urlopen") as opener:
            with self.assertRaises(core.BridgeError):
                core.post_to_typesafe(payload, settings=core.Settings(api_key="fake"))
            opener.assert_not_called()

    def test_opt_in_and_asset_shapes(self):
        for change in ({"allow_images": False}, {"allow_images": "true"}, {"assets": {}}, {"assets": []}, {"assets": None}):
            value = request()
            value.update(change)
            self.assert_local_failure(value)
        value = request()
        del value["allow_images"]
        self.assert_local_failure(value)
        for asset in ({"format": "gif", "data_base64": "AAAA"}, {"format": []}, {"part": "upload"}, {"path": "/private"}, {"url": "https://example.test"}, {"type": "video", "format": "png", "data_base64": "AAAA"}):
            value = request()
            value["assets"] = {"image-reference-1": asset}
            self.assert_local_failure(value)

    def test_base64_is_strict_and_bounded(self):
        for encoded in ("", "@@@@", "Zg=", "Zh==", "Zg===", "Zg==\n", "data:image/png;base64,AAAA", "é", "A" * (images.MAX_BASE64_CHARS + 1)):
            value = request()
            value["assets"]["image-reference-1"]["data_base64"] = encoded
            self.assert_local_failure(value)
        self.assert_local_failure(request(b"x" * (images.MAX_IMAGE_BYTES + 1)))

    def test_png_corruption_structure_and_bounded_decompression(self):
        good = png()
        invalid = [
            b"GIF89a", good[:-1], good + b"trailing", good[:40] + bytes([good[40] ^ 1]) + good[41:],
            png(279), png(1569), png(depth=16), png(interlace=1), png(color=3),
            png(extra=chunk(b"acTL", b"\x00" * 8)), png(extra=chunk(b"tEXt", b"name\x00value")),
            png(raw=b"\x00"), png(raw=b"\x00" * (280 * 281 + 1)),
            png(raw=b"\x05" + b"\x00" * (280 * 281 - 1)),
            png(raw=b"\x00" * (16 * 1024 * 1024)),
            images.PNG_SIGNATURE + chunk(b"IDAT", b"bad") + chunk(b"IEND", b""),
        ]
        for index, data in enumerate(invalid):
            with self.subTest(index=index):
                self.assert_local_failure(request(data))
        # A concatenated second zlib stream is not accepted as image data.
        data = images.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 0, 0, 0, 0))
        data += chunk(b"IDAT", zlib.compress(b"\x00" * (280 * 281)) + zlib.compress(b"extra")) + chunk(b"IEND", b"")
        self.assert_local_failure(request(data))

    def test_jpeg_marker_scan_and_table_failures(self):
        good = jpeg()
        invalid = [
            good[:-2], good + b"extra", good.replace(b"\xff\xc0", b"\xff\xc2"),
            jpeg(279), jpeg(1569), good.replace(b"\xff\xda\x00\x08", b"\xff\xda\xff\xff"),
            good.replace(b"\x00\x3f\x00", b"\x00\x3e\x00"),
            good.replace(b"\x01\x01\x00\x00\x3f\x00", b"\x01\x02\x00\x00\x3f\x00"),
            good.replace(b"\xff\xdb", b"\xff\xe0"),
            good.replace(b"\xff\xc4", b"\xff\xe0"),
            good[:-2] + b"\xff\xd0\x00\xff\xd9",
            good[:-2] + b"\xff\x00\xff", b"\xff\xd8\xff\xd9",
        ]
        for index, data in enumerate(invalid):
            with self.subTest(index=index):
                self.assert_local_failure(request(data, "jpeg"))
        self.assert_local_failure(request(good, "png"))
        self.assert_local_failure(request(png(), "jpeg"))

    def test_references_only_count_values_and_expansion_is_bounded(self):
        for reference in ("", " ", "é" * 513):
            self.assert_local_failure(request(reference=reference))
        value = request()
        value["state"] = {"image-reference-1": "unrelated"}
        self.assert_local_failure(value)
        value["state"] = "unrelated"
        value["questions"]["ready"]["criteria"] = {"true": {"nested": ["image-reference-1"]}}
        core.validate_request(value)
        value = request()
        value["state"] = "image-reference-1 " * 200
        self.assert_local_failure(value)
        value = request()
        value["assets"] = {str(index): copy.deepcopy(next(iter(value["assets"].values()))) for index in range(5)}
        self.assert_local_failure(value)

    def test_total_decoded_budget_and_json_limit(self):
        # Legal JPEG metadata makes each individually valid upload large.
        data = jpeg()
        data = data[:2] + segment(0xFE, b"x" * 60000) * 3 + data[2:]
        value = request(data, "jpg")
        value["state"] = ["image-reference-1", "second-reference"]
        value["assets"]["second-reference"] = copy.deepcopy(value["assets"]["image-reference-1"])
        self.assert_local_failure(value)
        with patch.object(core, "urlopen") as opener, self.assertRaisesRegex(core.BridgeError, "request is"):
            core.post_to_typesafe(request(), settings=core.Settings(api_key="fake", max_request_bytes=100))
        opener.assert_not_called()

    def test_png_chunk_order_and_stream_errors(self):
        header = chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 0, 0, 0, 0))
        compressed = zlib.compress(b"\x00" * (280 * 281))
        end = chunk(b"IEND", b"")
        for chunks in (
            header + header + chunk(b"IDAT", compressed) + end,
            header + chunk(b"IDAT", b"not-zlib") + end,
            header + chunk(b"IDAT", compressed),
            header + end,
            header + chunk(b"IEND", b"extra"),
        ):
            self.assert_local_failure(request(images.PNG_SIGNATURE + chunks))
        split = images.PNG_SIGNATURE + header + chunk(b"IDAT", compressed[:4]) + chunk(b"IDAT", compressed[4:]) + end
        core.validate_request(request(split))


class ImageTransportTests(unittest.TestCase):
    def test_multiple_assets_use_unique_internal_parts_and_no_header_injection(self):
        value = request(reference='image-ref"\r\nX-Injected: bad')
        value["assets"]["second-reference"] = request(jpeg(), "jpg")["assets"]["image-reference-1"]
        value["state"]["second"] = "second-reference"
        with patch.object(core, "urlopen", return_value=FakeResponse(noul_response())) as opener:
            core.post_to_typesafe(value, settings=core.Settings(api_key="fake"))
        sent = opener.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + sent.get_header("Content-type").encode() + b"\r\n\r\n" + sent.data)
        parts = list(parsed.iter_parts())
        self.assertEqual([part.get_param("name", header="content-disposition") for part in parts], ["request", "image0", "image1"])
        self.assertTrue(all(part.get("X-Injected") is None for part in parts))
        self.assertEqual(parts[2].get_payload(decode=True), jpeg())

    def test_final_multipart_size_limit_and_image_response_limits(self):
        value = request()
        normalized = core.validate_request(value)
        inline_size = len(core._json_bytes(normalized, context="request"))
        with patch.object(core, "urlopen") as opener, self.assertRaisesRegex(core.BridgeError, "multipart request"):
            core.post_to_typesafe(value, settings=core.Settings(api_key="fake", max_request_bytes=inline_size))
        opener.assert_not_called()
        with patch.object(core, "urlopen", return_value=FakeResponse(noul_response())) as opener, self.assertRaisesRegex(core.APIError, "exceeded"):
            core.post_to_typesafe(value, settings=core.Settings(api_key="fake", max_response_bytes=10))
        self.assertEqual(opener.call_count, 1)

    def test_opt_in_without_assets_does_not_change_text_http(self):
        value = {"state": "text", "questions": request()["questions"], "allow_images": True}
        with patch.object(core, "urlopen", return_value=FakeResponse(noul_response())) as opener:
            core.post_to_typesafe(value, settings=core.Settings(api_key="fake"))
        sent = opener.call_args.args[0]
        self.assertEqual(sent.get_header("Content-type"), "application/json")
        self.assertIsNone(sent.get_header("Typesafe-danger-zone-images"))
        self.assertNotIn("allow_images", json.loads(sent.data))

    def test_real_urllib_multipart_and_redirect_rejection_on_loopback(self):
        received = []
        redirect = [False]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received.append((self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
                if redirect[0]:
                    self.send_response(307)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirect-target")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(noul_response()).encode())

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = core.Settings(api_key="synthetic-key", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=6)
            core.post_to_typesafe(request(), settings=settings)
            redirect[0] = True
            with self.assertRaises(core.APIError) as caught:
                core.post_to_typesafe(request(), settings=settings)
            self.assertEqual(caught.exception.status, 307)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(len(received), 2)
        for path, headers, body in received:
            self.assertEqual(path, "/v1/systemone")
            self.assertEqual(headers[images.IMAGE_OPT_IN_HEADER], "true")
            parsed = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = list(parsed.iter_parts())
            self.assertEqual(parts[1].get_payload(decode=True), png())

    def test_multipart_typed_image_questions_and_schema(self):
        for fmt, data in (("png", png()), ("jpeg", jpeg())):
            value = request(data, fmt)
            value["questions"].update({
                "which": {"type": "choice", "instructions": "Which shade?", "criteria": {"gray": None, "white": None}},
                "level": {"type": "score", "instructions": "Brightness?", "criteria": ["dark", "light"]},
            })
            response = noul_response()
            response["answers"].update({
                "which": {"type": "choice", "choice": "gray", "probabilities": {"gray": 0.9, "white": 0.1}, "confidence": 0.8},
                "level": {"type": "score", "score": 0.5, "legend": {"0": "dark", "1": "light"}, "probabilities": {"0": 0.5, "1": 0.5}, "confidence": 0.8},
            })
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "synthetic-key"}), patch.object(core, "urlopen", return_value=FakeResponse(response)) as opener:
                result = mcp.call_tool("evaluate", value)
            self.assertEqual(result, response)
            sent = opener.call_args.args[0]
            self.assertEqual(sent.get_header("Typesafe-danger-zone-images"), "true")
            parsed = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + sent.get_header("Content-type").encode() + b"\r\n\r\n" + sent.data)
            parts = list(parsed.iter_parts())
            self.assertEqual(len(parts), 2)
            self.assertEqual(parts[0].get_param("name", header="content-disposition"), "request")
            document = json.loads(parts[0].get_payload(decode=True))
            self.assertNotIn("allow_images", document)
            self.assertNotIn("data_base64", json.dumps(document))
            self.assertEqual(document["assets"]["image-reference-1"], {"type": "image", "part": "image0"})
            self.assertEqual(parts[1].get_payload(decode=True), data)
            self.assertNotIn(b"synthetic-key", sent.data)

    def test_image_errors_never_retry_and_redact(self):
        for status in (302, 400, 401, 413, 429, 500, 529):
            error = HTTPError("https://example.test", status, "failure", {"x-typesafe-request-id": "req-1"}, io.BytesIO(b'{"message":"api_key=synthetic-key"}'))
            sleeps = []
            with patch.object(core, "urlopen", side_effect=error) as opener:
                with self.assertRaises(core.APIError) as caught:
                    core.post_to_typesafe(request(), settings=core.Settings(api_key="synthetic-key", max_retries=6), sleep=sleeps.append)
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(caught.exception.request_id, "req-1")
            self.assertNotIn("synthetic-key", str(caught.exception))
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(sleeps, [])
        for error in (URLError("lost"), TimeoutError(), OSError()):
            with patch.object(core, "urlopen", side_effect=error) as opener, self.assertRaises(core.APIError):
                core.post_to_typesafe(request(), settings=core.Settings(api_key="fake", max_retries=6))
            self.assertEqual(opener.call_count, 1)

    def test_malformed_response_remains_error(self):
        response = noul_response()
        response["answers"]["ready"]["type"] = "box"
        with patch.object(core, "urlopen", return_value=FakeResponse(response)), self.assertRaises(core.APIError):
            core.post_to_typesafe(request(), settings=core.Settings(api_key="fake"))

    def test_specialized_tools_do_not_accept_images(self):
        with patch.object(mcp, "TypeSafeClient") as client, self.assertRaises(core.BridgeError):
            mcp.call_tool("check", {"state": "x", "instructions": "Ready?", "assets": request()["assets"]})
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
