from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import settings


def _ledger_dir() -> Path:
    base = os.environ.get("LEDGER_DIR") or settings.get_str("LEDGER_DIR", "run/ledger")
    return Path(base)


def _ledger_enabled() -> bool:
    raw = os.environ.get("LEDGER_ENABLED") or settings.get_str("LEDGER_ENABLED", "1")
    return str(raw).strip() != "0"


def _ledger_file_for_date(date_str: str) -> Path:
    return _ledger_dir() / f"ledger_{date_str}.jsonl"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def hash_json(obj: Dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return _hash_str(payload)


def _hash_str(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_event(
    *,
    event_type: str,
    symbol: str,
    timeframe: str,
    correlation_id: str,
    payload_hash: Optional[str] = None,
    decision_hash: Optional[str] = None,
    trade_plan_hash: Optional[str] = None,
    client_order_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if not _ledger_enabled():
        return
    ts = datetime.now(timezone.utc)
    record = {
        "event_type": event_type,
        "ts_utc": ts.isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "correlation_id": correlation_id,
        "payload_hash": payload_hash,
        "decision_hash": decision_hash,
        "trade_plan_hash": trade_plan_hash,
        "client_order_id": client_order_id,
        "details": details or {},
    }
    out = _ledger_file_for_date(ts.date().isoformat())
    _ensure_dir(out)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
