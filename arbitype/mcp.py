"""Arbitype's dependency-free MCP STDIO server and typed decision tools."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any

from .core import (
    BridgeError,
    ConfigError,
    MAX_INPUT_LINE_BYTES,
    SERVER_NAME,
    SERVER_VERSION,
    Settings,
    TypeSafeClient,
    validate_request,
)
from .images import MAX_ASSETS, MAX_BASE64_CHARS


STRUCTURED_VALUE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"},
    ]
}
ENTRY_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"},
        {"type": "null"},
    ]
}
OPTION_DESCRIPTION_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"},
        {"type": "null"},
    ]
}
CRITERION_SCHEMA: dict[str, Any] = {
    **ENTRY_SCHEMA,
}
QUESTION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "type": {"const": "box"},
                "instructions": STRUCTURED_VALUE_SCHEMA,
            },
            "required": ["type", "instructions"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"const": "noul"},
                "instructions": ENTRY_SCHEMA,
                "criteria": {
                    "type": "object",
                    "properties": {"true": CRITERION_SCHEMA, "false": CRITERION_SCHEMA},
                    "additionalProperties": False,
                },
            },
            "required": ["type", "instructions"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"const": "choice"},
                "instructions": ENTRY_SCHEMA,
                "criteria": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 255,
                    "additionalProperties": OPTION_DESCRIPTION_SCHEMA,
                },
            },
            "required": ["type", "instructions", "criteria"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"const": "score"},
                "instructions": ENTRY_SCHEMA,
                "criteria": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 10,
                    "items": STRUCTURED_VALUE_SCHEMA,
                },
            },
            "required": ["type", "instructions", "criteria"],
            "additionalProperties": False,
        },
    ]
}

SERVER_INSTRUCTIONS = (
    "Arbitype provides read-only typed decision tools powered by TypeSafe Jev. "
    "Tool selection: classify=one closed label; score=ordered rubric; check=one proposition; "
    "verify=multiple claims; gate=thresholded checks; review=whole-object quality/risk; "
    "route=one next action; evaluate=raw questions; health=local configuration. "
    "USE these for bounded signals. DO NOT treat probabilities as proof, authorization, or security. "
    "Tools never edit files, run commands, approve changes, or replace tests/review."
)

# The current MCP specification (2026-07-28) introduced a per-request metadata
# era. This server remains deliberately dual-purpose: it serves modern
# per-request metadata clients and retains the initialize handshake used by
# Codex and other established STDIO hosts.
SUPPORTED_LEGACY_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
    "2024-10-07",
)
MODERN_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION, *SUPPORTED_LEGACY_PROTOCOL_VERSIONS)
LATEST_LEGACY_PROTOCOL_VERSION = SUPPORTED_LEGACY_PROTOCOL_VERSIONS[0]
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"

USAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "input_tokens": {"type": "integer", "minimum": 0},
        "output_tokens": {"type": "integer", "minimum": 0},
    },
    "required": ["input_tokens", "output_tokens"],
    "additionalProperties": True,
}
EVALUATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {"type": "string"},
        "answers": {"type": "object"},
        "usage": USAGE_SCHEMA,
    },
    "required": ["model", "answers", "usage"],
    "additionalProperties": True,
}


def _single_output_schema(result_type: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": result_type},
            "answer": {"type": "object"},
            "model": {"type": "string"},
            "usage": USAGE_SCHEMA,
            "evaluation": EVALUATION_OUTPUT_SCHEMA,
        },
        "required": ["type", "answer", "model", "usage", "evaluation"],
        "additionalProperties": True,
    }


def _gate_output_schema(result_type: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": result_type},
            "decision": {"enum": ["pass", "review", "fail"]},
            "policy": {
                "type": "object",
                "properties": {
                    "pass_at": {"type": "number", "minimum": 0, "maximum": 1},
                    "review_at": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["pass_at", "review_at"],
                "additionalProperties": False,
            },
            "checks": {"type": "object"},
            "evaluation": EVALUATION_OUTPUT_SCHEMA,
        },
        "required": ["type", "decision", "policy", "checks", "evaluation"],
        "additionalProperties": True,
    }


ROUTE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"const": "route"},
        "route": {"type": "string"},
        "answer": {"type": "object"},
        "model": {"type": "string"},
        "usage": USAGE_SCHEMA,
        "evaluation": EVALUATION_OUTPUT_SCHEMA,
    },
    "required": ["type", "route", "answer", "model", "usage", "evaluation"],
    "additionalProperties": True,
}
VERIFICATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"const": "verification"},
        "evaluation": EVALUATION_OUTPUT_SCHEMA,
        "answers": {"type": "object"},
    },
    "required": ["type", "evaluation", "answers"],
    "additionalProperties": True,
}
HEALTH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"const": "health"},
        "server": {"type": "string"},
        "version": {"type": "string"},
        "api_key_configured": {"type": "boolean"},
        "live": {"type": "boolean"},
        "status": {"type": "string"},
        "base_url": {"type": "string"},
        "model": {"type": "string"},
        "timeout_seconds": {"type": "number"},
        "max_retries": {"type": "integer", "minimum": 0},
    },
    "required": ["type", "server", "version", "api_key_configured", "live"],
    "additionalProperties": True,
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "annotations": {
            "title": f"TypeSafe {name.replace('_', ' ').title()}",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        "outputSchema": output_schema,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool(
        "evaluate",
        "USE WHEN you need raw typed Jev answers for bounded noul, choice, score, or box "
        "questions, optionally with experimental inline PNG/JPEG assets and allow_images:true. "
        "Box requires assets and opt-in; pair it with a separate presence Noul. "
        "DO NOT USE WHEN you need prose, code generation, arithmetic, dates, action authorization, or a "
        "specialized primitive such as route or gate.",
        {
            "state": {**STRUCTURED_VALUE_SCHEMA, "description": "Text or structured evidence."},
            "questions": {
                "type": "object",
                "description": "Map of question id to a TypeSafe question.",
                "minProperties": 1,
                "maxProperties": 64,
                "additionalProperties": QUESTION_SCHEMA,
            },
            "model": {"type": "string", "description": "Optional model id or alias."},
            "allow_images": {"type": "boolean", "description": "Explicit per-request image opt-in; no automatic image retries."},
            "assets": {
                "type": "object",
                "description": "1-4 inline PNG/JPEG images, 256 KiB decoded total; reference keys must occur in string values of state/instructions/criteria. Sides 280-1568 px; expansion <=32 MiB. PNG: noninterlaced 8-bit, no metadata. JPEG: baseline single scan, structural checks only. Existing request limits also apply.",
                "minProperties": 1,
                "maxProperties": MAX_ASSETS,
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "type": {"const": "image"},
                        "format": {"enum": ["png", "jpg", "jpeg"]},
                        "data_base64": {"type": "string", "minLength": 1, "maxLength": MAX_BASE64_CHARS},
                    },
                    "required": ["format", "data_base64"],
                    "additionalProperties": False,
                },
            },
        },
        ["state", "questions"],
        output_schema=EVALUATION_OUTPUT_SCHEMA,
    ),
    _tool(
        "classify",
        "USE WHEN you need exactly one label from a closed, unordered set and its probability "
        "distribution. DO NOT USE WHEN labels are ordered levels (use score), options are next "
        "actions (use route), or inputs are claims (use verify).",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "instructions": ENTRY_SCHEMA,
            "labels": {
                "type": "object",
                "minProperties": 1,
                "maxProperties": 255,
                "additionalProperties": OPTION_DESCRIPTION_SCHEMA,
            },
            "model": {"type": "string"},
        },
        ["state", "instructions", "labels"],
        output_schema=_single_output_schema("classification"),
    ),
    _tool(
        "score",
        "USE WHEN you need a weighted rating over two or more ordered rubric levels. DO NOT USE "
        "WHEN choices are unordered categories (use classify), next actions (use route), or "
        "thresholded checks (use gate).",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "instructions": ENTRY_SCHEMA,
            "levels": {
                "type": "array",
                "minItems": 2,
                "maxItems": 10,
                "items": STRUCTURED_VALUE_SCHEMA,
            },
            "model": {"type": "string"},
        },
        ["state", "instructions", "levels"],
        output_schema=_single_output_schema("score"),
    ),
    _tool(
        "check",
        "USE WHEN you need one bounded yes/no proposition and its probability. DO NOT USE WHEN "
        "you need to verify multiple named claims (use verify), combine checks with thresholds "
        "(use gate), or assess an entire object or review package (use review).",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "instructions": ENTRY_SCHEMA,
            "true_criteria": CRITERION_SCHEMA,
            "false_criteria": CRITERION_SCHEMA,
            "model": {"type": "string"},
        },
        ["state", "instructions"],
        output_schema=_single_output_schema("check"),
    ),
    _tool(
        "verify",
        "USE WHEN you need to check multiple named claims against supplied evidence in one request. "
        "DO NOT USE WHEN you need one proposition (use check), an overall quality/risk review "
        "(use review), or a pass/review/fail threshold (use gate). Results are signals, not proof.",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "claims": {
                "type": "object",
                "minProperties": 1,
                "maxProperties": 64,
                "additionalProperties": ENTRY_SCHEMA,
            },
            "true_criteria": CRITERION_SCHEMA,
            "false_criteria": CRITERION_SCHEMA,
            "model": {"type": "string"},
        },
        ["state", "claims"],
        output_schema=VERIFICATION_OUTPUT_SCHEMA,
    ),
    _tool(
        "gate",
        "USE WHEN you need to combine multiple bounded checks and thresholds into pass/review/fail. "
        "DO NOT USE WHEN you need claim-by-claim evidence checking (use verify) or whole-object "
        "quality/risk assessment (use review). This is not authorization or a security boundary.",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "checks": {
                "type": "object",
                "minProperties": 1,
                "maxProperties": 64,
                "additionalProperties": ENTRY_SCHEMA,
            },
            "pass_at": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.85},
            "review_at": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.60},
            "model": {"type": "string"},
        },
        ["state", "checks"],
        output_schema=_gate_output_schema("gate"),
    ),
    _tool(
        "route",
        "USE WHEN you need exactly one next action from a closed set of actions. DO NOT USE WHEN "
        "you need a topic/category label (use classify), an ordered rating (use score), or action "
        "execution. This suggests an action; it never executes it.",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "instructions": ENTRY_SCHEMA,
            "actions": {
                "type": "object",
                "minProperties": 2,
                "maxProperties": 32,
                "additionalProperties": OPTION_DESCRIPTION_SCHEMA,
            },
            "model": {"type": "string"},
        },
        ["state", "actions"],
        output_schema=ROUTE_OUTPUT_SCHEMA,
    ),
    _tool(
        "review",
        "USE WHEN you need an overall quality or risk review of a whole-object artifact such as a diff, plan, release, or test "
        "report against multiple checks. DO NOT USE WHEN you need claim-by-claim verification "
        "(use verify), a single proposition (use check), or next-action selection (use route). "
        "It returns signals and never edits files.",
        {
            "state": STRUCTURED_VALUE_SCHEMA,
            "checks": {
                "type": "object",
                "minProperties": 1,
                "maxProperties": 64,
                "additionalProperties": ENTRY_SCHEMA,
            },
            "pass_at": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.85},
            "review_at": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.60},
            "model": {"type": "string"},
        },
        ["state", "checks"],
        output_schema=_gate_output_schema("review"),
    ),
    _tool(
        "health",
        "USE WHEN you need local Arbitype configuration or an explicitly requested live provider "
        "check. DO NOT USE WHEN you need a business decision; health does not evaluate state and "
        "does not make a network request unless live=true is explicit.",
        {
            "live": {"type": "boolean", "default": False},
        },
        [],
        output_schema=HEALTH_OUTPUT_SCHEMA,
    ),
]
TOOL_MAP = {tool["name"]: tool for tool in TOOLS}
# These names were published by versions before the project became
# host-neutral. Keep accepting them for callers that have not migrated yet,
# but do not advertise them in tools/list.
TOOL_ALIASES = {"codex_route": "route", "codex_review": "review"}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _require_object(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise BridgeError("arguments must be a JSON object")
    return arguments


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise BridgeError(f"unsupported argument(s): {', '.join(map(str, unknown))}")


def _is_json_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _matches_json_schema(value: Any, schema: dict[str, Any]) -> bool:
    """Match the bounded JSON Schema subset used by the advertised tools.

    The server deliberately keeps a standard-library-only runtime. This small
    validator covers the fixed schemas we publish in ``tools/list`` so input
    validation cannot drift from the advertised contract without a network
    request or an optional third-party dependency.
    """

    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False

    if "oneOf" in schema:
        if sum(_matches_json_schema(value, branch) for branch in schema["oneOf"]) != 1:
            return False
    if "anyOf" in schema:
        if not any(_matches_json_schema(value, branch) for branch in schema["anyOf"]):
            return False
    if "allOf" in schema:
        if not all(_matches_json_schema(value, branch) for branch in schema["allOf"]):
            return False

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(
            (expected == "null" and value is None)
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "string" and isinstance(value, str))
            or (expected == "object" and isinstance(value, dict))
            or (expected == "array" and isinstance(value, list))
            or (expected == "number" and _is_json_number(value))
            or (
                expected == "integer"
                and isinstance(value, int)
                and not isinstance(value, bool)
            )
            for expected in expected_types
        ):
            return False

    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            return False
        if len(value) < schema.get("minProperties", 0):
            return False
        max_properties = schema.get("maxProperties")
        if max_properties is not None and len(value) > max_properties:
            return False

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(name not in value for name in required):
            return False
        if any(
            name in value and not _matches_json_schema(value[name], property_schema)
            for name, property_schema in properties.items()
        ):
            return False

        additional = schema.get("additionalProperties", True)
        extra_names = set(value) - set(properties)
        if extra_names and additional is False:
            return False
        if isinstance(additional, dict) and any(
            not _matches_json_schema(value[name], additional) for name in extra_names
        ):
            return False

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return False
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and any(
            not _matches_json_schema(item, item_schema) for item in value
        ):
            return False

    if _is_json_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False

    return True


def _validate_tool_arguments(name: str, arguments: Any) -> None:
    schema = TOOL_MAP[name]["inputSchema"]
    if not _matches_json_schema(arguments, schema):
        raise BridgeError(f"{name} arguments do not match the advertised input schema")


def _require_state(arguments: dict[str, Any]) -> Any:
    if "state" not in arguments:
        raise BridgeError("missing required argument: state")
    return arguments["state"]


def _require_structured(arguments: dict[str, Any], name: str, *, allow_null: bool = False) -> Any:
    if name not in arguments:
        raise BridgeError(f"missing required argument: {name}")
    value = arguments[name]
    if not isinstance(value, (str, dict, list)) and not (allow_null and value is None):
        suffix = ", or null" if allow_null else ""
        raise BridgeError(f"{name} must be a string, object, or array{suffix}")
    return value


def _optional_model(arguments: dict[str, Any]) -> str | None:
    model = arguments.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise BridgeError("model must be a non-empty string when provided")
    return model


def _one_answer(response: dict[str, Any], question_id: str) -> dict[str, Any]:
    answers = response.get("answers")
    if not isinstance(answers, dict) or question_id not in answers:
        raise BridgeError(f"TypeSafe response is missing answer {question_id!r}")
    return {
        "answer": answers[question_id],
        "model": response.get("model"),
        "usage": response.get("usage"),
        "evaluation": response,
    }


def _run_single(
    client: TypeSafeClient,
    *,
    state: Any,
    question_id: str,
    question: dict[str, Any],
    model: str | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"state": state, "questions": {question_id: question}}
    if model is not None:
        request["model"] = model
    response = client.evaluate(request)
    return _one_answer(response, question_id)


def _call_evaluate(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    payload = validate_request(args, client.settings)
    return client.evaluate(payload)


def _call_classify(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "instructions", "labels", "model"})
    model = _optional_model(args)
    labels = args.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise BridgeError("labels must be a non-empty object")
    result = _run_single(
        client,
        state=_require_state(args),
        question_id="classification",
        question={
            "type": "choice",
            "instructions": _require_structured(args, "instructions", allow_null=True),
            "criteria": labels,
        },
        model=model,
    )
    return {"type": "classification", **result}


def _call_route(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "instructions", "actions", "model"})
    actions = args.get("actions")
    if not isinstance(actions, dict) or not 2 <= len(actions) <= 32:
        raise BridgeError("actions must be an object with between 2 and 32 options")
    instructions = args.get(
        "instructions",
        "Which next action should the agent take based on the supplied state? Choose only one action.",
    )
    if not _is_structured_argument(instructions, allow_null=True):
        raise BridgeError("instructions must be a string, object, array, or null")
    model = _optional_model(args)
    result = _run_single(
        client,
        state=_require_state(args),
        question_id="next_action",
        question={"type": "choice", "instructions": instructions, "criteria": actions},
        model=model,
    )
    answer = result["answer"]
    return {"type": "route", "route": answer["choice"], **result}


def _call_score(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "instructions", "levels", "model"})
    model = _optional_model(args)
    levels = args.get("levels")
    if not isinstance(levels, list) or not levels:
        raise BridgeError("levels must be a non-empty array")
    result = _run_single(
        client,
        state=_require_state(args),
        question_id="score",
        question={
            "type": "score",
            "instructions": _require_structured(args, "instructions", allow_null=True),
            "criteria": levels,
        },
        model=model,
    )
    return {"type": "score", **result}


def _call_check(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "instructions", "true_criteria", "false_criteria", "model"})
    model = _optional_model(args)
    criteria: dict[str, Any] = {}
    if "true_criteria" in args:
        criteria["true"] = args["true_criteria"]
    if "false_criteria" in args:
        criteria["false"] = args["false_criteria"]
    question: dict[str, Any] = {
        "type": "noul",
        "instructions": _require_structured(args, "instructions", allow_null=True),
    }
    if criteria:
        question["criteria"] = criteria
    result = _run_single(
        client,
        state=_require_state(args),
        question_id="check",
        question=question,
        model=model,
    )
    return {"type": "check", **result}


def _claims_question(
    claim: Any,
    true_criteria: Any,
    false_criteria: Any,
) -> dict[str, Any]:
    question: dict[str, Any] = {
        "type": "noul",
        "instructions": {
            "claim": claim,
            "question": "Does the supplied evidence support this claim?",
        },
    }
    criteria: dict[str, Any] = {}
    if true_criteria is not None:
        criteria["true"] = true_criteria
    if false_criteria is not None:
        criteria["false"] = false_criteria
    if criteria:
        question["criteria"] = criteria
    return question


def _call_verify(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "claims", "true_criteria", "false_criteria", "model"})
    claims = args.get("claims")
    if not isinstance(claims, dict) or not claims:
        raise BridgeError("claims must be a non-empty object")
    true_criteria = args.get("true_criteria")
    false_criteria = args.get("false_criteria")
    questions = {
        claim_id: _claims_question(claim, true_criteria, false_criteria)
        for claim_id, claim in claims.items()
    }
    request: dict[str, Any] = {"state": _require_state(args), "questions": questions}
    model = _optional_model(args)
    if model is not None:
        request["model"] = model
    response = client.evaluate(request)
    return {"type": "verification", "evaluation": response, "answers": response["answers"]}


def _threshold(arguments: dict[str, Any], name: str, default: float) -> float:
    value = arguments.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise BridgeError(f"{name} must be a number between 0 and 1")
    return float(value)


def _call_gate(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"state", "checks", "pass_at", "review_at", "model"})
    checks = args.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise BridgeError("checks must be a non-empty object")
    pass_at = _threshold(args, "pass_at", 0.85)
    review_at = _threshold(args, "review_at", 0.60)
    if review_at > pass_at:
        raise BridgeError("review_at must not be greater than pass_at")

    questions = {
        check_id: {
            "type": "noul",
            "instructions": {
                "check": check,
                "question": "Does this check pass based on the supplied state?",
            },
        }
        for check_id, check in checks.items()
    }
    request: dict[str, Any] = {"state": _require_state(args), "questions": questions}
    model = _optional_model(args)
    if model is not None:
        request["model"] = model
    response = client.evaluate(request)

    normalized_checks: dict[str, Any] = {}
    decision = "pass"
    for check_id in checks:
        answer = response["answers"][check_id]
        probability = answer["noul"]
        if probability < review_at:
            check_decision = "fail"
        elif probability < pass_at:
            check_decision = "review"
        else:
            check_decision = "pass"
        if check_decision == "fail":
            decision = "fail"
        elif check_decision == "review" and decision == "pass":
            decision = "review"
        normalized_checks[check_id] = {
            "decision": check_decision,
            "probability": probability,
        }

    return {
        "type": "gate",
        "decision": decision,
        "policy": {"pass_at": pass_at, "review_at": review_at},
        "checks": normalized_checks,
        "evaluation": response,
    }


def _call_review(arguments: Any, client: TypeSafeClient) -> dict[str, Any]:
    result = _call_gate(arguments, client)
    result["type"] = "review"
    return result


def _is_structured_argument(value: Any, *, allow_null: bool = False) -> bool:
    return isinstance(value, (str, dict, list)) or (allow_null and value is None)


def _call_health(arguments: Any) -> dict[str, Any]:
    args = _require_object(arguments)
    _reject_unknown(args, {"live"})
    live = args.get("live", False)
    if not isinstance(live, bool):
        raise BridgeError("live must be a boolean")

    checks: dict[str, Any] = {
        "type": "health",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "api_key_configured": bool(os.getenv("TYPESAFE_API_KEY", "").strip()),
        "live": False,
    }
    try:
        settings = Settings.from_env(require_key=False)
    except ConfigError as exc:
        checks.update({"status": "configuration_error", "message": str(exc)})
        return checks

    checks.update(
        {
            "status": "ok" if checks["api_key_configured"] else "missing_api_key",
            "base_url": settings.base_url,
            "model": settings.model,
            "timeout_seconds": settings.timeout_seconds,
            "max_retries": settings.max_retries,
        }
    )
    if not live:
        return checks
    if not checks["api_key_configured"]:
        return checks

    started = time.monotonic()
    TypeSafeClient(settings).evaluate(
        {
            "state": "MCP health check",
            "questions": {
                "ready": {"type": "noul", "instructions": "Is this health check request ready?"}
            },
        }
    )
    checks.update({"live": True, "latency_ms": round((time.monotonic() - started) * 1000, 1)})
    return checks


def call_tool(name: str, arguments: Any) -> dict[str, Any]:
    """Dispatch one MCP tool call."""

    canonical_name = TOOL_ALIASES.get(name, name)
    if canonical_name not in TOOL_MAP:
        raise BridgeError(f"unknown tool: {name}")
    _validate_tool_arguments(canonical_name, arguments)
    if canonical_name == "health":
        return _call_health(arguments)
    client = TypeSafeClient()
    dispatch = {
        "evaluate": _call_evaluate,
        "classify": _call_classify,
        "score": _call_score,
        "check": _call_check,
        "verify": _call_verify,
        "gate": _call_gate,
        "route": _call_route,
        "review": _call_review,
    }
    return dispatch[canonical_name](arguments, client)


def _result(request_id: Any, result: Any, *, modern: bool = False) -> dict[str, Any]:
    if modern and isinstance(result, dict) and "resultType" not in result:
        result = {"resultType": "complete", **result}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def _is_request_id(value: Any) -> bool:
    """Return whether a value is a safe JSON-RPC request id."""

    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, (str, int))


def _server_capabilities() -> dict[str, Any]:
    return {"tools": {"listChanged": False}}


def _discovery_result() -> dict[str, Any]:
    """Return the modern discovery shape for both protocol eras."""

    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": _server_capabilities(),
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            }
        },
        "instructions": SERVER_INSTRUCTIONS,
        "ttlMs": 300_000,
        "cacheScope": "public",
    }


def _request_protocol_version(params: dict[str, Any]) -> str | None:
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return None
    version = metadata.get(PROTOCOL_VERSION_META_KEY)
    return version if isinstance(version, str) else None


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Return ``None`` for notifications."""

    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "invalid JSON-RPC request")
    if "id" in message and not _is_request_id(message["id"]):
        return _rpc_error(None, -32600, "request id must be a string or integer")

    method = message.get("method")
    if not isinstance(method, str):
        return _rpc_error(message.get("id"), -32600, "method must be a string")
    request_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params", {})
    if not isinstance(params, dict):
        return None if is_notification else _rpc_error(request_id, -32602, "params must be an object")

    metadata_present = "_meta" in params
    metadata = params.get("_meta")
    if metadata_present and not isinstance(metadata, dict):
        return None if is_notification else _rpc_error(request_id, -32602, "_meta must be an object")
    if isinstance(metadata, dict):
        if PROTOCOL_VERSION_META_KEY in metadata and not isinstance(
            metadata[PROTOCOL_VERSION_META_KEY], str
        ):
            return None if is_notification else _rpc_error(
                request_id,
                -32602,
                f"{PROTOCOL_VERSION_META_KEY} must be a string",
            )
        if (
            CLIENT_CAPABILITIES_META_KEY in metadata
            and PROTOCOL_VERSION_META_KEY not in metadata
        ):
            return None if is_notification else _rpc_error(
                request_id,
                -32602,
                f"modern requests require {PROTOCOL_VERSION_META_KEY}",
            )

    requested_protocol_version = _request_protocol_version(params)
    if requested_protocol_version is not None and requested_protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return None if is_notification else _rpc_error(
            request_id,
            -32022,
            "Unsupported protocol version",
            {
                "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                "requested": requested_protocol_version,
            },
        )
    modern = requested_protocol_version == MODERN_PROTOCOL_VERSION
    if modern and (
        not isinstance(metadata, dict)
        or not isinstance(metadata.get(CLIENT_CAPABILITIES_META_KEY), dict)
    ):
        return None if is_notification else _rpc_error(
            request_id,
            -32602,
            f"modern requests require {CLIENT_CAPABILITIES_META_KEY}",
        )

    if method in {"notifications/initialized", "notifications/cancelled", "notifications/progress"}:
        return None
    if method == "ping":
        return None if is_notification else _result(request_id, {}, modern=modern)
    if method == "shutdown":
        return None if is_notification else _result(request_id, {}, modern=modern)
    if method == "server/discover":
        return None if is_notification else _result(request_id, _discovery_result(), modern=modern)
    if method == "initialize":
        requested = params.get("protocolVersion")
        protocol_version = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_LEGACY_PROTOCOL_VERSIONS
            else LATEST_LEGACY_PROTOCOL_VERSION
        )
        return None if is_notification else _result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": _server_capabilities(),
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
            modern=modern,
        )
    if method == "tools/list":
        result: dict[str, Any] = {"tools": TOOLS}
        if modern:
            result.update({"ttlMs": 300_000, "cacheScope": "public"})
        return None if is_notification else _result(request_id, result, modern=modern)
    if method == "resources/list":
        result = {"resources": []}
        if modern:
            result.update({"ttlMs": 300_000, "cacheScope": "public"})
        return None if is_notification else _result(request_id, result, modern=modern)
    if method == "prompts/list":
        result = {"prompts": []}
        if modern:
            result.update({"ttlMs": 300_000, "cacheScope": "public"})
        return None if is_notification else _result(request_id, result, modern=modern)
    if method == "logging/setLevel":
        return None if is_notification else _result(request_id, {}, modern=modern)
    if method == "tools/call":
        if is_notification:
            return None
        name = params.get("name")
        if not isinstance(name, str) or (name not in TOOL_MAP and name not in TOOL_ALIASES):
            return _rpc_error(request_id, -32602, "unknown tool")
        try:
            result = call_tool(name, params.get("arguments", {}))
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": _json_text(result)}],
                    "structuredContent": result,
                },
                modern=modern,
            )
        except BridgeError as exc:
            return _result(
                request_id,
                {"isError": True, "content": [{"type": "text", "text": str(exc)}]},
                modern=modern,
            )
        except Exception:
            return _result(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": "internal tool error"}],
                },
                modern=modern,
            )

    return None if is_notification else _rpc_error(request_id, -32601, f"method not found: {method}")


def main_stdio() -> int:
    """Run the line-delimited JSON-RPC stdio transport."""

    def discard_remaining_line(stream: Any) -> None:
        while True:
            chunk = stream.readline(8192)
            if not chunk or b"\n" in chunk:
                return

    input_stream = sys.stdin.buffer
    while True:
        raw_line = input_stream.readline(MAX_INPUT_LINE_BYTES + 1)
        if not raw_line:
            break
        message: dict[str, Any] | None = None
        should_exit = False
        if len(raw_line) > MAX_INPUT_LINE_BYTES:
            if b"\n" not in raw_line:
                discard_remaining_line(input_stream)
            response = _rpc_error(None, -32600, "input message is too large")
        else:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded_line = line.decode("utf-8")
            except UnicodeError as exc:
                response = _rpc_error(None, -32700, f"invalid UTF-8 JSON-RPC message: {exc}")
            else:
                try:
                    message = json.loads(decoded_line)
                except (RecursionError, ValueError) as exc:
                    response = _rpc_error(None, -32700, f"invalid JSON-RPC message: {exc}")
                else:
                    if not isinstance(message, dict):
                        response = _rpc_error(None, -32600, "invalid JSON-RPC request")
                    else:
                        # MCP replies to ``shutdown`` first; the client then sends
                        # the ``exit`` notification. Exit only after that
                        # notification so the lifecycle remains compatible with
                        # strict clients.
                        should_exit = message.get("method") == "exit"
                        try:
                            response = handle_message(message)
                        except Exception:
                            # Never print tracebacks to stdout: a single malformed
                            # request must not corrupt the MCP stream or reveal
                            # local details.
                            response = _rpc_error(None, -32603, "internal server error")

        if response is not None:
            try:
                sys.stdout.write(_json_text(response) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                return 0
        if should_exit:
            return 0
    return 0


__all__ = [
    "SERVER_INSTRUCTIONS",
    "MODERN_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SUPPORTED_LEGACY_PROTOCOL_VERSIONS",
    "LATEST_LEGACY_PROTOCOL_VERSION",
    "TOOLS",
    "TOOL_MAP",
    "TOOL_ALIASES",
    "call_tool",
    "handle_message",
    "main_stdio",
]
