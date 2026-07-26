#!/usr/bin/env python3
"""
JARVIS — BTC Options Skew (25-delta), источник: Deribit public API
=====================================================================
Считает 25-delta skew для BTC (put_iv - call_iv на ближайшей ~30-дневной
экспирации) и Z-score относительно собственной истории.

ВАЖНО: Deribit не отдаёт готовую историю skew — только срез рынка сейчас.
Поэтому история накапливается в файле skew_history.json в этом же
репозитории: каждый запуск дописывает сегодняшнюю точку и читает
предыдущие для Z-score. Требуется git commit-back в workflow (см. правку
.yml ниже) — иначе история будет теряться между запусками.

Пороги (из памяти JARVIS "Mark50 Section 10"):
  Z-score ±1.5σ = сигнал, ±2.0σ = критично (институциональный tail-hedge)

Зависимости: requests
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from statistics import mean, pstdev

import requests

DERIBIT_BASE = "https://www.deribit.com/api/v2"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_history.json")
HISTORY_MAX_DAYS = 90
TARGET_DELTA = 0.25
TARGET_TENOR_DAYS = 30  # ищем экспирацию ближе всего к 30 дням вперёд
TIMEOUT = 10

SESSION = requests.Session()


def safe_get(url, params=None):
    try:
        r = SESSION.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "result" not in data:
            return None, f"no result field: {data.get('error')}"
        return data["result"], None
    except Exception as e:
        return None, str(e)


def get_btc_option_instruments():
    """Список всех активных BTC-опционов (с истекающими датами)."""
    result, err = safe_get(
        f"{DERIBIT_BASE}/public/get_instruments",
        {"currency": "BTC", "kind": "option", "expired": "false"},
    )
    if err:
        return None, err
    return result, None


def pick_target_expiry(instruments):
    """Выбираем экспирацию, ближайшую к TARGET_TENOR_DAYS дням вперёд."""
    now_ms = time.time() * 1000
    target_ms = now_ms + TARGET_TENOR_DAYS * 86400 * 1000
    expiries = sorted(set(i["expiration_timestamp"] for i in instruments))
    if not expiries:
        return None
    return min(expiries, key=lambda ts: abs(ts - target_ms))


def get_index_price(index_name="btc_usd") -> float | None:
    """Реальная споты-цена через специально предназначенный эндпоинт —
    ИСПРАВЛЕНО: /public/get_instruments НЕ отдаёт underlying_price для
    опционов (это только метаданные страйк/экспирация), из-за чего v1
    ошибочно откатывался на произвольный страйк середины списка."""
    result, err = safe_get(f"{DERIBIT_BASE}/public/get_index_price", {"index_name": index_name})
    if err or not result:
        return None
    return result.get("index_price")
    result, err = safe_get(f"{DERIBIT_BASE}/public/ticker", {"instrument_name": instrument_name})
    if err:
        return None, err
    return result, None


def find_25delta_skew(currency="BTC"):
    """
    Возвращает (skew_pct, meta) или (None, error_str).
    meta содержит expiry, call/put strikes и их IV/delta — для прозрачности.
    """
    instruments, err = get_btc_option_instruments()
    if err:
        return None, f"get_instruments failed: {err}"

    target_expiry = pick_target_expiry(instruments)
    if target_expiry is None:
        return None, "no expiries found"

    expiry_instruments = [i for i in instruments if i["expiration_timestamp"] == target_expiry]
    calls = sorted([i for i in expiry_instruments if i["option_type"] == "call"], key=lambda i: i["strike"])
    puts = sorted([i for i in expiry_instruments if i["option_type"] == "put"], key=lambda i: i["strike"])

    if not calls or not puts:
        return None, "no calls/puts for target expiry"

    # Ограничиваем диапазон страйков вокруг споты, чтобы не дергать сотни тикеров:
    # берём страйки в пределах +-25% от текущей споты (делта ~0.25 обычно там).
    # ИСПРАВЛЕНО: реальная споты-цена через /public/get_index_price,
    # а не через несуществующее поле underlying_price в get_instruments
    underlying_price = get_index_price("btc_usd")
    if underlying_price is None:
        return None, "get_index_price failed — не удалось получить реальную цену BTC"
    lo, hi = underlying_price * 0.75, underlying_price * 1.25
    calls = [c for c in calls if lo <= c["strike"] <= hi]
    puts = [p for p in puts if lo <= p["strike"] <= hi]

    best_call, best_call_diff = None, None
    for c in calls:
        ticker, terr = get_ticker(c["instrument_name"])
        time.sleep(0.05)
        if terr or not ticker or "greeks" not in ticker:
            continue
        delta = ticker["greeks"].get("delta")
        if delta is None:
            continue
        diff = abs(delta - TARGET_DELTA)
        if best_call_diff is None or diff < best_call_diff:
            best_call_diff = diff
            best_call = (c["instrument_name"], ticker["mark_iv"], delta)

    best_put, best_put_diff = None, None
    for p in puts:
        ticker, terr = get_ticker(p["instrument_name"])
        time.sleep(0.05)
        if terr or not ticker or "greeks" not in ticker:
            continue
        delta = ticker["greeks"].get("delta")
        if delta is None:
            continue
        diff = abs(delta - (-TARGET_DELTA))
        if best_put_diff is None or diff < best_put_diff:
            best_put_diff = diff
            best_put = (p["instrument_name"], ticker["mark_iv"], delta)

    if not best_call or not best_put:
        return None, "could not find ~25-delta call/put (greeks unavailable)"

    call_name, call_iv, call_delta = best_call
    put_name, put_iv, put_delta = best_put
    skew = put_iv - call_iv  # положительный skew = путы дороже (страх падения)

    meta = {
        "expiry_ts": target_expiry,
        "call_instrument": call_name,
        "call_iv": call_iv,
        "call_delta": call_delta,
        "put_instrument": put_name,
        "put_iv": put_iv,
        "put_delta": put_delta,
        "underlying_price": underlying_price,
    }
    return skew, meta


# ---------------------------------------------------------------------------
# ИСТОРИЯ / Z-SCORE
# ---------------------------------------------------------------------------

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
        json.dump(history[-HISTORY_MAX_DAYS:], f, indent=2)


def append_today(history, skew_value):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = [h for h in history if h["date"] != today]  # не дублировать при повторном запуске в тот же день
    history.append({"date": today, "skew": skew_value})
    return history


def rolling_zscore(history_values, current):
    if len(history_values) < 5:
        return None
    mu = mean(history_values)
    sigma = pstdev(history_values)
    if sigma == 0:
        return None
    return (current - mu) / sigma


def classify_skew(z):
    if z is None:
        return "INSUFFICIENT_HISTORY"
    if z >= 2.0:
        return "CRITICAL_PUT_PREMIUM"  # институциональный tail-hedge, рынок закладывает падение
    if z >= 1.5:
        return "SIGNAL_PUT_PREMIUM"
    if z <= -2.0:
        return "CRITICAL_CALL_PREMIUM"
    if z <= -1.5:
        return "SIGNAL_CALL_PREMIUM"
    return "NORMAL"


def main():
    skew, meta = find_25delta_skew("BTC")
    result = {
        "asset": "BTC",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if skew is None:
        result["error"] = meta  # meta — строка с ошибкой в этом случае
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    history = load_history()
    prior_values = [h["skew"] for h in history]  # история ДО сегодняшней точки
    z = rolling_zscore(prior_values, skew)

    history = append_today(history, skew)
    save_history(history)

    result.update({
        "skew_pct": round(skew, 3),
        "zscore": round(z, 2) if z is not None else None,
        "classification": classify_skew(z),
        "history_points": len(prior_values),
        "meta": meta,
    })
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
