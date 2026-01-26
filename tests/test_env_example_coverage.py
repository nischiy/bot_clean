from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

ENV_PATTERNS = [
    re.compile(r'settings\.get_[a-zA-Z_]+\(\s*["\']([A-Z0-9_]+)["\']'),
    re.compile(r'get_env\(\s*["\']([A-Z0-9_]+)["\']'),
    re.compile(r'os\.environ\.get\(\s*["\']([A-Z0-9_]+)["\']'),
    re.compile(r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']'),
]

ALLOWLIST = {
    "PYTEST_CURRENT_TEST",
    "PYTEST_ADDOPTS",
    "PYTEST_XDIST_WORKER",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_DEBUG",
    "CI",
    "TERM",
}

PREFIX_ALLOWLIST = {
    "OFFLINE_PRICE_",
    "OFFLINE_MIN_QTY_",
    "OFFLINE_STEP_SIZE_",
    "OFFLINE_MIN_NOTIONAL_",
    "OFFLINE_TICK_SIZE_",
}


def _env_keys_from_example() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            continue
        key = raw.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _iter_python_files() -> list[Path]:
    files = []
    for base in (ROOT / "app", ROOT / "core"):
        for path in base.rglob("*.py"):
            files.append(path)
    return files


def _env_keys_used_in_code() -> set[str]:
    keys = set()
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for pattern in ENV_PATTERNS:
            keys.update(pattern.findall(text))
    return keys


def test_env_example_covers_used_keys():
    env_example_keys = _env_keys_from_example()
    used = _env_keys_used_in_code()

    def _allowed(key: str) -> bool:
        if key in ALLOWLIST:
            return True
        return any(key.startswith(prefix) for prefix in PREFIX_ALLOWLIST)

    missing = sorted(k for k in used if k not in env_example_keys and not _allowed(k))
    assert not missing, f"Missing env keys in .env.example: {missing}"
