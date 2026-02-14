# HOLD Blocker Analysis — Code-Level Root Causes

## Section: Blocker Root Causes (Code-Level)

### P:dist50

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py` |
| **Function name(s)** | `make_decision` (inline pullback reject logic) |
| **Exact boolean condition** | `if dist50 is None or dist50 > pullback_reentry_dist50_max:` → `pullback_rejects.append("P:dist50")` |
| **Formula** | **dist50** (used in condition): `dist50 = |close_ltf - ema50_ltf| / atr14` when `close_ltf`, `ema50_ltf`, `atr14` all present and > 0; else `None`. Computed at lines 761–764. **Pass condition:** `dist50 is not None and dist50 <= pullback_reentry_dist50_max`. **Fail:** `dist50 is None` OR `dist50 > pullback_reentry_dist50_max`. |
| **Threshold** | `pullback_reentry_dist50_max` = `settings.get_float("PULLBACK_REENTRY_DIST50_MAX")` → **1.5** (default in `core/config/settings.py` line 84). |
| **Inputs that cause fail** | `dist50 > 1.5` (e.g. dist50_curr ≈ 2.12) or `dist50` is `None`. |
| **Hard/Soft** | Hard (direct reject; pullback_reentry_*_ok cannot pass without dist50 ≤ max). |
| **Regime-dependent** | Yes. P:dist50 is only appended when evaluating pullback; `regime_detected == "PULLBACK"` is required for PULLBACK_REENTRY to be routed; reject list is populated regardless, but used when `routing_regime == "PULLBACK"`. |

**Code quote (decision_engine.py):**
```python
# Lines 761-764 (dist50 computation)
dist50 = (
    _safe_div(abs(close_ltf - ema50_ltf), atr14, default=None)
    if close_ltf is not None and close_ltf > 0 and ema50_ltf is not None and ema50_ltf > 0
    else None
) 
# Lines 1401-1402 (reject)
if dist50 is None or dist50 > pullback_reentry_dist50_max:
    pullback_rejects.append("P:dist50")
```

---

### P:pullback_bars

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py` |
| **Function name(s)** | `make_decision` |
| **Exact boolean condition** | **Up trend:** `if htf_trend == "up":` then `if consec_below_ema50_prev is None or consec_below_ema50_prev < pullback_reentry_min_bars:` → `pullback_rejects.append("P:pullback_bars")`. **Down trend:** `if htf_trend == "down":` then `if consec_above_ema50_prev is None or consec_above_ema50_prev < pullback_reentry_min_bars:` → same. |
| **Formula** | **bars_since_signal:** For up: `bars_since_signal = consec_below_ema50_prev`; for down: `bars_since_signal = consec_above_ema50_prev` (run.py explain only; decision uses consec_* directly). **Pass:** `consec_below_ema50_prev >= pullback_reentry_min_bars` (up) or `consec_above_ema50_prev >= pullback_reentry_min_bars` (down). **Fail:** value is `None` or `< pullback_reentry_min_bars`. |
| **Threshold** | `pullback_reentry_min_bars` = `settings.get_int("PULLBACK_REENTRY_MIN_BARS")` → **2**. |
| **Inputs that cause fail** | e.g. `bars_since_signal = 1`, `min_bars = 2` → 1 < 2. |
| **Hard/Soft** | Hard. |
| **Regime-dependent** | Yes (PULLBACK regime; also trend-dependent: up vs down use different consec_* counters). |

**Code quote (decision_engine.py):**
```python
# Lines 1405-1410
if htf_trend == "up":
    if consec_below_ema50_prev is None or consec_below_ema50_prev < pullback_reentry_min_bars:
        pullback_rejects.append("P:pullback_bars")
if htf_trend == "down":
    if consec_above_ema50_prev is None or consec_above_ema50_prev < pullback_reentry_min_bars:
        pullback_rejects.append("P:pullback_bars")
```

---

### P:reclaim

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py`, `app/run.py` (explain only) |
| **Function name(s)** | `make_decision`; reclaim booleans computed inline. |
| **Exact boolean condition** | **Up:** `if htf_trend == "up":` then `if close_ltf is None or ema50_ltf is None or close_ltf <= ema50_ltf:` → `pullback_rejects.append("P:reclaim")`. **Down:** `if htf_trend == "down":` then `if close_ltf is None or ema50_ltf is None or close_ltf >= ema50_ltf:` → same. |
| **Formula** | **reclaim_long** (decision_engine.py 722–730): `close_ltf > ema50_ltf and close_prev <= ema50_ltf` (with non-None checks). **reclaim_short** (732–740): `close_ltf < ema50_ltf and close_prev >= ema50_ltf`. **reclaim_required** (run.py): `"long"` if htf_trend == "up", `"short"` if htf_trend == "down". **Pass (no P:reclaim):** Up → `close_ltf > ema50_ltf`; Down → `close_ltf < ema50_ltf`. **Fail:** Up → `close_ltf <= ema50_ltf`; Down → `close_ltf >= ema50_ltf`. |
| **Threshold** | Strict: price must be on correct side of EMA50 (above for long, below for short). |
| **Inputs that cause fail** | `reclaim_required = short` and `reclaim_short = false` ⇒ `close_ltf >= ema50_ltf` (price not below EMA50). |
| **Hard/Soft** | Hard. |
| **Regime-dependent** | Yes (PULLBACK; and trend up vs down). |

**Code quote (decision_engine.py):**
```python
# Lines 722-743 (reclaim definitions)
reclaim_long = (
    close_ltf is not None and ... and close_ltf > ema50_ltf and close_prev <= ema50_ltf
)
reclaim_short = (
    close_ltf is not None and ... and close_ltf < ema50_ltf and close_prev >= ema50_ltf
)
# Lines 1411-1416 (reject)
if htf_trend == "up":
    if close_ltf is None or ema50_ltf is None or close_ltf <= ema50_ltf:
        pullback_rejects.append("P:reclaim")
if htf_trend == "down":
    if close_ltf is None or ema50_ltf is None or close_ltf >= ema50_ltf:
        pullback_rejects.append("P:reclaim")
```

---

### P:confirm

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py`, `app/run.py` (explain: confirmation_type, body_ratio, min_bars) |
| **Function name(s)** | `make_decision` (reject); `_build_explain_pullback` (confirm explain). |
| **Exact boolean condition** | **Decision engine:** `if close_prev is None or close_ltf is None:` → `pullback_rejects.append("P:confirm")`; `else: if htf_trend == "up" and close_ltf <= close_prev:` → append; `if htf_trend == "down" and close_ltf >= close_prev:` → append. **Explain (run.py):** confirmation_type = "MISSING_DATA" | "CLOSE_DIRECTION" | "BODY" | "BARS" | "OK"; confirm_ok = True only when close_dir_ok and body_ok and bars_ok. |
| **Formula** | **Close direction:** Up → `close_ltf > close_prev`; Down → `close_ltf < close_prev`. **Body:** `candle_body_ratio >= pullback_reentry_confirm_body_min` (0.5). **Bars:** `bars_since_signal >= min_bars` (2), where bars_since_signal = consec_below_ema50_prev (up) or consec_above_ema50_prev (down). **Pass:** close_prev/close_ltf non-None, correct close direction, body_ok, bars_ok. |
| **Threshold** | `PULLBACK_REENTRY_CONFIRM_BODY_MIN` = 0.5; `PULLBACK_REENTRY_MIN_BARS` = 2. |
| **Inputs that cause fail** | Missing close_prev/close_ltf; or (up and close_ltf <= close_prev); or (down and close_ltf >= close_prev); or body_ratio < 0.5; or bars_since_signal < 2. |
| **Hard/Soft** | Hard. |
| **Regime-dependent** | Yes (PULLBACK; trend up/down for close direction and bars_since_signal). |

**Code quote (decision_engine.py):**
```python
# Lines 1417-1423
if close_prev is None or close_ltf is None:
    pullback_rejects.append("P:confirm")
else:
    if htf_trend == "up" and close_ltf <= close_prev:
        pullback_rejects.append("P:confirm")
    if htf_trend == "down" and close_ltf >= close_prev:
        pullback_rejects.append("P:confirm")
```

---

### P:strategy_ineligible

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py` |
| **Function name(s)** | `make_decision` |
| **Exact boolean condition** | `if not pullback_reentry_long_ok and not pullback_reentry_short_ok:` → `pullback_rejects.append("P:strategy_ineligible")`. |
| **Formula** | **Pass (no P:strategy_ineligible):** `pullback_reentry_long_ok or pullback_reentry_short_ok`. **Fail:** both False. pullback_reentry_long_ok / short_ok are the full AND-chains (trend, trend_stable, dist50, dist50_prev, consec_* bars, reclaim, close direction, body, reclaim vol/consec, volume_ratio, vol_min) plus `_apply_trend_stability_gate` (stable_block, stable_soft+confirm+anti_reversal). |
| **Threshold** | N/A (aggregate of all pullback conditions). |
| **Inputs that cause fail** | Any combination that makes both long and short pullback conditions false (e.g. dist50 fail, bars fail, reclaim fail, confirm fail, etc.). |
| **Hard/Soft** | Hard (consequence of other fails). |
| **Regime-dependent** | Yes (only for PULLBACK regime). |

**Code quote (decision_engine.py):**
```python
# Line 1437-1438
if not pullback_reentry_long_ok and not pullback_reentry_short_ok:
    pullback_rejects.append("P:strategy_ineligible")
```

---

### Stability (stability_score, stability_soft, stability_hard)

| Item | Detail |
|------|--------|
| **File path(s)** | `app/strategy/decision_engine.py` (`_compute_stability`), `core/config/settings.py` |
| **Function name(s)** | `_compute_stability` |
| **Formula** | `r_val = trend_candles / stability_n`, `w_val = wick_ratio_count / stability_n`, `x_val = clamp(dist50 / XMAX, 0, 1)`. `score = 0.55*r_val + 0.25*(1 - w_val) + 0.20*(1 - x_val)`. `stable_ok = score >= STABILITY_HARD`, `stable_soft_ok = STABILITY_SOFT <= score < STABILITY_HARD`, `stable_block = score < STABILITY_SOFT`. |
| **Threshold** | `STABILITY_HARD` = 0.70, `STABILITY_SOFT` = 0.58, `XMAX` = 1.8 (settings). |
| **Hard/Soft** | `stable_block` (score < 0.58) is hard (blocks via _apply_trend_stability_gate). `stable_soft` (0.58 ≤ score < 0.70) is soft (requires confirmation_ok and no anti_reversal). |
| **Regime-dependent** | Used for trend/pullback strategies via _apply_trend_stability_gate; not regime-specific thresholds. |

**Code quote (decision_engine.py):**
```python
# Lines 89-102
r_val = _safe_div(trend_candles, stability_n, default=0.0) or 0.0
w_val = _safe_div(wick_ratio_count, stability_n, default=0.0) or 0.0
xmax = max(settings.get_float("XMAX"), 1e-9)
x_val = _clamp(_safe_div(dist50, xmax, default=0.0), 0.0, 1.0, default=0.0)
score = stability_weight_r * r_val + stability_weight_w * (1 - w_val) + stability_weight_x * (1 - x_val)
stable_ok = score >= stable_hard      # >= 0.70
stable_soft_ok = stable_soft <= score < stable_hard  # [0.58, 0.70)
stable_block = score < stable_soft     # < 0.58
```

---

## Strategy routing

### regime_detected

- **Computed in:** `app/strategy/decision_engine.py`, function `compute_regime_5m(context)` (lines 323–411).
- **Order of checks (first match wins):**
  1. `event_detected` → `"EVENT"`
  2. `squeeze_break_long or squeeze_break_short` → `"SQUEEZE_BREAK"`
  3. `(brk_up or brk_dn) and volume_ratio >= breakout_vol_min` → `"BREAKOUT_EXPANSION"`
  4. `dc_width_atr <= compression_width_atr_max and volume_ratio <= compression_vol_max` → `"COMPRESSION"`
  5. `trend_bias and vol_expansion and dist50 is not None and dist50 <= trend_dist50_max` → `"TREND_ACCEL"`
  6. `trend_bias and dist50 <= trend_dist50_max and impulse_ok and (brk_up or brk_dn)` → `"TREND_CONTINUATION"`
  7. `trend_bias and dist50 is not None and dist50 > trend_dist50_max` → `"PULLBACK"`
  8. Else → `"RANGE"`

- **dist50 in regime:** Same as above: `dist50 = |close_ltf - ema50_ltf| / atr14` (inside compute_regime_5m at 347–350 using context values). So **PULLBACK** is chosen when trend is up/down and **dist50 > regime_trend_dist50_max** (default 1.0).

### eligible_strategies

- **Built in:** `app/strategy/decision_engine.py` inside `make_decision`, after all strategy_ok flags are set (lines 1567–1578).
- **Code:** `strategy_ok = { "BREAKOUT_EXPANSION": breakout_expansion_long_ok or breakout_expansion_short_ok, "SQUEEZE_BREAK": ..., "CONTINUATION": ..., "TREND_ACCEL": ..., "PULLBACK_REENTRY": pullback_reentry_long_ok or pullback_reentry_short_ok, "RANGE_MEANREV": ... }` then `eligible_strategies = [name for name, ok in strategy_ok.items() if ok]`.

### selected_strategy = NONE — exact conditions

- **routing_regime == "COMPRESSION"** → `selected_strategy = "NONE"`, `strategy_block_reason = "regime:COMPRESSION"`.
- **routing_regime == "EVENT"** → `selected_strategy = "NONE"`, `strategy_block_reason = "regime:EVENT"`.
- **routed_strategy is None** (regime not in strategy_by_regime or mapped to None) → `selected_strategy = "NONE"`, `strategy_block_reason = f"regime:{routing_regime or 'UNKNOWN'}"`.
- **len(eligible_strategies) == 0** → `selected_strategy = "NONE"`, `strategy_block_reason = "strategy_ineligible"`.
- If regime strategy is not eligible but others are, another strategy is chosen from `strategy_priority`; only if none eligible does the fallback set `selected_strategy = eligible_strategies[0] if eligible_strategies else "NONE"`.
- **Volatility gate:** If `htf_atr14_percentile < volatility_percentile_min` and selected_strategy in (`CONTINUATION`, `TREND_ACCEL`), then `selected_strategy = "NONE"`, `strategy_block_reason = "volatility_gate"`.
- **Session gate:** If `session_bucket == "Asia"` and selected_strategy == `BREAKOUT_EXPANSION`, then `selected_strategy = "NONE"`, `strategy_block_reason = "session_gate"`.

So for **PULLBACK** with no other strategy eligible, **selected_strategy = NONE** happens when **eligible_strategies** is empty, i.e. when **not (pullback_reentry_long_ok or pullback_reentry_short_ok)** (and no other strategy passes), which produces **P:strategy_ineligible** in the reject list.

---

## Section: Decision Flow Reconstruction (Step-by-Step)

**Given:**  
regime_detected = PULLBACK, dist50_curr ≈ 2.12, dist50_max = 1.5, bars_since_signal = 1, min_bars = 2, stability_score ≈ 0.39, reclaim_required = short, reclaim_short = false.

1. **Regime:** `compute_regime_5m` returns `"PULLBACK"` (trend_bias and dist50 > trend_dist50_max). `routing_regime = "PULLBACK"`, `routed_strategy = "PULLBACK_REENTRY"`.

2. **Pullback eligibility (short):**  
   - `dist50 <= pullback_reentry_dist50_max` → 2.12 <= 1.5 → **False** → pullback_reentry_short_ok would fail here; also `pullback_rejects.append("P:dist50")`.

3. **Evaluation order in code (short path):**  
   - `htf_trend == "down"`, `trend_stable_short`, `dist50 is not None`, `dist50 <= 1.5` → **False** (2.12 > 1.5). Rest of AND is not evaluated; `pullback_reentry_short_ok` stays False.  
   - Long path: `htf_trend == "up"` is False, so `pullback_reentry_long_ok` is False.

4. **Reject list (order in code):**  
   - `regime_detected != "PULLBACK"` → False (no P:regime).  
   - `htf_trend not in ("up","down")` → False (no P:trend).  
   - `dist50 is None or dist50 > 1.5` → True → **P:dist50** appended.  
   - `dist50_prev` check → possibly P:dist50_prev.  
   - For down: `consec_above_ema50_prev is None or consec_above_ema50_prev < 2` → 1 < 2 → **P:pullback_bars** appended.  
   - For down: `close_ltf >= ema50_ltf` (reclaim_short false) → **P:reclaim** appended.  
   - `close_prev is None or close_ltf is None` or (down and close_ltf >= close_prev) → **P:confirm** possibly appended.  
   - Then `not pullback_reentry_long_ok and not pullback_reentry_short_ok` → **P:strategy_ineligible** appended.

5. **Strategy routing:**  
   - `strategy_ok["PULLBACK_REENTRY"]` = False. If no other strategy is ok, `eligible_strategies = []`.  
   - `len(eligible_strategies) == 0` → `selected_strategy = "NONE"`, `strategy_block_reason = "strategy_ineligible"`.

6. **Intent:** No _apply_risk for PULLBACK_REENTRY; intent remains "HOLD". When `intent == "HOLD"`, `active_rejects = pullback_rejects` (routing_regime == "PULLBACK"), and those codes are merged into `reject_reasons`.

**Where execution “stops” for the pullback path:** The first failing condition in the pullback_reentry_short_ok AND-chain is **dist50 <= pullback_reentry_dist50_max** (2.12 > 1.5). All subsequent P:* reject codes are still appended for explainability; the decisive gate for selected_strategy = NONE is **len(eligible_strategies) == 0** because PULLBACK_REENTRY did not pass.

---

## Section: Why selected_strategy = NONE (Exact Conditions)

- **Primary:** `routing_regime == "PULLBACK"` and `routed_strategy == "PULLBACK_REENTRY"`, but `pullback_reentry_long_ok` and `pullback_reentry_short_ok` are both False. So `strategy_ok["PULLBACK_REENTRY"]` is False and `eligible_strategies` does not contain "PULLBACK_REENTRY". If no other strategy is eligible, `eligible_strategies == []`, so the branch `elif len(eligible_strategies) == 0:` sets `selected_strategy = "NONE"` and `strategy_block_reason = "strategy_ineligible"`.

- **Exact condition for NONE in this case:**  
  `len(eligible_strategies) == 0`  
  which holds because `pullback_reentry_long_ok or pullback_reentry_short_ok` is False (due to dist50, pullback_bars, reclaim, confirm, etc.).

---

## Consolidated Table

| Blocker | Code location | Formula / condition | Threshold | Actual value | Pass/Fail | Minimal change required to pass |
|---------|---------------|----------------------|-----------|--------------|-----------|----------------------------------|
| **P:dist50** | decision_engine.py ~1401–1402 | `dist50 = \|close_ltf - ema50_ltf\|/atr14`. Reject if `dist50 is None or dist50 > pullback_reentry_dist50_max`. | dist50_max = 1.5 | dist50_curr ≈ 2.12 | Fail | Lower dist50 (price closer to EMA50) or raise PULLBACK_REENTRY_DIST50_MAX above 2.12. |
| **P:pullback_bars** | decision_engine.py ~1405–1410 | Down: `consec_above_ema50_prev >= pullback_reentry_min_bars`. Reject if None or &lt; min_bars. | min_bars = 2 | bars_since_signal = 1 | Fail | Wait for one more bar so consec_above_ema50_prev ≥ 2, or set PULLBACK_REENTRY_MIN_BARS=1. |
| **P:reclaim** | decision_engine.py ~1411–1416 | Short: `close_ltf < ema50_ltf` (and close_prev ≥ ema50_ltf for reclaim_short). Reject if close_ltf ≥ ema50_ltf. | N/A (strict) | reclaim_short = false (close_ltf ≥ ema50_ltf) | Fail | Price must close below EMA50 (reclaim below). |
| **P:confirm** | decision_engine.py ~1417–1423 | Close direction: down → `close_ltf < close_prev`. Reject if close_prev/close_ltf None or (down and close_ltf ≥ close_prev). | body_min=0.5, min_bars=2 | Depends on close_ltf vs close_prev and body/bars | Depends | close_ltf < close_prev (down trend); body_ratio ≥ 0.5; bars_since_signal ≥ 2. |
| **P:strategy_ineligible** | decision_engine.py ~1437–1438 | `not pullback_reentry_long_ok and not pullback_reentry_short_ok` → append. | N/A | Both long/short False | Fail | Fix one or more of P:dist50, P:pullback_bars, P:reclaim, P:confirm (and any P:body, P:vol, P:stability, etc.) so one of pullback_reentry_*_ok is True. |

---

## Duplicated or inconsistent logic

1. **Stability formula duplicated in run.py:**  
   `stable_ok` and `stable_soft_ok` are computed in `_build_explain_pullback` (run.py ~1244–1245) and again in `_build_explain_continuation` (run.py ~1492–1493) with the same expressions. The single source of truth is `_compute_stability` in decision_engine.py; run.py only uses the result for explain. So logic is consistent but the formula is duplicated in two places in run.py.

2. **dist50 in two places:**  
   dist50 is computed in `compute_regime_5m` (decision_engine.py 347–350) for regime detection and again in `make_decision` (761–764) for pullback and stability. Same formula, same inputs; no inconsistency, just computed twice.

3. **Confirm “bars” in decision vs explain:**  
   In the decision engine, pullback pass requires `consec_above_ema50_prev >= pullback_reentry_min_bars` (down) or `consec_below_ema50_prev >= pullback_reentry_min_bars` (up). In run.py explain, `bars_since_signal` is set to `consec_below_ema50_prev` (up) or `consec_above_ema50_prev` (down) and `bars_ok = bars_since_signal >= min_bars`. So the same quantities and threshold are used; consistent.

4. **Reclaim in run.py vs decision_engine:**  
   run.py uses `reclaim_ok = reclaim_short` when close_ltf < ema50_ltf (down); decision_engine defines reclaim_short as close_ltf < ema50_ltf and close_prev >= ema50_ltf. So run.py’s “reclaim_ok” for explain is actually “price currently below EMA50”, which matches the P:reclaim reject condition (close_ltf >= ema50_ltf for down). Consistent.

---

*Document generated from codebase analysis. No refactoring or speculative suggestions; thresholds and formulas match `core/config/settings.py` and `app/strategy/decision_engine.py`.*
