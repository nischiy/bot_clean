from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.config import settings

_log = logging.getLogger("core.telemetry.health")

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def log_health(*,
               ok: Optional[bool] = None,
               msg: Optional[str] = None,
               message: Optional[str] = None,
               level: str = "INFO",
               **fields: Any) -> None:
    """Log a health event (JSONL + std logger).
    Accepts 'ok' and either 'msg' or 'message' (backward compatible).
    """
    text = message if message is not None else msg
    payload = {
        "ts": _now_iso(),
        "ok": ok,
        "level": level,
        "message": text,
    }
    if fields:
        payload.update(fields)

    # File logging
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_dir = settings.get_str("LOG_DIR", "logs")
    out_dir = Path(log_dir) / "health" / day
    _ensure_dir(out_dir)
    out_file = out_dir / "health.jsonl"
    try:
        with out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:  # pragma: no cover
        _log.warning("Failed to write health file: %s", e)

    try:
        extra_str = ", ".join(f"{k}={v!r}" for k, v in fields.items())
        line = f"[HEALTH] ok={ok} | {text} | extra={{ {extra_str} }}"
        lvl = getattr(logging, level.upper(), logging.INFO)
        _log.log(lvl, line)
    except Exception:
        _log.info("HEALTH: %s", text)
