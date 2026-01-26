from __future__ import annotations

import logging
from pathlib import Path


def _reset_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)
    return logger


def test_runtime_log_created_when_not_pytest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from app import run

    _reset_logger(run.LOG_NAME)
    run._setup_logging()

    assert (tmp_path / "runtime.log").exists()
    sessions = list((tmp_path / "sessions").glob("*.log"))
    assert sessions, "session log not created"


def test_runtime_log_skipped_in_pytest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from app import run

    _reset_logger(run.LOG_NAME)
    run._setup_logging()

    assert not (tmp_path / "runtime.log").exists()
    assert not (tmp_path / "sessions").exists()


def test_runtime_logs_not_created_on_import(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    import importlib

    importlib.import_module("app.run")

    assert not (tmp_path / "runtime.log").exists()
    assert not (tmp_path / "sessions").exists()
