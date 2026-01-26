
from __future__ import annotations
from typing import Dict, Any
import pandas as pd

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _rsi(close: pd.Series, period: int=14) -> pd.Series:
    delta = close.diff()
    up = (delta.where(delta > 0, 0.0)).rolling(period).mean()
    down = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = up / (down.replace(0, 1e-9))
    return 100.0 - (100.0 / (1.0 + rs))

def _atr(df: pd.DataFrame, period: int=14) -> pd.Series:
    high = df['high'] if 'high' in df else df['High']
    low = df['low'] if 'low' in df else df['Low']
    close = df['close'] if 'close' in df else df['Close']
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def generate_signal(df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
    """EMA/RSI/ATR стратегія з розрахунком SL/TP.
    Повертає decision dict з ключами принаймні: action, price, qty (якщо задано), sl/tp (для LONG/SHORT).
    """
    if df is None or df.empty:
        return {"action": "HOLD", "reason": "empty_df"}

    close = df['close'] if 'close' in df else df['Close']

    ema_fast = int(params.get('ema_fast', 20))
    ema_slow = int(params.get('ema_slow', 50))
    rsi_p = int(params.get('rsi_period', 14))
    rsi_lo = float(params.get('rsi_buy', 35))
    rsi_hi = float(params.get('rsi_sell', 65))
    atr_p = int(params.get('atr_period', 14))
    sl_k = float(params.get('sl_atr', 1.5))
    tp_k = float(params.get('tp_atr', 2.0))

    e_fast = _ema(close, ema_fast)
    e_slow = _ema(close, ema_slow)
    rsi = _rsi(close, rsi_p)
    atr = _atr(df, atr_p)

    reasons = []
    if e_fast.iloc[-1] > e_slow.iloc[-1]:
        reasons.append("ema_fast>ema_slow")
    else:
        reasons.append("ema_fast<=ema_slow")
    if rsi.iloc[-1] > rsi_hi:
        reasons.append("rsi>rsi_sell")
    elif rsi.iloc[-1] < rsi_lo:
        reasons.append("rsi<rsi_buy")
    else:
        reasons.append("rsi_in_range")

    if e_fast.iloc[-1] > e_slow.iloc[-1] and rsi.iloc[-1] > rsi_hi:
        side = "BUY"
    elif e_fast.iloc[-1] < e_slow.iloc[-1] and rsi.iloc[-1] < rsi_lo:
        side = "SELL"
    else:
        side = "HOLD"

    price = float(close.iloc[-1])
    qty = float(params.get('qty', params.get('base_qty', 0.001)))

    decision = {
        "action": side,
        "side": side,
        "price": price,
        "qty": qty,
        "ema_fast": float(e_fast.iloc[-1]),
        "ema_slow": float(e_slow.iloc[-1]),
        "rsi": float(rsi.iloc[-1]),
        "atr": float(atr.iloc[-1]) if not atr.empty else None,
        "indicators": {
            "ema_fast": float(e_fast.iloc[-1]),
            "ema_slow": float(e_slow.iloc[-1]),
            "rsi": float(rsi.iloc[-1]),
            "atr": float(atr.iloc[-1]) if not atr.empty else None,
        },
        "reasons": reasons,
        "reason": "; ".join(reasons),
    }

    last_atr = decision["atr"]
    if last_atr and side in ("BUY", "SELL"):
        if side == "BUY":
            decision["sl"] = price - sl_k * last_atr
            decision["tp"] = price + tp_k * last_atr
        else:
            decision["sl"] = price + sl_k * last_atr
            decision["tp"] = price - tp_k * last_atr

    return decision


# === AUTO-ADAPTER START (generated) ===
# Provides a unified interface expected by the runner/tests.
# Exposes: class Strategy.decide(*args, **kwargs), decide(), signal()
class Strategy:
    """Unified interface for the trading runner. Generated adapter."""
    @staticmethod
    def decide(*args, **kwargs):
        return generate_signal(*args, **kwargs)
def decide(*args, **kwargs):
    return generate_signal(*args, **kwargs)
def signal(*args, **kwargs):
    # Alias for decide()
    return decide(*args, **kwargs)# === AUTO-ADAPTER END ===


# === OHLC NORMALIZER START (generated) ===
try:
    import pandas as pd  # ensure pd available
except Exception:
    pass

def _pick_col(df, *cands):
    # Try exact names
    for name in cands:
        if name in df:
            return df[name]
    # Try case-insensitive
    lower = {str(c).lower(): c for c in getattr(df, "columns", [])}
    for name in cands:
        key = str(name).lower()
        if key in lower:
            return df[lower[key]]
    return None

def _ensure_ohlc(df):
    close = _pick_col(df, "close","Close","c","C","price","Price","last","Last")
    if close is None:
        raise KeyError("close/Close not found in DataFrame columns")
    high = _pick_col(df, "high","High","h","H","HighPrice","max","Max")
    low  = _pick_col(df, "low","Low","l","L","LowPrice","min","Min")
    if high is None:
        high = close
    if low is None:
        low = close
    return high, low, close

def _atr(df, period):
    """
    Robust ATR: supports various OHLC column namings; if only 'close' exists,
    ATR reduces to a moving mean of |delta close| (valid fallback for smoke tests).
    """
    high, low, close = _ensure_ohlc(df)
    prev_close = close.shift(1).fillna(close)
    tr_components = [
        (high - low).abs(),
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ]
    tr = pd.concat(tr_components, axis=1).max(axis=1)
    return tr.rolling(int(period), min_periods=1).mean()
# === OHLC NORMALIZER END ===
