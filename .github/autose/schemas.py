"""JSON schemas for agent outputs (architecture/views/contracts.md)."""

import json
from pathlib import Path

import jsonschema

_STR = {"type": "string", "minLength": 1}

SPEC = {
    "type": "object",
    "required": ["issue", "summary", "acceptance_criteria", "assumptions", "out_of_scope"],
    "additionalProperties": False,
    "properties": {
        "issue": {"type": "integer"},
        "summary": _STR,
        "acceptance_criteria": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object", "required": ["id", "text", "verify_by"], "additionalProperties": False,
            "properties": {"id": {"type": "string", "pattern": r"^AC-[1-9][0-9]*$"}, "text": _STR, "verify_by": _STR}}},
        "assumptions": {"type": "array", "items": _STR},
        "out_of_scope": {"type": "array", "items": _STR},
    },
}

REJECT = {
    "type": "object", "required": ["reason", "questions"], "additionalProperties": False,
    "properties": {"reason": _STR, "questions": {"type": "array", "minItems": 1, "maxItems": 5, "items": _STR}},
}

JUDGE = {
    "type": "array", "minItems": 1,
    "items": {"type": "object", "required": ["id", "testable", "reason"], "additionalProperties": False,
              "properties": {"id": _STR, "testable": {"type": "boolean"}, "reason": {"type": "string"}}},
}

DESIGN = {
    "type": "object", "required": ["approach", "targets"], "additionalProperties": False,
    "properties": {
        "approach": _STR,
        "targets": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
            "type": "object", "required": ["file", "symbol", "kind", "signature", "status"],
            "additionalProperties": False,
            "properties": {"file": _STR, "symbol": _STR,
                           "kind": {"enum": ["function", "class", "method", "attribute", "module"]},
                           "signature": {"type": "string"}, "status": {"enum": ["EXISTING", "NEW"]}}}},
    },
}

_EVIDENCE = {"oneOf": [
    {"type": "object", "required": ["file", "line"], "additionalProperties": False,
     "properties": {"file": _STR, "line": {"type": "integer", "minimum": 1}}},
    {"type": "object", "required": ["command", "excerpt"], "additionalProperties": False,
     "properties": {"command": _STR, "excerpt": {"type": "string"}}},
]}

REVIEW = {
    "type": "object", "required": ["verdict", "approval_evidence", "findings"], "additionalProperties": False,
    "properties": {
        "verdict": {"enum": ["approve", "changes_requested"]},
        "approval_evidence": {"type": "array", "items": _EVIDENCE},
        "findings": {"type": "array", "items": {
            "type": "object", "required": ["id", "classification", "severity", "evidence", "explanation"],
            "additionalProperties": False,
            "properties": {"id": _STR, "classification": {"enum": ["spec", "design", "code", "test"]},
                           "severity": {"enum": ["minor", "major", "blocker"]}, "evidence": _EVIDENCE,
                           "explanation": _STR, "drift_reference": {"type": "string"}}}},
    },
}


def load_and_validate(path: Path, schema: dict) -> tuple[object | None, str]:
    """Return (data, "") if valid, else (None, error message)."""
    if path.is_symlink():
        return None, f"{path.name} is a symlink; refusing to read it"
    if not path.exists():
        return None, f"{path.name} was not written"
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return None, f"{path.name} is not valid JSON: {e}"
    problems = explain(data, schema)
    return (data, "") if not problems else (None, f"{path.name}: " + "; ".join(problems))


def explain(data, schema: dict, limit: int = 6) -> list[str]:
    """Schema errors as short `at <path>: <problem>` lines an agent can act on. For a oneOf (evidence), the
    error of the closest alternative is shown instead of 'not valid under any of the given schemas'."""
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))[:limit]:
        if err.validator == "oneOf" and err.context:
            err = jsonschema.exceptions.best_match(err.context)
        where = "/".join(str(p) for p in err.absolute_path) or "(top level)"
        out.append(f"at {where}: {err.message[:300]}")
    return out
