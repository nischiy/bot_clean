from __future__ import annotations

from typing import Any, Dict

_BUY = {"BUY", "LONG"}
_SELL = {"SELL", "SHORT"}
_HOLD = {"HOLD", "NONE"}

def normalize_side(value: Any) -> str:
    if value is None:
        return "HOLD"
    text = str(value).strip().upper()
    if text in _BUY:
        return "BUY"
    if text in _SELL:
        return "SELL"
    if text in _HOLD:
        return "HOLD"
    return "UNKNOWN"

def normalize_decision(decision: Any, *, fallback_reason: str = "signal_empty") -> Dict[str, Any]:
    """
    Normalize a decision dict to the minimal contract used by runner/services.
    - Ensures decision is a dict
    - Normalizes side/action
    - Ensures reason/reasons are present
    """
    if not isinstance(decision, dict):
        return {"side": "HOLD", "action": "HOLD", "reason": fallback_reason, "reasons": [fallback_reason]}

    out = dict(decision)
    side_raw = out.get("side") if out.get("side") is not None else out.get("action")
    side = normalize_side(side_raw)
    out["side"] = side if side != "UNKNOWN" else "HOLD"
    out["action"] = out.get("action") or out.get("side")

    if not out.get("reason"):
        out["reason"] = fallback_reason
    if not out.get("reasons"):
        out["reasons"] = [out.get("reason")]
    return out
