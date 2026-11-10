"""Bounded inline image validation and provider multipart encoding.

No filesystem or network access. JPEG validation checks marker/scan structure,
not entropy coefficients or pixels; the provider remains the final decoder.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import struct
import zlib
from typing import Any


MAX_ASSETS = 4
MAX_IMAGE_BYTES = 256 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
MIN_SIDE = 280
MAX_SIDE = 1568
MAX_REFERENCE_BYTES = 1024
MAX_REFERENCES_BYTES = 65536
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
IMAGE_OPT_IN_HEADER = "TypeSafe-Danger-Zone-Images"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _invalid(message: str) -> None:
    raise ValueError(message)


def _dimensions(width: int, height: int) -> None:
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        _invalid(f"image sides must be {MIN_SIDE}-{MAX_SIDE} pixels")


def _png(data: bytes) -> tuple[int, int]:
    if not data.startswith(PNG_SIGNATURE):
        _invalid("invalid PNG signature")
    offset = len(PNG_SIGNATURE)
    width = height = channels = 0
    compressed = bytearray()
    seen: set[bytes] = set()
    last = b""
    while offset < len(data):
        if offset + 12 > len(data):
            _invalid("truncated PNG chunk")
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            _invalid("truncated PNG chunk")
        chunk = data[offset + 8:offset + 8 + size]
        crc = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
            _invalid("invalid PNG CRC")
        if not seen and kind != b"IHDR":
            _invalid("PNG must start with IHDR")
        if kind == b"IHDR":
            if seen or size != 13:
                _invalid("invalid PNG IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            _dimensions(width, height)
            if depth != 8 or color not in {0, 2, 4, 6} or (compression, filtering, interlace) != (0, 0, 0):
                _invalid("PNG supports only noninterlaced 8-bit grayscale/RGB with optional alpha")
            channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
        elif kind == b"PLTE":
            if kind in seen or b"IDAT" in seen or color not in {2, 6} or not 3 <= size <= 768 or size % 3:
                _invalid("invalid PNG palette")
        elif kind == b"IDAT":
            if b"IDAT" in seen and last != b"IDAT":
                _invalid("PNG IDAT chunks must be consecutive")
            compressed.extend(chunk)
        elif kind == b"IEND":
            if size or b"IDAT" not in seen or end != len(data):
                _invalid("invalid PNG end")
            break
        else:
            _invalid("unsupported PNG chunk (including animation/metadata)")
        seen.add(kind)
        last = kind
        offset = end
    else:
        _invalid("missing PNG IEND")
    stride = width * channels + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise ValueError("invalid PNG compressed stream") from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        _invalid("invalid PNG scanline size or compressed stream termination")
    if any(pixels[row * stride] > 4 for row in range(height)):
        _invalid("invalid PNG scanline filter")
    return width, height


def _jpeg(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        _invalid("invalid JPEG signature")
    offset = 2
    frame: dict[int, int] = {}
    quant: set[int] = set()
    huffman: set[tuple[int, int]] = set()
    width = height = 0
    restart_interval = 0
    scanned = False
    while offset < len(data):
        if data[offset] != 0xFF:
            _invalid("invalid JPEG marker boundary")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            _invalid("truncated JPEG marker")
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            if not scanned or offset != len(data):
                _invalid("invalid JPEG end")
            return width, height
        if scanned:
            _invalid("JPEG supports only one baseline interleaved scan")
        if marker not in {0xC0, 0xC4, 0xDB, 0xDA, 0xDD, 0xFE, *range(0xE0, 0xF0)}:
            _invalid("unsupported JPEG marker; baseline sequential only")
        if offset + 2 > len(data):
            _invalid("truncated JPEG segment")
        size = int.from_bytes(data[offset:offset + 2], "big")
        if size < 2 or offset + size > len(data):
            _invalid("invalid JPEG segment length")
        chunk = data[offset + 2:offset + size]
        offset += size
        if marker == 0xDB:
            cursor = 0
            while cursor < len(chunk):
                table = chunk[cursor]
                if table > 3 or cursor + 65 > len(chunk):
                    _invalid("JPEG requires 8-bit quantization tables 0-3")
                if 0 in chunk[cursor + 1:cursor + 65]:
                    _invalid("invalid JPEG quantization value")
                quant.add(table)
                cursor += 65
            if not cursor:
                _invalid("empty JPEG quantization segment")
        elif marker == 0xC4:
            cursor = 0
            while cursor < len(chunk):
                if cursor + 17 > len(chunk):
                    _invalid("truncated JPEG Huffman table")
                table = chunk[cursor]
                counts = chunk[cursor + 1:cursor + 17]
                count = sum(counts)
                if table >> 4 > 1 or table & 15 > 3 or not 1 <= count <= 256 or cursor + 17 + count > len(chunk):
                    _invalid("invalid JPEG Huffman table")
                available = 1
                for number in counts:
                    available = available * 2 - number
                    if available <= 0:
                        _invalid("oversubscribed JPEG Huffman table")
                symbols = chunk[cursor + 17:cursor + 17 + count]
                if len(set(symbols)) != count:
                    _invalid("duplicate JPEG Huffman symbols")
                if table >> 4 == 0 and any(value > 11 for value in symbols):
                    _invalid("invalid baseline JPEG DC symbol")
                if table >> 4 == 1 and any(
                    value not in {0, 0xF0} and not 1 <= value & 15 <= 10 for value in symbols
                ):
                    _invalid("invalid baseline JPEG AC symbol")
                huffman.add((table >> 4, table & 15))
                cursor += 17 + count
            if not cursor:
                _invalid("empty JPEG Huffman segment")
        elif marker == 0xC0:
            if frame or len(chunk) < 6 or chunk[0] != 8:
                _invalid("invalid baseline JPEG frame")
            height, width = struct.unpack(">HH", chunk[1:5])
            _dimensions(width, height)
            components = chunk[5]
            if components not in {1, 3} or len(chunk) != 6 + 3 * components:
                _invalid("JPEG supports grayscale or three-component frames")
            sampling_blocks = 0
            for cursor in range(6, len(chunk), 3):
                component, sampling, table = chunk[cursor:cursor + 3]
                if component in frame or not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 15 <= 4 or table > 3:
                    _invalid("invalid JPEG frame component")
                sampling_blocks += (sampling >> 4) * (sampling & 15)
                frame[component] = table
            if sampling_blocks > 10:
                _invalid("unsupported JPEG sampling factors")
        elif marker == 0xDD:
            if len(chunk) != 2:
                _invalid("invalid JPEG restart interval")
            restart_interval = int.from_bytes(chunk, "big")
        elif marker == 0xDA:
            if not frame or not chunk or chunk[0] != len(frame) or len(chunk) != 4 + 2 * len(frame) or chunk[-3:] != b"\x00\x3f\x00":
                _invalid("invalid baseline JPEG scan header")
            selected: set[int] = set()
            for cursor in range(1, len(chunk) - 3, 2):
                component, tables = chunk[cursor:cursor + 2]
                if component not in frame or component in selected or frame[component] not in quant or (0, tables >> 4) not in huffman or (1, tables & 15) not in huffman:
                    _invalid("JPEG scan references missing or duplicate components/tables")
                selected.add(component)
            scanned = True
            entropy_bytes = 0
            restart = 0
            while offset < len(data):
                if data[offset] != 0xFF:
                    entropy_bytes += 1
                    offset += 1
                    continue
                start = offset
                offset += 1
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                if offset == len(data):
                    _invalid("truncated JPEG scan")
                code = data[offset]
                if code == 0:
                    if offset != start + 1:
                        _invalid("invalid JPEG byte stuffing")
                    entropy_bytes += 1
                    offset += 1
                elif 0xD0 <= code <= 0xD7:
                    if not restart_interval or code != 0xD0 + restart or not entropy_bytes:
                        _invalid("invalid JPEG restart sequence")
                    restart = (restart + 1) % 8
                    entropy_bytes = 0
                    offset += 1
                else:
                    if not entropy_bytes:
                        _invalid("empty JPEG scan")
                    offset = start
                    break
    _invalid("missing JPEG EOI")


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _occurrences(text: str, reference: str) -> int:
    count = 0
    start = 0
    while True:
        start = text.find(reference, start)
        if start < 0:
            return count
        count += 1
        start += 1


def validate_assets(assets: Any, payload: dict[str, Any]) -> dict[str, tuple[bytes, str]]:
    """Return validated files, or ValueError without echoing untrusted data."""
    if not isinstance(assets, dict) or not 1 <= len(assets) <= MAX_ASSETS:
        _invalid(f"assets must contain 1-{MAX_ASSETS} inline images")
    references_bytes = 0
    total_bytes = 0
    expanded = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    values = list(_strings(payload["state"]))
    for question in payload["questions"].values():
        values.extend(_strings(question["instructions"]))
        values.extend(_strings(question.get("criteria")))
    files = {}
    for reference, asset in assets.items():
        if not isinstance(reference, str) or not reference.strip() or len(reference.encode("utf-8")) > MAX_REFERENCE_BYTES:
            _invalid("asset references must be nonempty and at most 1024 UTF-8 bytes")
        references_bytes += len(reference.encode("utf-8"))
        if references_bytes > MAX_REFERENCES_BYTES:
            _invalid("asset references exceed total byte limit")
        if not isinstance(asset, dict) or set(asset) - {"type", "format", "data_base64"}:
            _invalid("assets accept only type, format and data_base64; no paths, URLs or parts")
        if asset.get("type", "image") != "image" or not isinstance(asset.get("format"), str) or asset["format"] not in {"png", "jpg", "jpeg"}:
            _invalid("assets require image type and png/jpg/jpeg format")
        encoded = asset.get("data_base64")
        if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BASE64_CHARS:
            _invalid("invalid Base64 image or image byte limit exceeded")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid image Base64") from exc
        if base64.b64encode(data).decode("ascii") != encoded:
            _invalid("image Base64 must use canonical padding")
        total_bytes += len(data)
        if total_bytes > MAX_IMAGE_BYTES:
            _invalid("decoded images exceed 256 KiB total byte limit")
        width, height = _png(data) if asset["format"] == "png" else _jpeg(data)
        # Count overlapping occurrences too: never underestimate repeated input.
        occurrences = sum(_occurrences(text, reference) for text in values)
        if not occurrences:
            _invalid("every asset must occur in a state/instructions/criteria string value")
        expanded += occurrences * max(len(data), width * height * 4 + height)
        if expanded > MAX_EXPANDED_BYTES:
            _invalid("image reference expansion exceeds 32 MiB conservative local limit")
        files[reference] = (data, "image/png" if asset["format"] == "png" else "image/jpeg")
    return files


def multipart(payload: dict[str, Any], files: dict[str, tuple[bytes, str]]) -> tuple[bytes, str]:
    """Encode internally named parts; caller-provided strings never form headers."""
    request = {key: value for key, value in payload.items() if key not in {"assets", "allow_images"}}
    request["assets"] = {reference: {"type": "image", "part": f"image{index}"} for index, reference in enumerate(files)}
    document = json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    boundary = "arbitype-" + secrets.token_hex(24)
    while boundary.encode() in document or any(boundary.encode() in data for data, _ in files.values()):
        boundary = "arbitype-" + secrets.token_hex(24)
    parts = [b'Content-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n' + document]
    for index, (data, mime) in enumerate(files.values()):
        parts.append(f'Content-Disposition: form-data; name="image{index}"; filename="image{index}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + data)
    delimiter = b"--" + boundary.encode()
    body = b"".join(delimiter + b"\r\n" + part + b"\r\n" for part in parts) + delimiter + b"--\r\n"
    return body, f"multipart/form-data; boundary={boundary}"
