from __future__ import annotations

from core.runtime_mode import get_runtime_settings, reset_runtime_settings, RuntimeMode


def test_runtime_mode_paper(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "1")
    monkeypatch.setenv("SAFE_RUN", "0")
    monkeypatch.setenv("RUNTIME_MODE", "paper")
    reset_runtime_settings()
    settings = get_runtime_settings()
    assert settings.mode == RuntimeMode.PAPER


def test_runtime_mode_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE_MODE", "1")
    monkeypatch.setenv("RUNTIME_MODE", "offline")
    reset_runtime_settings()
    settings = get_runtime_settings()
    assert settings.mode == RuntimeMode.OFFLINE


def test_runtime_mode_replay(monkeypatch):
    monkeypatch.setenv("REPLAY_MODE", "1")
    monkeypatch.setenv("RUNTIME_MODE", "replay")
    reset_runtime_settings()
    settings = get_runtime_settings()
    assert settings.mode == RuntimeMode.REPLAY


def test_runtime_mode_safe_run(monkeypatch):
    monkeypatch.setenv("SAFE_RUN", "1")
    reset_runtime_settings()
    settings = get_runtime_settings()
    assert settings.safe_run is True
