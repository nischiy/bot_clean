# Strategy Tradability Diagnostic Report

**Repo:** `g:\Bot\bot_clean`  
**Constraints:** Evidence only (file:line). No code changes. No refactors.

---

## 1) Strategy inventory (authoritative)

### 1.1 Where strategies are defined and *_ok computed

- **Strategy list and eligibility:** **app/strategy/decision_engine.py:1568-1575** — `strategy_ok` dict keys: BREAKOUT_EXPANSION, SQUEEZE_BREAK, CONTINUATION, TREND_ACCEL, PULLBACK_REENTRY, RANGE_MEANREV. Each value is `breakout_expansion_long_ok or breakout_expansion_short_ok` (and analogous for others).
- **Regime → strategy mapping:** **app/strategy/decision_engine.py:1557-1566** — strategy_by_regime: BREAKOUT_EXPANSION→BREAKOUT_EXPANSION, SQUEEZE_BREAK→SQUEEZE_BREAK, TREND_CONTINUATION→CONTINUATION, TREND_ACCEL→TREND_ACCEL, PULLBACK→PULLBACK_REENTRY, RANGE→RANGE_MEANREV, COMPRESSION/EVENT→None.

### 1.2 Strategies that can produce LONG/SHORT/CLOSE

| Strategy | Intent | Routing source (file:line) |
|----------|--------|----------------------------|
| TIME_EXIT | CLOSE | decision_engine.py:1553-1556 when time_exit_signal |
| BREAKOUT_EXPANSION | LONG/SHORT | decision_engine.py:1655-1657 |
| SQUEEZE_BREAK | LONG/SHORT | decision_engine.py:1675-1678 |
| CONTINUATION | LONG/SHORT | decision_engine.py:1658-1661 |
| TREND_ACCEL | LONG/SHORT | decision_engine.py:1662-1665 |
| PULLBACK_REENTRY | LONG/SHORT | decision_engine.py:1666-1669 |
| RANGE_MEANREV | LONG/SHORT | decision_engine.py:1670-1673 |
| NONE | HOLD | decision_engine.py:1466, 1595-1608, 1623-1624, 1646, 1652 |

### 1.3 Per-strategy: eligibility booleans, reject buckets, gating layers, thresholds

**BREAKOUT_EXPANSION**  
- Eligibility: `breakout_expansion_long_ok` / `breakout_expansion_short_ok` — **app/strategy/decision_engine.py:1250-1263** (impulse + accept/retest + trend_stable + not anti_reversal).  
- Reject bucket: **B:** — decision_engine.py:1360-1372 (B:breakout, B:impulse, B:accept, B:stability, B:vol, B:spread, B:strategy_ineligible).  
- Gating: anti-reversal applied directly (1240-1249); stability via trend_stable_long/short; session gate Asia→NONE (1648-1652).  
- Thresholds: REGIME_BREAKOUT_VOL_MIN, BREAKOUT_ACCEPT_BARS, BREAKOUT_REJECT_WICK_ATR, BREAKOUT_RETEST_ATR — **core/config/settings.py** 57-66; **.env.example** 55-62.

**SQUEEZE_BREAK**  
- Eligibility: `squeeze_break_long_ok` / `squeeze_break_short_ok` = squeeze_break_long / squeeze_break_short — **decision_engine.py:1273-1170**.  
- Reject bucket: **S:** — decision_engine.py:1275-1282 (S:compression, S:breakout, S:bias, S:strategy_ineligible).  
- Gating: no _apply_trend_stability_gate for squeeze.  
- Thresholds: SQUEEZE_BB_WIDTH_TH, CONFIRM_MIN_BODY_RATIO — settings.py 104, 123; .env.example 94, 114.

**CONTINUATION**  
- Eligibility: `cont_long_ok` / `cont_short_ok` after _cont_context_ok, _cont_common_filters, _apply_trend_stability_gate — **decision_engine.py:1016-1123**.  
- Reject bucket: **C:** — decision_engine.py:1019-1127 (C:trend, C:stability, C:body, C:vol, C:atr_ratio, C:slope, C:k, C:rsi, C:break, C:stability_block, C:confirm_soft, C:anti_reversal, C:strategy_ineligible).  
- Gating: stability gate + anti-reversal when stable_soft (1043-1074); volatility gate can set selected_strategy NONE (1640-1646).  
- Thresholds: CONT_BODY_MIN, CONT_VOL_MIN, CONT_ATR_RATIO_MIN, CONT_SLOPE_ATR_MAX/MIN, CONT_K_MAX, CONT_RSI_*, CONT_BREAK_D_ATR — settings.py 72-79, 71; .env.example (cont/confirm vars).

**TREND_ACCEL**  
- Eligibility: `trend_accel_long_ok = cont_long_ok and atr_ratio_valid and atr_ratio >= trend_accel_vol_mult` (and short) — **decision_engine.py:1264-1265**.  
- Reject bucket: **A:** — decision_engine.py:1266-1271 (A:cont, A:vol, A:strategy_ineligible).  
- Gating: same as CONTINUATION (depends on cont_*_ok); volatility gate (1640-1646).  
- Thresholds: TREND_ACCEL_VOL_MULT — settings.py 144; .env.example 114.

**PULLBACK_REENTRY**  
- Eligibility: `pullback_reentry_long_ok` / `pullback_reentry_short_ok` (base then _apply_trend_stability_gate) — **decision_engine.py:1283-1332**.  
- Reject bucket: **P:** — decision_engine.py:1397-1438 (P:regime, P:trend, P:dist50, P:dist50_prev, P:pullback_bars, P:reclaim, P:confirm, P:body, P:fake_reclaim, P:vol, P:stability, P:spread, P:strategy_ineligible; plus P:stability_block, P:confirm_soft, P:anti_reversal from gate).  
- Gating: stability + anti-reversal when stable_soft (1331-1332).  
- Thresholds: PULLBACK_REENTRY_DIST50_MIN/MAX, PULLBACK_REENTRY_MIN_BARS, PULLBACK_REENTRY_CONFIRM_BODY_MIN, PULLBACK_REENTRY_RECLAIM_VOL_MIN, PULLBACK_REENTRY_VOL_MIN — settings.py 83-91; .env.example 68-74.

**RANGE_MEANREV**  
- Eligibility: `range_meanrev_long_ok` / `range_meanrev_short_ok` — **decision_engine.py:1334-1355**.  
- Reject bucket: **M:** — decision_engine.py:1377-1395 (M:regime, M:breakout, M:trend, M:vol, M:range_edge, M:range_long, M:range_short, M:spread, M:strategy_ineligible); plus X:extreme_* (1442-1455).  
- Gating: none of stability/anti-reversal/session/volatility for meanrev itself.  
- Thresholds: RANGE_MEANREV_EDGE_ATR, RANGE_MEANREV_VOL_MAX, TREND_STRENGTH_MIN — settings.py 90-91, 100; .env.example 76-77, 84.

**Event gate (E:)**  
- decision_engine.py:1680-1686 — intent LONG/SHORT + event_block → HOLD, reject E:event or E:event_cooldown.

**EV gate (EV:)**  
- decision_engine.py:1721-1738 — EV_GATE_ENABLED and ev_value <= 0 → reject EV:low, intent HOLD.

---

## 2) Router & decision pipeline

### 2.1 End-to-end flow

- **Candle → payload:** App receives closed candle; builds payload (features_ltf, context_htf, etc.) — **app/run.py** (payload passed to make_decision).
- **make_decision:** **app/strategy/decision_engine.py:424** — `make_decision(payload, daily_state, decision_state)`.
- **Inside make_decision:** Extract features (515-549), compute regime via `compute_regime_5m` (1173-1195), compute all *_ok booleans (cont, breakout, squeeze, pullback, range, trend_accel), build `strategy_ok` (1568-1575), `eligible_strategies = [name for name, ok in strategy_ok.items() if ok]` (1579), then routing (1593-1624) and intent/risk application (1655-1677). Post-gates: event_block (1680-1686), pending_entry (1687-1719), ev_gate (1721-1738), final reject_reasons→HOLD (1738-1744), then active_rejects merged when intent==HOLD (1748-1771).
- **selected_strategy → trade plan:** **app/run.py:750** — `trade_plan, rejections = create_trade_plan(payload, decision, daily_state, exchange_positions)`. **app/risk/risk_manager.py:103** — `create_trade_plan` uses decision (intent, entry, sl, tp, etc.).

### 2.2 Where strategy_ineligible, selected_strategy=NONE, eligible_strategies=[] are set

- **eligible_strategies = []:** Not assigned literally "[]" for the “empty” case; it is the result of the list comprehension when no strategy_ok value is True: **app/strategy/decision_engine.py:1579** — `eligible_strategies = [name for name, ok in strategy_ok.items() if ok]`.
- **selected_strategy = "NONE":**  
  - **decision_engine.py:1466** — initial.  
  - **1595-1598** — routing_regime COMPRESSION.  
  - **1599-1601** — routing_regime EVENT.  
  - **1602-1604** — routed_strategy is None.  
  - **1605-1608** — **len(eligible_strategies) == 0** → strategy_block_reason = **"strategy_ineligible"**, selected_strategy = **"NONE"**.  
  - **1623** — fallback: selected_strategy = eligible_strategies[0] if eligible_strategies else "NONE".  
  - **1646** — volatility gate (CONTINUATION/TREND_ACCEL).  
  - **1652** — session gate (Asia + BREAKOUT_EXPANSION).
- **strategy_block_reason = "strategy_ineligible":** **decision_engine.py:1607** (when len(eligible_strategies)==0), **1624** (fallback when eligible_strategies empty).

---

## 3) Stability + anti-reversal deep dive

### 3.1 Formulas (as implemented)

- **stability_score:** **app/strategy/decision_engine.py:64-101** — `r_val = trend_candles / stability_n`, `w_val = wick_ratio_count / stability_n`, `x_val = clamp(dist50 / XMAX, 0, 1)`; `score = STABILITY_WEIGHT_R * r_val + STABILITY_WEIGHT_W * (1 - w_val) + STABILITY_WEIGHT_X * (1 - x_val)`. stable_ok = score >= STABILITY_HARD, stable_soft = STABILITY_SOFT <= score < STABILITY_HARD, stable_block = score < STABILITY_SOFT.
- **dist50:** **decision_engine.py:761-763** — `dist50 = |close_ltf - ema50_ltf| / atr14`. dist50_prev: 765-768 — `|close_prev - ema50_ltf| / atr14`.
- **wick_ratio:** **decision_engine.py:36-43** — `_wick_ratio(open, close, high, low)` = (upper_wick + lower_wick) / body, body = |close - open|.
- **slope_atr:** **decision_engine.py:984-993** — `slope_atr = (ema50_ltf - ema50_prev_12) / atr14`.
- **atr_ratio:** **decision_engine.py:690-693** — `atr_ratio = atr14 / atr14_sma20` when atr_ratio_valid.
- **bb_width_atr:** **decision_engine.py:778-782** — `bb_width / atr14` when bb_width and atr14 valid.
- **volume_ratio:** **app/data/payload_builder.py:332-346** — `current_vol / avg_vol` (20-period rolling mean).

### 3.2 When anti-reversal applies

- **Behind stable_soft only:** CONTINUATION and PULLBACK_REENTRY — **decision_engine.py:1043-1074** (_apply_trend_stability_gate): when stable_soft is True, confirmation_ok and _anti_reversal_filter(entry_side, ...) are checked; if anti_rev blocks, strategy becomes ineligible.
- **Direct (not behind stable_soft):** BREAKOUT_EXPANSION — **decision_engine.py:1240-1249** — _anti_reversal_filter is called for LONG/SHORT when impulse and (accept or retest); result is anded into breakout_expansion_long_ok/short_ok.

### 3.3 HTF_EMA_RECLAIM buffer/hysteresis

- **app/strategy/decision_engine.py:243-251** — EMA block: LONG blocked if `close_htf < ema_fast_htf`, SHORT if `close_htf > ema_fast_htf`. No ATR buffer or hysteresis; exact comparison only.

---

## 4) Runtime empirical section (logs)

### 4.1 Log sources and parsing approach

- **Log directories scanned:** `logs/sessions`, `logs/sessions_clean`, `logs` (including `logs/runtime.log`).
- **Events used:** Lines containing `"event":"decision_candle"` (one JSON object per closed-candle decision).
- **Parsing:** For each line, locate substring `INFO TraderApp: ` and parse the remainder as JSON. Reproducible script: **tools/log_stats/parse_decision_candles.py** (run from repo root: `py -3 tools/log_stats/parse_decision_candles.py`).
- **Log files used (exact list from run):**
  - logs/sessions/20260126_001725_11968.log
  - logs/sessions/20260126_190628_1552.log
  - logs/sessions/20260127_012936_21100.log
  - logs/sessions/20260127_022702_9000.log
  - logs/sessions/20260127_053538_8832.log
  - logs/sessions/20260127_150644_12320.log
  - logs/sessions/20260127_190924_9052.log
  - logs/sessions/20260127_191839_15168.log
  - logs/sessions/20260127_192723_9300.log
  - logs/sessions/20260127_214355_26708.log
  - logs/sessions/20260204_031606_9100.log
  - logs/sessions/20260207_121646_27816.log
  - logs/sessions_clean/20260126_001725_11968.log
  - logs/runtime.log

**Note:** Some log lines may be from older builds (e.g. different reject codes or selected_strategy values like "TREND_PULLBACK"). Counts below are as parsed.

### 4.2 Counts and fractions

| Metric | Value |
|--------|--------|
| Total decision_candle records | 449 |
| regime_detected counts | (blank): 91, PULLBACK: 274, RANGE: 66, TREND_ACCEL: 16, EVENT: 2 |
| regime_used_for_routing counts | (blank): 133, PULLBACK: 240, RANGE: 58, TREND_ACCEL: 16, EVENT: 2 |
| selected_strategy counts | NONE: 436, (blank): 9, TREND_PULLBACK: 4 |
| **eligible_strategies empty** | **358 / 449 = 79.73%** |
| **stable_block true** | **164 (36.5%)** |
| **vol_ok false (explain_pullback.vol_ok)** | **76 (16.9%)** — all in PULLBACK regime |

### 4.3 Top 10 reject_reasons overall

| Code | Count |
|------|-------|
| P:strategy_ineligible | 274 |
| P:reclaim | 253 |
| P:dist50 | 208 |
| P:dist50_prev | 203 |
| P:stability | 182 |
| P:vol | 182 |
| P:confirm | 145 |
| P:body | 136 |
| M:regime | 112 |
| M:strategy_ineligible | 92 |

### 4.4 Top reject codes per prefix (P, C, B, M, A, S, E)

- **P:** P:strategy_ineligible 274, P:reclaim 253, P:dist50 208, P:dist50_prev 203, P:stability 182.
- **C:** C:break 73, C:vol 56, C:atr_ratio 47, C:body 42, C:k 29.
- **B:** B:breakout 78, B:volume 70, B:atr 62, B:body 51 (note: current code uses B:impulse, B:vol; some logs may be from another version).
- **M:** M:regime 112, M:strategy_ineligible 92, M:reentry_long 75, M:reentry_short 75, M:trend 66.
- **A:** A:cont 16, A:strategy_ineligible 16.
- **S:** S:rsi 4, S:reclaim 4.
- **E:** E:event 2.

### 4.5 Distribution of gating metrics (min / median / p90)

| Metric | N | min | median | p90 |
|--------|---|-----|--------|-----|
| stability_score | 190 | 0.15 | 0.355 | 0.6079 |
| volume_ratio_5m | 436 | 0.2121 | 0.6999 | 2.049 |
| dist50_curr | 116 | 1.0426 | 2.1425 | 4.4421 |
| atr_ratio | 436 | 0.4855 | 0.9475 | 1.4915 |
| wick_ratio | 190 | 0.0055 | 1.1957 | 8.2032 |
| bb_width_atr | 190 | 2.8201 | 5.8156 | 6.8374 |

**Interpretation:** dist50_curr median 2.14 and p90 4.44 vs PULLBACK_REENTRY_DIST50_MAX 1.5 (settings) → most pullback candles have dist50_curr > 1.5, so P:dist50 fails. stability_score median 0.355 < STABILITY_SOFT 0.58 → stable_block or stable_soft common. volume_ratio_5m median 0.70 < PULLBACK_REENTRY_VOL_MIN 1.0 for many candles → P:vol frequent.

### 4.6 stable_block and vol_ok failure

- **stable_block true:** 164 of 449 (36.5%).
- **vol_ok false (explain_pullback):** 76 of 449 (16.9%); all 76 when regime_used_for_routing = PULLBACK.

---

## 5) Tradability diagnosis

### 5.1 Top 3 bottlenecks (with numbers)

1. **PULLBACK dist50 and reclaim (strategy never eligible)**  
   - **Evidence:** P:dist50 208, P:dist50_prev 203, P:reclaim 253, P:strategy_ineligible 274. dist50_curr median 2.14, p90 4.44 vs threshold 1.5.  
   - **Conclusion:** In sampled logs, PULLBACK is the dominant regime (240 routing); price is often too far from EMA50 (dist50 > PULLBACK_REENTRY_DIST50_MAX) or reclaim (close back across EMA50) has not occurred, so pullback_reentry_*_ok stay False and eligible_strategies stay empty.

2. **Stability gate (stable_block / stable_soft)**  
   - **Evidence:** stable_block true in 36.5% of candles; P:stability 182; stability_score median 0.355 vs STABILITY_SOFT 0.58 and STABILITY_HARD 0.70.  
   - **Conclusion:** A large share of candles fail the stability score threshold (score < 0.58 or < 0.70), so strategies that use _apply_trend_stability_gate (CONTINUATION, PULLBACK_REENTRY) are often excluded.

3. **Volume and confirm/body (P:vol, P:confirm, P:body)**  
   - **Evidence:** P:vol 182, P:confirm 145, P:body 136; vol_ok false in 16.9% of records (all PULLBACK); volume_ratio_5m median 0.70 vs PULLBACK_REENTRY_VOL_MIN 1.0.  
   - **Conclusion:** Many candles fail pullback volume or confirmation/body checks, contributing to empty eligible_strategies when regime is PULLBACK.

### 5.2 Expected trade frequency (per day) under current thresholds

- **Evidence:** 449 decision_candle records, selected_strategy non-NONE in 13 records (4 TREND_PULLBACK + 9 blank; NONE in 436). So in this sample, **no TRADE intent** (no LONG/SHORT) is observed when using current schema (selected_strategy NONE or legacy TREND_PULLBACK).  
- **Rough estimate:** If each record is one 5m closed candle, 449 candles ≈ 449 × 5 min ≈ 37.4 hours of data. **Expected entry (LONG/SHORT) frequency from these logs: 0 per 37 hours** under current thresholds and code paths that set selected_strategy. So **expected trade frequency per day is 0** in the sampled conditions (predominantly PULLBACK regime with dist50/reclaim/stability/vol/confirm/body blocking).  
- **Caveat:** Sample is limited to available session and runtime logs; different market regimes (e.g. more TREND_CONTINUATION or BREAKOUT_EXPANSION) could yield non-zero trades.

---

**End of report.** All claims cite file:line or parsed log output. No application code was changed.
