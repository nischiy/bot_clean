from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import settings


def _parse_structured_json(msg: str) -> Optional[Dict[str, Any]]:
    msg = msg.strip()
    if not msg.startswith("{") or not msg.endswith("}"):
        return None
    try:
        data = json.loads(msg)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class CleanSessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Tick #" in msg:
            return False
        if "tick_skip_agg" in msg:
            return False
        obj = _parse_structured_json(msg)
        if not isinstance(obj, dict):
            return True
        event = obj.get("event")
        if event == "decision_candle":
            return False
        if event == "tick_skip_agg":
            return False
        if event == "tick_summary" and obj.get("skip_reason") == "already_processed":
            return False
        return True


def ensure_runtime_logging(
    logger: logging.Logger,
    *,
    log_dir: Optional[str] = None,
    pytest_env: bool = False,
) -> Dict[str, Any]:
    level_name = str(settings.get_str("LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    runtime_path = None
    session_path = None
    if not pytest_env:
        log_dir = log_dir or settings.get_str("LOG_DIR", "logs")
        runtime_path = os.path.join(log_dir, "runtime.log")
        try:
            os.makedirs(log_dir, exist_ok=True)
            if not any(
                isinstance(h, logging.handlers.RotatingFileHandler)
                and os.path.basename(getattr(h, "baseFilename", "")) == "runtime.log"
                for h in logger.handlers
            ):
                rh = logging.handlers.RotatingFileHandler(
                    runtime_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
                )
                rh.setFormatter(fmt)
                logger.addHandler(rh)
            sessions_dir = os.path.join(log_dir, "sessions")
            sessions_clean_dir = os.path.join(log_dir, "sessions_clean")
            os.makedirs(sessions_dir, exist_ok=True)
            os.makedirs(sessions_clean_dir, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            session_path = os.path.join(sessions_dir, f"{ts}_{os.getpid()}.log")
            session_clean_path = os.path.join(sessions_clean_dir, f"{ts}_{os.getpid()}.log")
            if not any(
                isinstance(h, logging.FileHandler)
                and "sessions" in str(getattr(h, "baseFilename", ""))
                for h in logger.handlers
            ):
                sh = logging.FileHandler(session_path, encoding="utf-8")
                sh.setFormatter(fmt)
                logger.addHandler(sh)
            if not any(
                isinstance(h, logging.FileHandler)
                and "sessions_clean" in str(getattr(h, "baseFilename", ""))
                for h in logger.handlers
            ):
                ch = logging.FileHandler(session_clean_path, encoding="utf-8")
                ch.setFormatter(fmt)
                ch.addFilter(CleanSessionFilter())
                logger.addHandler(ch)
        except Exception:
            pass
    return {
        "log_dir": log_dir or settings.get_str("LOG_DIR", "logs"),
        "runtime_log": runtime_path,
        "session_log": session_path,
        "file_handler": any(isinstance(h, logging.FileHandler) for h in logger.handlers),
        "pytest_env": pytest_env,
    }
