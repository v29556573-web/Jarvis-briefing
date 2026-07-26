#!/usr/bin/env python3
"""
JARVIS — CME-OKX BTC Premium/Discount Spread
===============================================
CME-сторона: yfinance (BTC=F), задержка ~10-15 мин, что не критично для
часового/дневного анализа спреда.
OKX-сторона: BTC-USDT-SWAP, уже используется в derivatives_block_v1.py.

Пороги (Gemini, 26.07.2026, подтверждены как не требующие перекалибровки
при замене Binance на OKX): норма 0.1-0.3%, red flag >1.0% на протяжении
3-4 часовых свечей подряд (не разовый всплеск 15-30 минут).

ИЗВЕСТНЫЙ РИСК: yfinance парсит недокументированный API Yahoo Finance —
он менее агрессивно блокирует облачные IP, чем Binance, но это не
гарантия. Первый живой прогон на GitHub Actions покажет, работает ли
это надёжно; если Yahoo забанит раннер — нужен fallback-источник
(например, investing.com или сам CME's публичный delayed quote).

Зависимости: requests, yfinance
    pip install requests yfinance --break-system-packages
"""

import json
import os
from datetime import datetime, timezone
from statistics import mean

import requests

try:
    import yfinance as yf
except ImportError:
    yf = None

OKX_BASE = "https://www.okx.com"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cme_okx_spread_history.json")
HISTORY_MAX_POINTS = 200  # ~4 свечи/час * 3-4 часа запаса на разных запусках
REDFLAG_THRESHOLD = 1.0
NORMAL_RANGE = (0.1, 0.3)
CONSEC_CANDLES_FOR_REDFLAG = 3  # red flag только если спред держится N запусков подряд
TIMEOUT = 10


def get_okx_btc_price() -> float | None:
    try:
        r = requests.get(f"{OKX_BASE}/api/v5/market/ticker", params={"instId": "BTC-USDT-SWAP"}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "0" or not data.get("data"):
            return None
        return float(data["data"][0]["last"])
    except Exception:
        return None


def get_cme_btc_price() -> tuple[float | None, str | None]:
    if yf is None:
        return None, "yfinance не установлен"
    try:
        ticker = yf.Ticker("BTC=F")
        hist = ticker.history(period="1d")
        if hist.empty:
            return None, "yfinance вернул пустые данные"
        return float(hist["Close"].iloc[-1]), None
    except Exception as e:
        return None, str(e)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[-HISTORY_MAX_POINTS:], f, indent=2)


def classify_spread(spread_pct, history):
    """
    spread_pct положительный = CME дороже OKX (премия), отрицательный = дисконт.
    Red flag только если |spread| > 1.0% держится N последних точек подряд
    (устраняет ложные срабатывания на 15-30-минутных всплесках).
    """
    recent = history[-(CONSEC_CANDLES_FOR_REDFLAG - 1):] + [{"spread_pct": spread_pct}]
    values = [h["spread_pct"] for h in recent]

    if len(values) >= CONSEC_CANDLES_FOR_REDFLAG and all(abs(v) > REDFLAG_THRESHOLD for v in values):
        direction = "CME_PREMIUM" if spread_pct > 0 else "CME_DISCOUNT"
        return f"RED_FLAG_{direction}"
    if NORMAL_RANGE[0] <= abs(spread_pct) <= NORMAL_RANGE[1]:
        return "NORMAL"
    return "ELEVATED_WATCH"  # вышел из нормы, но ещё не подтверждён как red flag


def main():
    result = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    okx_price = get_okx_btc_price()
    cme_price, cme_err = get_cme_btc_price()

    if okx_price is None:
        result["error"] = "OKX price fetch failed"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if cme_price is None:
        result["error"] = f"CME price fetch failed: {cme_err}"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    spread_pct = (cme_price - okx_price) / okx_price * 100
    history = load_history()
    classification = classify_spread(spread_pct, history)

    history.append({
        "ts_utc": result["generated_at_utc"],
        "cme_price": cme_price,
        "okx_price": okx_price,
        "spread_pct": round(spread_pct, 4),
    })
    save_history(history)

    result.update({
        "cme_price": cme_price,
        "okx_price": okx_price,
        "spread_pct": round(spread_pct, 4),
        "classification": classification,
        "note": "CME-цена с задержкой 10-15 мин (yfinance); для 1H/1D анализа некритично.",
    })
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
