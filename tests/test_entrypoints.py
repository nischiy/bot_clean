from __future__ import annotations

import importlib

def test_entrypoints_exist():
    main = importlib.import_module("main")
    assert hasattr(main, "main") and callable(main.main)

    bootstrap = importlib.import_module("app.bootstrap")
    assert hasattr(bootstrap, "compose_trader_app")

    run = importlib.import_module("app.run")
    assert hasattr(run, "TraderApp")
