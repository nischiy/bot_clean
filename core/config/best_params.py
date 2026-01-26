from __future__ import annotations

# Minimal, explicit defaults for our EMA/RSI/ATR + SL/TP
BEST_PARAMS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "rsi_period": 14,
    "rsi_buy": 35,
    "rsi_sell": 65,
    "atr_period": 14,
    "sl_atr": 1.5,
    "tp_atr": 2.0,
    "qty": 0.001,
}

def get_best_params():
    """Return a copy of defaults. Safe to mutate by caller."""
    return dict(BEST_PARAMS)
