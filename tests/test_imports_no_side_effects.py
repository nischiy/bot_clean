from __future__ import annotations

import importlib
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "site-packages",
    "tests",
}

def is_excluded(rel: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in rel.parts)

def iter_project_modules(root: Path):
    """
    Yield valid python module names only for files that belong to the project,
    excluding venv/site-packages/tests/etc.
    """
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)

        # exclude junk
        if is_excluded(rel):
            continue

        # skip hidden directories just in case
        if any(part.startswith(".") and part not in {".", ".."} for part in rel.parts):
            continue

        # build module name
        if rel.name == "__init__.py":
            # package module = directory path
            mod = ".".join(rel.parent.parts)
        else:
            mod = ".".join(rel.with_suffix("").parts)

        if not mod:
            continue

        yield mod

def test_imports_no_side_effects():
    os.chdir(PROJECT_ROOT)

    failed = []
    for module_name in sorted(set(iter_project_modules(PROJECT_ROOT))):
        try:
            importlib.import_module(module_name)
        except Exception as e:
            failed.append((module_name, repr(e)))

    assert not failed, "Import failures:\n" + "\n".join([f"{m}: {err}" for m, err in failed])
