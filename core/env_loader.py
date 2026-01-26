from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

__all__ = [
    "load_env_files",
    "discover_env_files",
    "read_env_files",
]

_LOG = logging.getLogger("core.env_loader")

# Matches ${VAR_NAME} for simple in-file / environment expansion
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)}")

def _load_with_dotenv(env_path: Path, override: bool) -> bool:
    """
    Try to load using python-dotenv if installed.
    Returns True on success (library present and did load), False otherwise.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return False
    try:
        load_dotenv(dotenv_path=str(env_path), override=override)
        _LOG.info("Loaded env via python-dotenv: %s (override=%s)", env_path, override)
        return True
    except Exception as e:
        _LOG.exception("python-dotenv failed for %s: %s", env_path, e)
        return False


def _strip_inline_comment(value: str) -> str:
    """
    Remove an inline comment starting with '#' if the hash is not inside quotes.
    Example:  KEY=foo # comment  -> 'foo'
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return value[:i].rstrip()
    return value


def _unquote(value: str) -> str:
    """
    Remove surrounding single/double quotes and unescape common sequences.
    """
    v = value.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        v = v[1:-1]
    # Basic escapes
    v = v.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    return v


def _expand_vars(value: str, env: Mapping[str, str]) -> str:
    """
    Expand ${VAR} using provided env mapping (already-loaded + process).
    """
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        return env.get(key, os.environ.get(key, ""))
    return _VAR_PATTERN.sub(repl, value)


def _parse_kv(line: str) -> Optional[Tuple[str, str]]:
    """
    Parse a single KEY=VALUE (or 'export KEY=VALUE') line.
    Returns (key, raw_value) or None if not a kv line.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.lower().startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None
    k, v = line.split("=", 1)
    k = k.strip()
    v = _strip_inline_comment(v.strip())
    if not k:
        return None
    return k, v


def _minimal_parse(env_path: Path, override: bool, env_out: MutableMapping[str, str]) -> None:
    """
    Lightweight parser for .env files:
      - Supports comments (#) and inline comments outside quotes
      - Supports 'export KEY=VALUE'
      - Supports quoted values and simple escapes
      - Supports ${VAR} expansion using already-set values
      - Respects 'override' flag
    Writes results into env_out (typically os.environ-like).
    """
    try:
        text = env_path.read_text(encoding="utf-8", errors="strict")
    except Exception as e:
        _LOG.exception("Failed reading env file %s: %s", env_path, e)
        return

    lines = text.splitlines()
    applied: List[str] = []
    for raw in lines:
        parsed = _parse_kv(raw)
        if not parsed:
            continue
        key, raw_val = parsed
        val = _unquote(_expand_vars(raw_val, env_out))
        if override or key not in env_out:
            env_out[key] = val
            applied.append(key)

    if applied:
        _LOG.info("Loaded env via minimal parser: %s (override=%s, keys=%d)", env_path, override, len(applied))


def read_env_files(paths: Optional[Iterable[Path]] = None) -> Mapping[str, str]:
    """
    Read env files without mutating os.environ.
    Returns the last-seen value for each key in file order.
    """
    files = list(paths) if paths is not None else discover_env_files()
    if not files:
        return {}
    base_env: MutableMapping[str, str] = dict(os.environ)
    parsed: MutableMapping[str, str] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            _LOG.exception("Failed reading env file %s: %s", p, e)
            continue
        for raw in text.splitlines():
            parsed_kv = _parse_kv(raw)
            if not parsed_kv:
                continue
            key, raw_val = parsed_kv
            merged = dict(base_env)
            merged.update(parsed)
            val = _unquote(_expand_vars(raw_val, merged))
            parsed[key] = val
    return dict(parsed)


def discover_env_files(
    start: Optional[Path] = None,
    names: Sequence[str] = (".env", ".env.local", ".env.development"),
) -> List[Path]:
    """
    Discover env files starting at 'start' (default: cwd) and not walking up.
    Priority is the order of 'names'. Existing files are returned in that order.
    """
    base = (start or Path.cwd()).resolve()
    out: List[Path] = []
    for n in names:
        p = (base / n).resolve()
        if p.exists() and p.is_file():
            out.append(p)
    # Fallback: .env.example (lowest priority)
    ex = (base / ".env.example").resolve()
    if ex.exists() and ex.is_file():
        out.append(ex)
    return out


def load_env_files(
    paths: Optional[Iterable[Path]] = None,
    override: bool = False,
    required_keys: Optional[Iterable[str]] = None,
) -> Mapping[str, str]:
    """
    Load environment variables from the given '.env' files (or discovered defaults).
    - 'override': if True, values from files will overwrite existing os.environ.
    - 'required_keys': if provided, raises RuntimeError if any are missing after load.

    Returns a shallow copy of the resulting environment subset for inspection.
    """
    env_view: MutableMapping[str, str] = os.environ  # use process env as the sink
    files = list(paths) if paths is not None else discover_env_files()

    if not files:
        _LOG.info("No .env files found in working directory: %s", Path.cwd())

    for p in files:
        ok = _load_with_dotenv(p, override=override)
        if not ok:
            _minimal_parse(p, override=override, env_out=env_view)

    # Validate required keys if any
    if required_keys:
        missing = [k for k in required_keys if k not in env_view or env_view[k] == ""]
        if missing:
            msg = f"Missing required env keys after load: {missing}"
            _LOG.error(msg)
            raise RuntimeError(msg)

    # Return a copy of loaded/visible keys (diagnostics convenience)
    if required_keys:
        subset = {k: env_view.get(k, "") for k in required_keys}
    else:
        # modest subset to avoid dumping entire environment
        subset = {k: env_view[k] for k in env_view if k.isupper()}
    return dict(subset)
