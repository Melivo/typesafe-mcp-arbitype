"""Configuration, validation, and HTTP transport for Arbitype.

The project intentionally uses only the Python standard library. Keeping the
transport small makes the MCP service easy to audit and lets it run in any
MCP-capable agent host that can start Python 3.10 or newer.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import random
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ._version import __version__
from . import images


SERVER_NAME = "arbitype"
SERVER_VERSION = __version__
DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
# Match the official Python SDK's default per-operation timeout.  Keeping one
# attempt below common MCP host tool timeouts leaves room for bounded retries
# and backoff without making a tool appear hung.
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_MAX_BACKOFF_SECONDS = 20.0
DEFAULT_RETRY_JITTER = 0.25
DEFAULT_MAX_STATE_CHARS = 120_000
DEFAULT_MAX_QUESTION_CHARS = 60_000
DEFAULT_MAX_REQUEST_BYTES = 512_000
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_QUESTIONS = 64
DEFAULT_MAX_QUESTION_ID_CHARS = 128
DEFAULT_MAX_OPTION_CHARS = 256
DEFAULT_MAX_MODEL_CHARS = 128
MAX_INPUT_LINE_BYTES = 16 * 1024 * 1024
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

JsonObject = dict[str, Any]
Sleep = Callable[[float], None]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Turn provider redirects into errors before urllib can resend a request."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise HTTPError(req.full_url, code, "redirects are disabled", headers, fp)


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def urlopen(request: Request, timeout: float | None = None) -> Any:
    """Open one request without following redirects.

    This remains a module-level seam so tests can replace the network call
    without installing a process-wide urllib opener.
    """

    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class BridgeError(Exception):
    """An expected, safe-to-display bridge failure."""


class ConfigError(BridgeError):
    """The local configuration is missing or invalid."""


@dataclass
class APIError(BridgeError):
    """A TypeSafe HTTP or transport failure with redacted detail."""

    status: int | None
    detail: str
    request_id: str | None = None
    retry_after: float | None = None

    def __str__(self) -> str:
        if self.status is None:
            message = self.detail
        else:
            message = f"TypeSafe API returned HTTP {self.status}: {self.detail}"
        if self.request_id:
            message += f" (request_id={self.request_id})"
        return message


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from environment variables.

    The API key is deliberately excluded from ``repr`` so accidental config
    logging cannot print it.
    """

    api_key: str = field(default="", repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    retry_jitter: float = DEFAULT_RETRY_JITTER
    max_state_chars: int = DEFAULT_MAX_STATE_CHARS
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_questions: int = DEFAULT_MAX_QUESTIONS

    def __post_init__(self) -> None:
        _validate_base_url(self.base_url)

    @classmethod
    def from_env(cls, *, require_key: bool = True) -> "Settings":
        api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        if require_key and not api_key:
            raise ConfigError(
                "TYPESAFE_API_KEY is not set; set it in the MCP server environment"
            )

        base_url = os.getenv("TYPESAFE_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")

        # TYPESAFE_MODEL was used by the first public version.  The official
        # SDK calls the equivalent setting TYPESAFE_DEFAULT_MODEL, so support
        # both while keeping the old name's precedence for compatibility.
        model = (
            os.getenv("TYPESAFE_MODEL")
            or os.getenv("TYPESAFE_DEFAULT_MODEL")
            or DEFAULT_MODEL
        ).strip()
        if not model or len(model) > DEFAULT_MAX_MODEL_CHARS:
            raise ConfigError("TYPESAFE_MODEL must be 1-128 characters")

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=_env_float(
                "TYPESAFE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, 1.0, 300.0
            ),
            max_retries=_env_int("TYPESAFE_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, 6),
            retry_backoff_seconds=_env_float(
                "TYPESAFE_RETRY_BACKOFF_SECONDS",
                DEFAULT_RETRY_BACKOFF_SECONDS,
                0.0,
                30.0,
            ),
            max_backoff_seconds=_env_float(
                "TYPESAFE_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF_SECONDS, 0.0, 120.0
            ),
            retry_jitter=_env_float("TYPESAFE_RETRY_JITTER", DEFAULT_RETRY_JITTER, 0.0, 1.0),
            max_state_chars=_env_int(
                "TYPESAFE_MAX_STATE_CHARS", DEFAULT_MAX_STATE_CHARS, 1_000, 10_000_000
            ),
            max_question_chars=_env_int(
                "TYPESAFE_MAX_QUESTION_CHARS", DEFAULT_MAX_QUESTION_CHARS, 1_000, 10_000_000
            ),
            max_request_bytes=_env_int(
                "TYPESAFE_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES, 4_096, 20_000_000
            ),
            max_response_bytes=_env_int(
                "TYPESAFE_MAX_RESPONSE_BYTES",
                DEFAULT_MAX_RESPONSE_BYTES,
                4_096,
                50_000_000,
            ),
            max_questions=_env_int("TYPESAFE_MAX_QUESTIONS", DEFAULT_MAX_QUESTIONS, 1, 512),
        )


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_base_url(base_url: str) -> None:
    if not isinstance(base_url, str):
        raise ConfigError("TYPESAFE_BASE_URL must be an http(s) URL")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ConfigError("TYPESAFE_BASE_URL must be a valid http(s) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("TYPESAFE_BASE_URL must be an http(s) URL")
    if hostname is None:
        raise ConfigError("TYPESAFE_BASE_URL must include a host")
    if parsed.username or parsed.password:
        raise ConfigError("TYPESAFE_BASE_URL must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError("TYPESAFE_BASE_URL must not contain a query or fragment")
    if parsed.scheme == "http" and not _is_loopback_host(hostname):
        raise ConfigError(
            "TYPESAFE_BASE_URL must use HTTPS; HTTP is only allowed for loopback hosts"
        )


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _is_structured(value: Any, *, allow_null: bool = False) -> bool:
    if value is None:
        return allow_null
    return isinstance(value, (str, dict, list))


def _has_structured_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return True


def _json_bytes(value: Any, *, context: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"{context} must contain JSON-serializable values") from exc


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def validate_request(arguments: Any, settings: Settings | None = None) -> JsonObject:
    """Validate and normalize a TypeSafe evaluation request."""

    if not isinstance(arguments, dict):
        raise BridgeError("arguments must be a JSON object")
    if settings is None:
        settings = Settings.from_env(require_key=False)

    allowed = {"state", "questions", "model", "assets", "allow_images"}
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise BridgeError(f"unsupported argument(s): {', '.join(map(str, unknown))}")

    if "state" not in arguments:
        raise BridgeError("missing required argument: state")
    state = arguments["state"]
    if not _is_structured(state):
        raise BridgeError("state must be a string, object, or array")

    questions = arguments.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise BridgeError("questions must be a non-empty JSON object")
    if len(questions) > settings.max_questions:
        raise BridgeError(f"questions cannot contain more than {settings.max_questions} entries")

    normalized_questions: JsonObject = {}
    for question_id, question in questions.items():
        if (
            not isinstance(question_id, str)
            or not question_id.strip()
            or len(question_id) > DEFAULT_MAX_QUESTION_ID_CHARS
            or any(char in question_id for char in "\r\n")
        ):
            raise BridgeError("question ids must be non-empty strings of at most 128 characters")
        if not isinstance(question, dict):
            raise BridgeError(f"question {question_id!r} must be an object")
        unknown_question_fields = sorted(set(question) - {"type", "instructions", "criteria"})
        if unknown_question_fields:
            raise BridgeError(
                f"question {question_id!r} has unsupported field(s): "
                f"{', '.join(map(str, unknown_question_fields))}"
            )

        question_type = question.get("type")
        if question_type not in {"noul", "choice", "score", "box"}:
            raise BridgeError(
                f"question {question_id!r} has unsupported type; use noul, choice, score, or box"
            )
        if "instructions" not in question or not _is_structured(
            question["instructions"], allow_null=True
        ):
            raise BridgeError(
                f"question {question_id!r} instructions must be a string, object, array, or null"
            )

        criteria = question.get("criteria")
        if question_type == "box":
            if "criteria" in question:
                raise BridgeError(f"box question {question_id!r} does not accept criteria")
            if not _has_structured_content(question["instructions"]):
                raise BridgeError(f"box question {question_id!r} needs non-empty instructions")
            if "assets" not in arguments or arguments.get("allow_images") is not True:
                raise BridgeError("box questions require assets and explicit allow_images: true")
        elif question_type == "noul":
            if criteria is not None:
                if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                    raise BridgeError(
                        f"noul question {question_id!r} criteria must contain only true/false"
                    )
                if not all(_is_structured(value, allow_null=True) for value in criteria.values()):
                    raise BridgeError(
                        f"noul question {question_id!r} criteria values must be structured JSON or null"
                    )
            has_criteria = isinstance(criteria, dict) and any(
                _has_structured_content(value) for value in criteria.values()
            )
            if not _has_structured_content(question["instructions"]) and not has_criteria:
                raise BridgeError(
                    f"noul question {question_id!r} needs non-empty instructions or criteria"
                )
        elif question_type == "choice":
            if not isinstance(criteria, dict) or not criteria:
                raise BridgeError(
                    f"choice question {question_id!r} needs a non-empty criteria object"
                )
            if len(criteria) > 255:
                raise BridgeError(f"choice question {question_id!r} cannot contain more than 255 options")
            for option, description in criteria.items():
                if (
                    not isinstance(option, str)
                    or not option.strip()
                    or len(option) > DEFAULT_MAX_OPTION_CHARS
                ):
                    raise BridgeError(
                        f"choice question {question_id!r} option ids must be non-empty strings"
                    )
                if not _is_structured(description, allow_null=True):
                    raise BridgeError(
                        f"choice question {question_id!r} option descriptions must be structured JSON"
                    )
        else:
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise BridgeError(
                    f"score question {question_id!r} needs between 2 and 10 criteria levels"
                )
            if not all(_is_structured(level) for level in criteria):
                raise BridgeError(
                    f"score question {question_id!r} criteria levels must be non-null structured JSON"
                )

        normalized_questions[question_id] = {
            "type": question_type,
            "instructions": question["instructions"],
        }
        if criteria is not None:
            normalized_questions[question_id]["criteria"] = criteria

    model = arguments.get("model", settings.model)
    if not isinstance(model, str) or not model.strip() or len(model) > DEFAULT_MAX_MODEL_CHARS:
        raise BridgeError("model must be a non-empty string of at most 128 characters")

    state_bytes = _json_bytes(state, context="state")
    question_bytes = _json_bytes(normalized_questions, context="questions")
    if len(state_bytes) > settings.max_state_chars:
        raise BridgeError(
            f"state exceeds the {settings.max_state_chars}-byte limit; summarize or truncate it first"
        )
    if len(question_bytes) > settings.max_question_chars:
        raise BridgeError(
            f"questions exceed the {settings.max_question_chars}-byte limit; reduce descriptions"
        )

    payload: JsonObject = {
        "state": state,
        "model": model.strip(),
        "questions": normalized_questions,
    }
    if "allow_images" in arguments and not isinstance(arguments["allow_images"], bool):
        raise BridgeError("allow_images must be a boolean")
    if "assets" in arguments:
        if arguments.get("allow_images") is not True:
            raise BridgeError("assets require explicit allow_images: true")
        try:
            images.validate_assets(arguments["assets"], payload)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        payload["assets"] = arguments["assets"]
        payload["allow_images"] = True
    body = _json_bytes(payload, context="request")
    if len(body) > settings.max_request_bytes:
        raise BridgeError(
            f"request is {len(body)} bytes, over the {settings.max_request_bytes}-byte limit"
        )
    return payload


def _validate_probability_map(
    value: Any,
    expected_keys: set[str],
    *,
    context: str,
) -> None:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise BridgeError(f"TypeSafe response {context} probabilities do not match the question")
    probabilities = list(value.values())
    if not all(_is_number(probability) and 0 <= probability <= 1 for probability in probabilities):
        raise BridgeError(f"TypeSafe response {context} probabilities must be between 0 and 1")
    if abs(sum(probabilities) - 1.0) > 0.02:
        raise BridgeError(f"TypeSafe response {context} probabilities do not sum to 1")


def validate_api_response(response: Any, questions: Mapping[str, Any]) -> JsonObject:
    """Validate the stable parts of a TypeSafe response before exposing it."""

    if not isinstance(response, dict):
        raise APIError(None, "TypeSafe returned a non-object JSON response")
    model = response.get("model")
    if not isinstance(model, str) or not model.strip():
        raise APIError(None, "TypeSafe response is missing a model")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise APIError(None, "TypeSafe response is missing an answers object")

    for question_id, question in questions.items():
        if question_id not in answers:
            raise APIError(None, f"TypeSafe response is missing answer {question_id!r}")
        answer = answers[question_id]
        if not isinstance(answer, dict):
            raise APIError(None, f"TypeSafe answer {question_id!r} is not an object")
        expected_type = question["type"]
        if answer.get("type") != expected_type:
            raise APIError(None, f"TypeSafe answer {question_id!r} has the wrong type")

        if expected_type == "noul":
            if not _is_number(answer.get("noul")) or not 0 <= answer["noul"] <= 1:
                raise APIError(None, f"TypeSafe answer {question_id!r} has an invalid noul value")
        elif expected_type == "box":
            box = answer.get("box")
            if (
                not isinstance(box, dict)
                or set(box) != {"left", "top", "right", "bottom"}
                or not all(_is_number(value) and 0 <= value <= 1 for value in box.values())
                or not box["left"] < box["right"]
                or not box["top"] < box["bottom"]
            ):
                raise APIError(None, f"TypeSafe answer {question_id!r} has an invalid box")
            confidence = answer.get("confidence")
            if not _is_number(confidence) or not 0 <= confidence <= 1:
                raise APIError(None, f"TypeSafe answer {question_id!r} has invalid confidence")
        elif expected_type == "choice":
            criteria = question["criteria"]
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in criteria:
                raise APIError(None, f"TypeSafe answer {question_id!r} selected an unknown option")
            _validate_probability_map(
                answer.get("probabilities"), set(criteria), context=question_id
            )
            confidence = answer.get("confidence")
            if not _is_number(confidence) or not 0 <= confidence <= 1:
                raise APIError(None, f"TypeSafe answer {question_id!r} has invalid confidence")
            maximum = max(answer["probabilities"].values())
            if answer["probabilities"][choice] + 1e-9 < maximum:
                raise APIError(None, f"TypeSafe answer {question_id!r} is not the top probability")
        else:
            levels = question["criteria"]
            score = answer.get("score")
            if not _is_number(score) or not 0 <= score <= len(levels) - 1:
                raise APIError(None, f"TypeSafe answer {question_id!r} has an invalid score")
            legend = answer.get("legend")
            if not isinstance(legend, dict) or set(legend) != {str(index) for index in range(len(levels))}:
                raise APIError(None, f"TypeSafe answer {question_id!r} has an invalid legend")
            _validate_probability_map(
                answer.get("probabilities"), set(legend), context=question_id
            )
            confidence = answer.get("confidence")
            if not _is_number(confidence) or not 0 <= confidence <= 1:
                raise APIError(None, f"TypeSafe answer {question_id!r} has invalid confidence")
            expected_score = sum(
                int(level) * probability
                for level, probability in answer["probabilities"].items()
            )
            if abs(score - expected_score) > 0.02:
                raise APIError(None, f"TypeSafe answer {question_id!r} score disagrees with probabilities")

    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise APIError(None, "TypeSafe response is missing a usage object")
    for field_name in ("input_tokens", "output_tokens"):
        if field_name not in usage:
            raise APIError(None, f"TypeSafe response usage is missing {field_name}")
        if (
            not isinstance(usage[field_name], int)
            or isinstance(usage[field_name], bool)
            or usage[field_name] < 0
        ):
            raise APIError(None, f"TypeSafe response usage.{field_name} is invalid")
    return response


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([\"']?)[^\"'\s,}]+"),
    re.compile(r"\b(?:sk|apikey)_[A-Za-z0-9_-]{12,}\b"),
)


def redact_text(text: str, *, secret: str = "") -> str:
    """Redact credentials from diagnostics without touching normal state data."""

    if secret:
        text = text.replace(secret, "<redacted>")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: match.group(1) + "<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text


def _safe_error_detail(raw: bytes, *, secret: str = "") -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "empty error response"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        detail = ""
        if isinstance(value, dict):
            for key in ("message", "error", "detail", "code"):
                candidate = value.get(key)
                if isinstance(candidate, (str, int, float)):
                    detail = str(candidate)
                    break
        if not detail:
            detail = "provider returned an error response"
    detail = redact_text(detail, secret=secret)
    detail = " ".join(detail.split())
    return detail[:500] + ("..." if len(detail) > 500 else "")


def _read_limited(stream: Any, limit: int) -> bytes:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise APIError(None, f"TypeSafe response exceeded the {limit}-byte limit")
    return raw


def _header_value(headers: Any, name: str) -> str | None:
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return str(value) if value is not None else None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, retry_at.timestamp() - datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_after_from_headers(headers: Any) -> float | None:
    """Read both standard Retry-After and TypeSafe's millisecond hint."""

    standard = _parse_retry_after(_header_value(headers, "Retry-After"))
    if standard is not None:
        return standard
    milliseconds = _header_value(headers, "retry-after-ms")
    if not milliseconds:
        return None
    try:
        value = float(milliseconds.strip()) / 1000.0
    except ValueError:
        return None
    return max(0.0, value) if math.isfinite(value) else None


def _retry_delay(settings: Settings, attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(settings.max_backoff_seconds, retry_after)
    base = min(
        settings.max_backoff_seconds,
        settings.retry_backoff_seconds * (2**attempt),
    )
    if not base or not settings.retry_jitter:
        return base
    return base * (1.0 - random.random() * settings.retry_jitter)


def _response_request_id(response_or_error: Any) -> str | None:
    headers = getattr(response_or_error, "headers", None)
    value = _header_value(headers, "x-typesafe-request-id")
    if value and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        return value
    return None


def post_to_typesafe(
    payload: JsonObject,
    *,
    settings: Settings | None = None,
    sleep: Sleep = time.sleep,
) -> JsonObject:
    """POST one validated request to TypeSafe with bounded retries."""

    settings = settings or Settings.from_env(require_key=True)
    normalized = validate_request(payload, settings)
    body = _json_bytes(normalized, context="request")
    content_type = "application/json"
    image_request = "assets" in normalized
    if image_request:
        try:
            files = images.validate_assets(normalized["assets"], {
                key: value for key, value in normalized.items() if key not in {"assets", "allow_images"}
            })
            body, content_type = images.multipart(normalized, files)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        if len(body) > settings.max_request_bytes:
            raise BridgeError(f"multipart request exceeds the {settings.max_request_bytes}-byte limit")
    if not settings.api_key:
        raise ConfigError("TYPESAFE_API_KEY is not set; set it in the MCP server environment")

    url = f"{settings.base_url}/v1/systemone"
    last_error: APIError | None = None
    max_retries = 0 if image_request else settings.max_retries
    for attempt in range(max_retries + 1):
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": content_type,
                "Accept": "application/json",
                "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
                **({images.IMAGE_OPT_IN_HEADER: "true"} if image_request else {}),
            },
        )
        try:
            with urlopen(request, timeout=settings.timeout_seconds) as response:
                raw = _read_limited(response, settings.max_response_bytes)
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise APIError(
                        None,
                        "TypeSafe returned invalid JSON",
                        request_id=_response_request_id(response),
                    ) from exc
                return validate_api_response(decoded, normalized["questions"])
        except HTTPError as exc:
            try:
                raw = exc.read(settings.max_response_bytes + 1)
            except OSError:
                raw = b""
            if len(raw) > settings.max_response_bytes:
                detail = f"error response exceeded the {settings.max_response_bytes}-byte limit"
            else:
                detail = _safe_error_detail(raw, secret=settings.api_key)
            last_error = APIError(
                exc.code,
                detail,
                request_id=_response_request_id(exc),
                retry_after=_retry_after_from_headers(getattr(exc, "headers", None)),
            )
            if exc.code not in RETRYABLE_STATUSES or attempt >= max_retries:
                raise last_error from exc
        except (URLError, TimeoutError, socket.timeout, HTTPException, OSError) as exc:
            last_error = APIError(None, f"request failed: {type(exc).__name__}")
            if attempt >= max_retries:
                raise last_error from exc

        assert last_error is not None
        sleep(_retry_delay(settings, attempt, last_error.retry_after))

    raise last_error or APIError(None, "request failed after retries")


class TypeSafeClient:
    """Small reusable client for applications that want the library directly."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env(require_key=True)

    def evaluate(self, request: JsonObject) -> JsonObject:
        return post_to_typesafe(request, settings=self.settings)


__all__ = [
    "APIError",
    "BridgeError",
    "ConfigError",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "MAX_INPUT_LINE_BYTES",
    "SERVER_NAME",
    "SERVER_VERSION",
    "Settings",
    "TypeSafeClient",
    "post_to_typesafe",
    "redact_text",
    "validate_api_response",
    "validate_request",
]
