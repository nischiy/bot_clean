from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from core.env_loader import load_env_files, discover_env_files, read_env_files

_DOTENV_STATUS: Optional[bool] = None

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def get_env(name: str, default: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    source = env if env is not None else os.environ
    return source.get(name, default)


def get_bool(name: str, default: bool = False, env: Optional[Mapping[str, str]] = None) -> bool:
    return parse_bool(get_env(name, None, env=env), default=default)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _running_under_pytest() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


def load_dotenv_once(*, override: bool = False) -> bool:
    global _DOTENV_STATUS
    if _running_under_pytest():
        _DOTENV_STATUS = False
        return _DOTENV_STATUS
    if _DOTENV_STATUS is not None:
        return _DOTENV_STATUS

    if get_bool("DOTENV_DISABLE", False):
        _DOTENV_STATUS = False
        return _DOTENV_STATUS

    root = _repo_root()
    paths = discover_env_files(start=root)
    if paths:
        load_env_files(paths=paths, override=override)
    _DOTENV_STATUS = True if paths else False
    return _DOTENV_STATUS


def dotenv_loaded() -> bool:
    return bool(_DOTENV_STATUS)


def dotenv_paths() -> list[str]:
    try:
        paths = discover_env_files(start=_repo_root())
        return [str(p) for p in paths]
    except Exception:
        return []


def dotenv_override_report() -> dict:
    """
    Report keys where process env overrides .env values (override=False).
    """
    try:
        paths = discover_env_files(start=_repo_root())
        if not paths:
            return {"count": 0, "keys": []}
        file_values = read_env_files(paths=paths)
        overridden = []
        for key, file_val in file_values.items():
            if key in os.environ and os.environ.get(key) != file_val:
                overridden.append(key)
        return {"count": len(overridden), "keys": overridden}
    except Exception:
        return {"count": 0, "keys": []}
