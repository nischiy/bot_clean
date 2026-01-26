# -*- coding: utf-8 -*-
from __future__ import annotations

"""
MarketData (HTTP, без сторонніх залежностей).

Призначення:
  - Надати історичні свічки (klines) і поточну ціну з Binance REST.
  - Працює як для SPOT, так і для FUTURES (шлях обирається за base_url).
  - Стабільна схема колонок DataFrame та коректні типи.
  - Ретраї з backoff, обробка 429/5xx, таймаути.

НЕ робить:
  - Жодних хотпатчів класів.
  - Жодних динамічних імпортів “core.*”.
  - WebSocket-стрімів (тільки REST).

Публічний API:
  class HttpMarketData:
      def __init__(self, base_url: str = "https://fapi.binance.com", *, timeout: int = 15, max_retries: int = 5, logger=None)
      def get_klines(self, symbol: str, interval: str, limit: int = 1000, *, start_time: int | None = None, end_time: int | None = None, max_bars: int | None = None) -> pd.DataFrame
      def get_latest_price(self, symbol: str) -> float
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlencode

import requests

import pandas as pd

# ---- Константи та валідація інтервалів ----

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

# Валідні інтервали (офіційні Binance)
_VALID_INTERVALS = {
    "1s", "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M"
}

# Максимальний ліміт за 1 запит у Binance
_MAX_LIMIT = 1500  # SPOT/FUTURES дозволяють 1000/1500 для різних ендпойнтів, беремо 1500 з перестраховкою


@dataclass(frozen=True)
class _Endpoints:
    klines_path: str
    price_path: str


def _endpoints_for_base(base_url: str) -> _Endpoints:
    """
    Вибирає шляхи API залежно від базового URL.
    Якщо у base_url є 'fapi' => FUTURES, інакше => SPOT.
    """
    u = (base_url or "").lower()
    if "fapi" in u:
        return _Endpoints(klines_path="/fapi/v1/klines", price_path="/fapi/v1/ticker/price")
    return _Endpoints(klines_path="/api/v3/klines", price_path="/api/v3/ticker/price")


# ---- HTTP утиліти з ретраями ----

def _http_get(url: str, params: Dict[str, Any], *, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> str:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    resp = requests.get(full_url, timeout=timeout)
    resp.raise_for_status()
    if hasattr(resp, "text"):
        return resp.text
    if hasattr(resp, "json"):
        return json.dumps(resp.json())
    return str(resp)

def _http_get_json(url: str, params: Dict[str, Any], *, timeout: int, max_retries: int, log: logging.Logger) -> Tuple[Any, Dict[str, str]]:
    """
    Виконує GET із ретраями (експоненційний backoff).
    Повертає (parsed_json, headers_dict).
    """
    headers = {"User-Agent": "almost-bot/1.0"}
    query = urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{query}" if query else url

    delay = 0.5
    for attempt in range(max_retries + 1):
        try:
            raw = _http_get(url, params, headers=headers, timeout=timeout)
            return json.loads(raw), {}
        except requests.HTTPError as e:
            # Обробка 429/5xx з паузами
            status = e.response.status_code if e.response is not None else 0
            retry_after = 0.0
            try:
                if e.response is not None:
                    ra = e.response.headers.get("Retry-After")
                    if ra:
                        retry_after = float(ra)
            except Exception:
                retry_after = 0.0

            if status in (429, 418, 500, 502, 503, 504) and attempt < max_retries:
                sleep_for = max(retry_after, delay)
                log.warning("HTTP %s on %s, retry in %.2fs (attempt %d/%d)", status, full_url, sleep_for, attempt + 1, max_retries)
                time.sleep(sleep_for)
                delay = min(delay * 2, 8.0)
                continue

            # Безпечна спроба прочитати тіло для діагностики
            try:
                raw = e.response.text if e.response is not None else ""
            except Exception:
                raw = ""
            log.error("HTTP error %s on %s: %s", status, full_url, raw.strip()[:300])
            raise
        except requests.RequestException as e:
            if attempt < max_retries:
                log.warning("Network error on %s: %s, retry in %.2fs (attempt %d/%d)", full_url, e, delay, attempt + 1, max_retries)
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            log.error("Network error on %s: %s (no more retries)", full_url, e)
            raise
        except Exception as e:
            log.error("Unexpected error on %s: %s", full_url, e, exc_info=True)
            raise

    # Теоретично недосяжно: цикл вийде раніше або кине виключення
    raise RuntimeError("GET failed with retries exhausted")


# ---- Перетворення у стабільний DataFrame ----

def _as_dataframe_klines(raw: List[List[Any]]) -> pd.DataFrame:
    """
    Приймає сирий масив масивів Binance і повертає DataFrame з **стабільною** схемою колонок:
      ['time','open','high','low','close','volume','open_time','close_time',
       'quote_asset_volume','number_of_trades','taker_buy_base_asset_volume','taker_buy_quote_asset_volume']
    - 'time' == 'open_time' (UTC)
    - числові поля приведені до float/int
    """
    cols_full = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","number_of_trades",
        "taker_buy_base_asset_volume","taker_buy_quote_asset_volume","ignore"
    ]
    if not isinstance(raw, list) or not raw:
        raise ValueError("empty klines payload")

    df = pd.DataFrame(raw, columns=cols_full)
    # Приводимо до числових типів
    to_num = ["open", "high", "low", "close", "volume", "quote_asset_volume",
              "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume",
              "open_time", "close_time", "number_of_trades"]
    for c in to_num:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Епоха мс → datetime UTC; зберігаємо і open_time/close_time, і додаємо 'time'
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df.insert(0, "time", df["open_time"])

    # Повертаємо тільки стабільні, корисні колонки (без 'ignore')
    want = ["time", "open", "high", "low", "close", "volume",
            "open_time", "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]
    return df[want]


# ---- Основний клас ----

class HttpMarketData:
    """
    Легкий HTTP адаптер без залежності від `requests`.

    Параметри:
      base_url: SPOT чи FUTURES. За замовчуванням FUTURES (fapi).
      timeout:  таймаут одного запиту (сек).
      max_retries: скільки разів повторювати при 429/5xx/мережевих помилках.
      logger: існуючий логер або None (тоді створиться 'MarketData').
    """
    def __init__(self, base_url: str = FUTURES_BASE, *, timeout: int = 15, max_retries: int = 5, logger: Optional[logging.Logger] = None) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.timeout: int = int(timeout)
        self.max_retries: int = int(max_retries)
        self.log = logger or logging.getLogger("MarketData")
        self._endpoints = _endpoints_for_base(self.base_url)

    # ---- Публічні методи ----

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 1000,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        max_bars: int | None = None,
    ) -> pd.DataFrame:
        """
        Отримати свічки (klines) як DataFrame зі стабільною схемою колонок.
        - symbol (str): 'BTCUSDT' тощо; буде upper().
        - interval (str): один з Binance інтервалів (наприклад, '1m', '1h', '1d').
        - limit (int): до _MAX_LIMIT за один запит (1500). Binance часто дає ≤1000.
        - start_time/end_time (ms epoch): опційний фільтр у Binance API.
        - max_bars: якщо потрібно більше за ліміт — завантажуємо порціями до max_bars.

        Повертає: pandas.DataFrame (див. _as_dataframe_klines()).
        """
        sym = (symbol or "").upper().strip()
        if not sym:
            raise ValueError("symbol must be non-empty")

        itv = (interval or "").strip()
        if itv not in _VALID_INTERVALS:
            raise ValueError(f"invalid interval: {interval!r}")

        # Поважаємо обмеження ліміту на запит
        per_call = max(1, min(int(limit), _MAX_LIMIT))

        url = f"{self.base_url}{self._endpoints.klines_path}"

        def fetch_once(_limit: int, _start: Optional[int], _end: Optional[int]) -> List[List[Any]]:
            payload = {
                "symbol": sym,
                "interval": itv,
                "limit": int(_limit),
                "startTime": int(_start) if _start is not None else None,
                "endTime": int(_end) if _end is not None else None,
            }
            data, _hdrs = _http_get_json(url, payload, timeout=self.timeout, max_retries=self.max_retries, log=self.log)
            if not isinstance(data, list):
                raise RuntimeError(f"unexpected klines payload type: {type(data)}")
            return data

        # Якщо потрібно <= per_call — один запит
        if not max_bars or max_bars <= per_call:
            raw = fetch_once(per_call, start_time, end_time)
            return _as_dataframe_klines(raw)

        # Інакше — пагінація порціями
        all_rows: List[List[Any]] = []
        fetched = 0
        next_start = start_time
        # Якщо start_time не заданий — беремо останні max_bars (кроками назад через end_time)
        # Але, щоб не ускладнювати: коли немає start_time, просто робимо послідовні запити назад за endTime.
        local_end = end_time

        while fetched < max_bars:
            want = min(per_call, max_bars - fetched)
            raw = fetch_once(want, next_start, local_end)
            if not raw:
                break
            all_rows.extend(raw)
            fetched += len(raw)

            # На Binance kline: raw[i][6] = closeTime (ms). Зсуваємо вікно.
            last_close = raw[-1][6]
            # Щоб уникнути перетину, наступний startTime = last_close + 1
            next_start = int(last_close) + 1

            # Якщо масив повернувся меншим від запитаного — більше даних немає
            if len(raw) < want:
                break

        if not all_rows:
            raise RuntimeError("no klines returned")

        return _as_dataframe_klines(all_rows[:max_bars])

    def get_latest_price(self, symbol: str) -> float:
        """
        Повертає останню ціну інструмента (float).
        """
        sym = (symbol or "").upper().strip()
        if not sym:
            raise ValueError("symbol must be non-empty")

        url = f"{self.base_url}{self._endpoints.price_path}"
        data, _hdrs = _http_get_json(url, {"symbol": sym}, timeout=self.timeout, max_retries=self.max_retries, log=self.log)

        # Binance повертає {"symbol":"BTCUSDT","price":"12345.67"}
        if isinstance(data, dict) and "price" in data:
            try:
                return float(data["price"])
            except Exception as e:
                raise RuntimeError(f"cannot parse price: {data!r}") from e

        # Якщо раптом масив — шукаємо наш символ
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("symbol") == sym and "price" in item:
                    return float(item["price"])
        raise RuntimeError(f"unexpected price payload: {data!r}")
