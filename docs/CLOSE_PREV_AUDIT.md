# Audit: close_prev for P:confirm vs logged prev_close

## A) P:confirm code block + close_prev assignment

**File:** `app/strategy/decision_engine.py`

**Where `close_prev` is assigned (same function, earlier in `make_decision`):**

```python
    features_ltf = payload.get("features_ltf", {})
    ...
    close_ltf = _to_float(features_ltf.get("close")) or _to_float(price_snapshot.get("last")) or _to_float(price_snapshot.get("mark"))
    close_prev = _to_float(features_ltf.get("close_prev"))
```

**Exact expression assigned to `close_prev`:**  
`_to_float(features_ltf.get("close_prev"))`  
— i.e. the value from **`payload["features_ltf"]["close_prev"]`**, coerced to float (or `None` if missing/invalid).

**Exact block that appends `P:confirm`:**

```python
    if close_prev is None or close_ltf is None:
        pullback_rejects.append("P:confirm")
    else:
        if htf_trend == "up" and close_ltf <= close_prev:
            pullback_rejects.append("P:confirm")
        if htf_trend == "down" and close_ltf >= close_prev:
            pullback_rejects.append("P:confirm")
```

**Source of `close_prev`:** Direct key access: **`features_ltf.get("close_prev")`**, where `features_ltf = payload.get("features_ltf", {})`. So the variable used for P:confirm is **payload `features_ltf["close_prev"]`** (after `_to_float`).

---

## B) close_prev upstream origin (payload/features)

**File:** `app/data/payload_builder.py`

**Where the value is created:**

```python
    close_col = df_ltf.get("close") if df_ltf is not None and hasattr(df_ltf, "columns") else None
    close_ltf = _safe_float(close_col.iloc[-1]) if close_col is not None and not close_col.empty else None
    close_prev = _safe_float(close_col.iloc[-2]) if close_col is not None and len(close_col) > 1 else None
```

Then fallback when previous close is missing:

```python
    if close_prev is None and close_ltf is not None:
        close_prev = close_ltf
```

Then written into the payload:

```python
    features_ltf = {
        ...
"close": close_ltf,
"close_prev": close_prev,
        ...
    }
```

**Conclusion:**  
- **a) Previous candle close of the LTF timeframe:** Yes. `close_prev` is the close of the **second-to-last** bar of the LTF series (`close_col.iloc[-2]`), i.e. the previous closed candle’s close.  
- Not HTF close (HTF uses `prev_close_htf` elsewhere for ATR, not for `features_ltf["close_prev"]`).  
- Not derived/smoothed: raw LTF close at index -2; only `_safe_float` is applied.

---

## C) prev_close logging origin

**File:** `app/run.py`

**Where `prev_close` is added to the object that becomes the `decision_candle` log line:**

`decision_log` is built by `_build_decision_log(...)`. Inside it, the dict passed to the logger includes:

```python
        "prev_close": explain_fields.get("close_prev_ltf"),
```

(around line 2077 in `_build_decision_log`.)

**Where `explain_fields["close_prev_ltf"]` comes from:**

`_build_explain_fields(payload, decision)` returns a dict that includes:

```python
        "close_prev_ltf": signal.get("close_prev_ltf"),
```

(around line 1945), where `signal = decision.get("signal") or {}`.

**Where `signal["close_prev_ltf"]` is set:**

**File:** `app/strategy/decision_engine.py` — inside `make_decision`, when building the `signal` dict returned in the decision:

```python
            "close_prev_ltf": close_prev or 0.0,
```

(around line 1863.) Here `close_prev` is the same in-function variable assigned from `_to_float(features_ltf.get("close_prev"))` at line 492.

**Chain:**  
- Decision engine: `close_prev = _to_float(features_ltf.get("close_prev"))` → used in P:confirm and written to signal as `"close_prev_ltf": close_prev or 0.0`.  
- run.py: `explain_fields["close_prev_ltf"] = signal.get("close_prev_ltf")` → `decision_log["prev_close"] = explain_fields.get("close_prev_ltf")`.

So the value logged as **`prev_close`** is the same value the decision engine used as **`close_prev`** for P:confirm, with one exception: when `close_prev` is `None`, the engine uses `None` in the condition (and appends P:confirm), but the signal stores `0.0` (`close_prev or 0.0`), so the log shows **0.0** for `prev_close` in that case.

---

## D) Identifier inventory table

| Identifier | File | Meaning (from code) |
|------------|------|---------------------|
| `close_prev` | `app/strategy/decision_engine.py` | Local variable: `_to_float(features_ltf.get("close_prev"))` — LTF previous candle close used for reclaim, dist50_prev, P:confirm, etc. |
| `close_prev` | `app/data/payload_builder.py` | Local: LTF close at `iloc[-2]` (previous candle close), then `features_ltf["close_prev"]`. |
| `close_prev` | `app/run.py` (in _build_explain_pullback) | Local: `signal.get("close_prev_ltf")` — used only for confirm explain (close direction vs close_ltf). |
| `close_prev_ltf` | `app/strategy/decision_engine.py` | Signal key: `close_prev or 0.0` — same as `close_prev` written for logging/explain. |
| `close_prev_ltf` | `app/run.py` | explain_fields key from `signal.get("close_prev_ltf")`; also key in explain_fields returned by _build_explain_fields. |
| `prev_close` | `app/run.py` | Key in decision_log: `explain_fields.get("close_prev_ltf")` — value logged in decision_candle. |
| `prev_close` | `app/strategy/decision_engine.py` | Docstring only: “prev_close <= ema50_ltf” / “prev_close >= ema50_ltf” (describes reclaim rule). |
| `prev_close` | `app/data/payload_builder.py` | Local (ATR/TR): `close_col.shift(1).fillna(close_col)` — shifted series for true range, not the scalar written to features_ltf. |
| `prev_close_htf` | `app/data/payload_builder.py` | Local: `htf_close_col.shift(1).fillna(htf_close_col)` — HTF previous close for HTF ATR, not LTF. |
| `close_prev` (payload key) | Tests / payload_builder | `features_ltf["close_prev"]` — payload key consumed by decision engine as `features_ltf.get("close_prev")`. |

No identifiers matching `previous_close` or `close_ltf_prev` were found in the repo.

---

## E) Verdict

**P:confirm uses `payload["features_ltf"]["close_prev"]` (after `_to_float`), and it is the same value as logged `prev_close`** — because the decision engine writes that value to `signal["close_prev_ltf"]` as `close_prev or 0.0`, and the log sets `prev_close = explain_fields.get("close_prev_ltf")`. The only mismatch is when `close_prev` is `None`: the decision correctly treats it as missing and appends P:confirm, while the log shows `prev_close = 0.0` due to the `or 0.0` in the signal. To make logs and decision fully consistent in that edge case, either log `None` (or a sentinel) when `close_prev` is `None`, or store `close_prev` in the signal without coercing None to 0.0 and let the log reflect that.
