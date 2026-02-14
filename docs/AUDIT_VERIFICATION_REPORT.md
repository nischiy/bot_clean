# Audit Verification Report

**Date:** 2026-02-14  
**Scope:** Authoritative implementation summary vs code/docs/tests.  
**Outcome:** Verified; all items match. No discrepancies. Tests: 213 passed.

---

## A) Verified (with file/line anchors)

### 1) Router visibility

| Item | Location |
|------|----------|
| `router_debug` built in decision_engine | `app/strategy/decision_engine.py`: dict at 1746–1751 (`regime_detected`, `strategies_for_regime`, `enabled_strategies`, `rejected_strategies`) |
| Added to signal | `app/strategy/decision_engine.py`: 1923 `"router_debug": router_debug` |
| Propagated via _build_explain_fields | `app/run.py`: 1911 `"router_debug": signal.get("router_debug")` |
| Propagated via _build_decision_log | `app/run.py`: 2135 `"router_debug": explain_fields.get("router_debug")` |
| decision_clean router_debug.compact | `app/run.py`: 1080–1094 `_router_debug_compact`, 1132 `router_compact = _router_debug_compact(...)`, 1158 `payload["router_debug"] = {"compact": router_compact}` |

### 2) Concrete strategy_block_reason

| Item | Location |
|------|----------|
| COMPRESSION/EVENT/unknown => not_mapped_to_regime | `app/strategy/decision_engine.py`: 1699, 1702, 1705 `strategy_block_reason = "not_mapped_to_regime"` |
| No eligible => normalize_strategy_block_reason(first_code) | `app/strategy/decision_engine.py`: 1711, 1731 `strategy_block_reason = normalize_strategy_block_reason(first_code)` |
| No generic "strategy_ineligible" as block_reason | `app/strategy/decision_engine.py`: 1697 comment; no assignment of literal `"strategy_ineligible"` to strategy_block_reason |
| Normalization used in both branches | `app/strategy/decision_engine.py`: 1711 (len(eligible)==0), 1731 (else fallback when not eligible_strategies) |

### 3) Reclaim

| Item | Location |
|------|----------|
| effective_tolerance = max(reclaim_tol_abs, reclaim_tol_atr*atr14) | `app/strategy/decision_engine.py`: 1018–1022 `reclaim_tol_abs`, `reclaim_tol_atr`; 1020–1022 `effective_tolerance = max(...)` |
| reclaim_level_used, effective_tolerance, distance_to_reclaim in signal | `app/strategy/decision_engine.py`: 1024 `reclaim_level_used = ema50_ltf`; 1025–1029 `distance_to_reclaim`; 1991–1993 in signal dict |
| In decision logs (explain_fields / log_dict) | `app/run.py`: 1984–1986 (_build_explain_fields from signal), 2153–2155 (_build_decision_log from explain_fields) |
| _reclaim_debug_compact uses them | `app/run.py`: 1096–1114, 1098–1100 `decision_log.get("reclaim_level_used")`, `effective_tolerance`, `distance_to_reclaim` |

### 4) Stability

| Item | Location |
|------|----------|
| stability_mode_used (hard\|soft\|block) in signal | `app/strategy/decision_engine.py`: 1930 `"stability_mode_used": "hard" if stable_ok else ("soft" if stable_soft else "block")` |
| adaptive_soft_stability in signal | `app/strategy/decision_engine.py`: 1931–1937 |
| risk_manager 0.7 qty, step_size, min_qty | `app/risk/risk_manager.py`: 246–250 `adaptive_enabled`; 249 `qty = round_to_step(float(qty) * 0.7, step_size)`; 250 `qty = max(qty, min_qty) if min_qty and qty is not None else qty` |

### 5) RANGE_IN_TREND

| Item | Location |
|------|----------|
| RANGE_IN_TREND_ENABLED | `core/config/settings.py`: 113 `"RANGE_IN_TREND_ENABLED": "0"` |
| range_in_trend_long_ok | `app/strategy/decision_engine.py`: 1409–1435 |
| strategy_ok / priority / rejects / exit_map | `app/strategy/decision_engine.py`: 1663–1664, 1679, 1681–1682, 1694–1695, 1764 |
| Selection branch for RANGE_IN_TREND_LONG | `app/strategy/decision_engine.py`: 1807–1810 `elif selected_strategy == "RANGE_IN_TREND_LONG"` |

### 6) decision_clean payload

| Item | Location |
|------|----------|
| router_debug.compact | `app/run.py`: 1158 `payload["router_debug"] = {"compact": router_compact}` |
| reclaim_debug.compact | `app/run.py`: 1160 `payload["reclaim_debug"] = {"compact": reclaim_compact}` |
| stability_mode_used | `app/run.py`: 1134, 1143 |
| gating_summary | `app/run.py`: 1131, 1155 |

### 7) TIME_EXIT branch router_debug

| Item | Location |
|------|----------|
| router_debug with regime_detected, strategies_for_regime, enabled_strategies, rejected_strategies | `app/strategy/decision_engine.py`: 1637–1642 `router_debug = {"regime_detected": routing_regime, "strategies_for_regime": ["TIME_EXIT"], "enabled_strategies": ["TIME_EXIT"], "rejected_strategies": {}}` |

### 8) Edge-case strategy_block_reason

| Item | Location |
|------|----------|
| normalize_strategy_block_reason(None/empty/whitespace) => gated_by_unknown | `app/strategy/decision_engine.py`: 15–21 `normalize_strategy_block_reason`; 19–20 `if code is None or not str(code).strip() or ":" not in str(code): return "gated_by_unknown"` |
| Module-level function used in both branches | `app/strategy/decision_engine.py`: 15 (definition), 1711, 1731 (use) |
| rejected_strategies safe build (empty list fallback) | `app/strategy/decision_engine.py`: 1737–1745; 1743–1744 `first_rej = rejects[0] if rejects else None`; fallback `"unknown"` |

### 9) Unit test test_normalize_strategy_block_reason_edge_cases

| Item | Location |
|------|----------|
| None/empty/whitespace => gated_by_unknown | `tests/test_decision_engine.py`: 1019–1022 |
| P:reclaim => gated_by_reclaim | `tests/test_decision_engine.py`: 1023 |
| Never returns "strategy_ineligible" | `tests/test_decision_engine.py`: 1026–1030 |

### 10) Golden replay

| Item | Location |
|------|----------|
| Loads golden_bars.json | `tests/test_golden_replay_defaults_off.py`: 136–137 `with open(GOLDEN_BARS_PATH)` |
| Bar-by-bar with defaults OFF (env override) | `tests/test_golden_replay_defaults_off.py`: 131–136 `monkeypatch.setenv("ADAPTIVE_SOFT_STABILITY_ENABLED", "0")` etc.; 140–153 loop |
| Signatures vs baseline | `tests/test_golden_replay_defaults_off.py`: 166–173 |
| Baseline written ONLY if UPDATE_GOLDEN=1 | `tests/test_golden_replay_defaults_off.py`: 162–165 `if os.environ.get("UPDATE_GOLDEN") == "1":` then write and skip |
| Fixtures exist | `tests/fixtures/golden_bars.json`, `tests/fixtures/baselines/golden_signatures_defaults_off.json` |

### 11) Coverage tests (test_audit_features.py)

| Item | Location |
|------|----------|
| (a) Adaptive soft ON, qty ~0.7, >= min_qty | `tests/test_audit_features.py`: 89–132 `test_adaptive_soft_stability_reduces_qty_when_enabled` |
| (b) Range-in-trend ON, router_debug and eligibility/priority | `tests/test_audit_features.py`: 135–155 `test_range_in_trend_eligible_when_enabled_and_conditions_met`; 154–155 router_debug assert |
| (c) Reclaim effective_tolerance and distance_to_reclaim formula | `tests/test_audit_features.py`: 158–184 `test_pullback_reclaim_effective_tolerance_and_distance`; 179, 182 expected_tol, expected_dist |

### 12) Docs

| Item | Location |
|------|----------|
| TESTING.md golden replay + UPDATE_GOLDEN=1 | `docs/TESTING.md`: 69–78 |
| CONFIG.md new flags and decision_clean; no schema changes | `docs/CONFIG.md`: 186–187 (ADAPTIVE_SOFT_STABILITY_ENABLED), 211–217 (optional flags), 265–270 (decision_clean fields), 270 "Decision logging is additive; no JSON schema changes." |

### 13) run.py kill-switch fix

| Item | Location |
|------|----------|
| daily_state: {} in early-return path | `app/run.py`: 391–396 `_log_structured(..., "daily_state": {})` |

### 14) test_closed_candle_gating

| Item | Location |
|------|----------|
| initialize_config_fingerprint() before app | `tests/test_closed_candle_gating.py`: 38–39 (`test_closed_candle_gating`), 134–136 (`test_closed_candle_gating_restart`) |

### 15) .env.example and defaults

| Item | Location |
|------|----------|
| New keys; defaults OFF | `core/config/settings.py`: 113, 126, 134; `.env.example`: 97, 123, 144 |
| test_env_example_covers_used_keys passes | Covered by full pytest run below |

---

## B) Behavioral checks

- **decision.schema.json unchanged:** `app/core/schemas/decision.schema.json` contains only `intent`, `reject_reasons`, `entry`, `sl`, `tp`, `rr`, `tp_targets`, `move_sl_to_be_after_tp1`, `strategy`, `timestamp`. New fields live in `signal` / logs only; schema does not reference them; validation allows extra keys (draft-07 default). **Confirmed.**
- **Defaults OFF:** `ADAPTIVE_SOFT_STABILITY_ENABLED`, `RANGE_IN_TREND_ENABLED` default `"0"` in settings (113, 134); `PULLBACK_RECLAIM_TOL_ABS` default `"0.0"` (126). `.env.example` reflects them (97, 123, 144). **Confirmed.**

---

## C) Safety invariants check

- **SAFE_RUN / fail-closed:** No changes to live execution enabling or to kill/validation logic. Only additive logging, optional flags (default OFF), and the run.py fix for the kill_switch log (use `daily_state: {}` when variable not yet set). **Unchanged.**
- **No baseline write unless UPDATE_GOLDEN=1:** Baseline file is written only inside `if os.environ.get("UPDATE_GOLDEN") == "1":` in `tests/test_golden_replay_defaults_off.py` (162–164). **Guard present.**

---

## D) Tests

- **Command:** `py -3 -m pytest tests/ -q --tb=line`
- **Result:** 213 passed.

---

## E) Files changed in this verification

- **None.** Verification was read-only; all items matched the authoritative summary. No code or doc edits were required.

---

## F) Files expected (per summary) — all present

- `app/strategy/decision_engine.py` — router_debug, concrete block reason, reclaim, stability_mode_used, adaptive_soft_stability, RANGE_IN_TREND, normalize_strategy_block_reason, rejected_strategies safe build
- `app/run.py` — _build_explain_fields, _build_decision_log, _log_decision_clean (router_debug.compact, reclaim_debug.compact, stability_mode_used, gating_summary), kill_switch daily_state fix
- `app/risk/risk_manager.py` — 0.7 qty multiplier with round_to_step and min_qty
- `tests/test_decision_engine.py` — test_normalize_strategy_block_reason_edge_cases
- `tests/test_golden_replay_defaults_off.py` — golden replay, UPDATE_GOLDEN guard
- `tests/fixtures/golden_bars.json` — fixture bars
- `tests/fixtures/baselines/golden_signatures_defaults_off.json` — baseline
- `tests/test_audit_features.py` — (a)(b)(c) coverage
- `tests/test_closed_candle_gating.py` — initialize_config_fingerprint in both tests
- `docs/TESTING.md` — golden replay section
- `docs/CONFIG.md` — new flags, decision_clean, no schema changes
- `.env.example` — new keys

**End of report.**
