"""
JSON Schema validation for payload, decision, and trade_plan contracts.
Fail-closed: validation failures must not crash the runtime loop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger(__name__)

try:
    import jsonschema
    from jsonschema import validate, ValidationError
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    jsonschema = None
    validate = None
    ValidationError = Exception
    _JSONSCHEMA_AVAILABLE = False
    logger.error(
        "Missing dependency: jsonschema. Install with: "
        r".\.venv\Scripts\python.exe -m pip install jsonschema"
    )


_SCHEMA_DIR = Path(__file__).parent / "schemas"


def _load_schema(schema_name: str) -> Optional[Dict[str, Any]]:
    """Load JSON schema from file."""
    if not _JSONSCHEMA_AVAILABLE:
        return None
    schema_path = _SCHEMA_DIR / f"{schema_name}.schema.json"
    if not schema_path.exists():
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def validate_json(schema_path: str, obj: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate a JSON object against a schema.
    
    Args:
        schema_path: Path to schema file (e.g., "payload", "decision", "trade_plan")
        obj: Object to validate
    
    Returns:
        (ok: bool, errors: List[str])
        - If ok is True, errors is empty
        - If ok is False, errors contains validation error messages
    """
    if not _JSONSCHEMA_AVAILABLE:
        # Fail-closed if jsonschema is not installed
        return False, ["jsonschema_not_installed"]
    
    schema = _load_schema(schema_path)
    if schema is None:
        # Schema file not found - fail closed for production
        return False, [f"schema_not_found: {schema_path}"]
    
    try:
        validate(instance=obj, schema=schema)
        return True, []
    except ValidationError as e:
        errors = [f"validation_error: {e.message}"]
        if e.path:
            errors.append(f"path: {list(e.path)}")
        return False, errors
    except Exception as e:
        return False, [f"validation_exception: {str(e)}"]


def validate_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate payload.json against schema."""
    return validate_json("payload", payload)


def validate_decision(decision: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate decision.json against schema."""
    return validate_json("decision", decision)


def validate_trade_plan(trade_plan: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate trade_plan.json against schema."""
    return validate_json("trade_plan", trade_plan)
