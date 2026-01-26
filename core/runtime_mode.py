from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.config.env import parse_bool, get_env


class RuntimeMode(str, Enum):
    TEST = "test"
    OFFLINE = "offline"
    PAPER = "paper"
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True)
class RuntimeSettings:
    mode: RuntimeMode
    env: str
    trade_enabled: bool
    paper_trading: bool
    dry_run_only: bool
    safe_run: bool
    live_readonly: bool

    @property
    def is_test(self) -> bool:
        return self.mode == RuntimeMode.TEST

    @property
    def is_offline(self) -> bool:
        return self.mode == RuntimeMode.OFFLINE

    @property
    def is_paper(self) -> bool:
        return self.mode == RuntimeMode.PAPER

    @property
    def is_live(self) -> bool:
        return self.mode == RuntimeMode.LIVE

    @property
    def is_replay(self) -> bool:
        return self.mode == RuntimeMode.REPLAY


_CACHE: Optional[RuntimeSettings] = None


def get_runtime_settings() -> RuntimeSettings:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    env_name = str(get_env("ENV", "production") or "production").lower()
    trade_enabled = parse_bool(get_env("TRADE_ENABLED", "0"))
    paper_trading = parse_bool(get_env("PAPER_TRADING", "1"))
    dry_run_only = parse_bool(get_env("DRY_RUN_ONLY", "1"))
    safe_run = parse_bool(get_env("SAFE_RUN", "0"))
    live_readonly = parse_bool(get_env("LIVE_READONLY", "0"))
    override = str(get_env("RUNTIME_MODE", "") or "").strip().lower()
    offline_mode = parse_bool(get_env("OFFLINE_MODE", "0"))
    replay_mode = parse_bool(get_env("REPLAY_MODE", "0"))

    if override in {m.value for m in RuntimeMode}:
        mode = RuntimeMode(override)
    elif "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("CI", "0") == "1":
        mode = RuntimeMode.TEST
    elif replay_mode:
        mode = RuntimeMode.REPLAY
    elif offline_mode:
        mode = RuntimeMode.OFFLINE
    elif paper_trading or safe_run:
        mode = RuntimeMode.PAPER
    else:
        mode = RuntimeMode.LIVE

    # Non-production defaults: force dry-run to prevent real trading
    if env_name != "production":
        dry_run_only = True

    settings = RuntimeSettings(
        mode=mode,
        env=env_name,
        trade_enabled=trade_enabled,
        paper_trading=paper_trading,
        dry_run_only=dry_run_only,
        safe_run=safe_run,
        live_readonly=live_readonly,
    )
    _CACHE = settings
    return settings


def reset_runtime_settings() -> None:
    global _CACHE
    _CACHE = None
