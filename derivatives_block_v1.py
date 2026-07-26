#!/usr/bin/env python3
"""
JARVIS — Mark50 Section 10: Derivatives / Positioning Block (v2)
==================================================================
v2 vs v1: полностью переведён на OKX вместо Binance.

ПРИЧИНА: тест v1 на GitHub Actions (26.07.2026) показал, что все запросы
к fapi.binance.com/dapi.binance.com/api.binance.com возвращают пустые
данные — Binance геоблокирует IP-диапазоны облачных провайдеров
(тот же паттерн, что уже был у JARVIS с funding-коллектором на Binance/
Bybit — см. память). OKX прошёл тест успешно (top_trader_ls_ratio_okx
вернулся корректно), поэтому вся сборка данных переведена на OKX.

Также исправлена опечатка в Hyperliquid payload: правильный type —
"metaAndAssetCtxs" (а не "metaAndAssetContexts", из-за которой v1 получил
422 Unprocessable Entity).

Методология (пороги OI/funding/skew, EWMA/Z-score, сценарии A/Б) —
без изменений, см. память JARVIS "Mark50 Section 10" (16.07.2026).

Зависимости: requests
    pip install requests --break-system-packages

ВАЖНО: не тестировался вживую в песочнице Claude (сетевые ограничения
не пускают на okx.com/hyperliquid.xyz). Проверено только логически по
документации OKX/Hyperliquid — первый реальный прогон подтвердит,
что и OKX не заблокирован для остальных инструментов (тест был только
на BTC/ETH через long-short-ratio, не на все эндпоинты).
"""

import json
import sys
import time
from datetime import datetime, timezone
from statistics import mean, pstdev

import requests

# ---------------------------------------------------------------------------
# КОНФИГ: тиры активов и пороги (из памяти JARVIS, без изменений)
# ---------------------------------------------------------------------------

MAJORS = {"BTC", "ETH", "SOL"}
ALT_STACK_ONLY = {"HYPE", "ONDO", "LINK"}  # нет ликвидных опционов, skew неприменим

OI_THRESHOLD = {"major": (8.0, 10.0), "alt": (25.0, 35.0)}
FUNDING_LONG_SQUEEZE = {"major": 0.05, "alt": 0.12}
FUNDING_SHORT_SQUEEZE = {"major": -0.05, "alt": -0.10}
SPOT_PERP_RATIO_WARN = 0.15
CME_BASIS_NORMAL = (0.1, 0.3)
CME_BASIS_REDFLAG = 1.0

OKX_BASE = "https://www.okx.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

TIMEOUT = 10
SESSION = requests.Session()


def tier_of(symbol: str) -> str:
    return "major" if symbol in MAJORS else "alt"


def safe_get(url, params=None, method="GET", json_body=None):
    """Обёртка над requests с обработкой ошибок — не роняет весь скрипт."""
    try:
        if method == "POST":
            r = SESSION.post(url, json=json_body, timeout=TIMEOUT)
        else:
            r = SESSION.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


# ---------------------------------------------------------------------------
# СБОР ДАННЫХ — OKX
# ---------------------------------------------------------------------------

def okx_data_rows(payload):
    """OKX всегда оборачивает ответ в {"code": "0", "data": [...]}."""
    if isinstance(payload, dict) and "_error" in payload:
        return None
    if not isinstance(payload, dict) or payload.get("code") != "0":
        return None
    return payload.get("data")


def get_oi_current(inst_id: str) -> float | None:
    """Текущий Open Interest (в контрактах/монетах в зависимости от инструмента)."""
    resp = safe_get(f"{OKX_BASE}/api/v5/public/open-interest", {"instId": inst_id})
    rows = okx_data_rows(resp)
    if not rows:
        return None
    return float(rows[0]["oi"])


def get_oi_history(ccy: str, days: int = 30) -> list[float]:
    """Исторический ряд OI (дневные точки, в USD) через rubik-эндпоинт, для Z-score."""
    resp = safe_get(
        f"{OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume",
        {"ccy": ccy, "period": "1D"},
    )
    rows = okx_data_rows(resp)
    if not rows:
        return []
    # формат строки: [ts, oi(usd), vol(usd)], от новых к старым
    rows = rows[:days]
    return [float(r[1]) for r in reversed(rows)]


def get_funding_now(inst_id: str) -> float | None:
    resp = safe_get(f"{OKX_BASE}/api/v5/public/funding-rate", {"instId": inst_id})
    rows = okx_data_rows(resp)
    if not rows:
        return None
    return float(rows[0]["fundingRate"]) * 100  # в %


def get_funding_history(inst_id: str, limit: int = 42) -> list[float]:
    """История funding (каждые 8ч, ~42 записи = 14 дней) для EWMA."""
    resp = safe_get(
        f"{OKX_BASE}/api/v5/public/funding-rate-history",
        {"instId": inst_id, "limit": limit},
    )
    rows = okx_data_rows(resp)
    if not rows:
        return []
    return [float(r["fundingRate"]) * 100 for r in reversed(rows)]  # от старых к новым


def get_ticker_vol(inst_id: str) -> float | None:
    """24ч объём в quote-валюте (USDT) для инструмента (спот или своп)."""
    resp = safe_get(f"{OKX_BASE}/api/v5/market/ticker", {"instId": inst_id})
    rows = okx_data_rows(resp)
    if not rows:
        return None
    return float(rows[0]["volCcy24h"])


def get_top_trader_ratio_okx(ccy: str) -> float | None:
    resp = safe_get(
        f"{OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio",
        {"ccy": ccy},
    )
    rows = okx_data_rows(resp)
    if not rows:
        return None
    return float(rows[0][1])  # [timestamp, ratio]


def get_hyperliquid_asset(coin: str = "HYPE") -> dict:
    """OI/funding/markPrice для токена на Hyperliquid.
    ИСПРАВЛЕНО в v2: type = "metaAndAssetCtxs" (не "metaAndAssetContexts")."""
    data = safe_get(HYPERLIQUID_API, method="POST", json_body={"type": "metaAndAssetCtxs"})
    if isinstance(data, dict) and "_error" in data:
        return {"_error": data["_error"]}
    try:
        universe = data[0]["universe"]
        contexts = data[1]
        idx = next(i for i, a in enumerate(universe) if a["name"] == coin)
        ctx = contexts[idx]
        return {
            "openInterest": float(ctx.get("openInterest", 0)),
            "funding": float(ctx.get("funding", 0)) * 100,
            "markPrice": float(ctx.get("markPx", 0)),
        }
    except Exception as e:
        return {"_error": f"parse_failed: {e}"}


# ---------------------------------------------------------------------------
# РАСЧЁТЫ: Z-SCORE / EWMA / КЛАССИФИКАЦИЯ (без изменений от v1)
# ---------------------------------------------------------------------------

def rolling_zscore(history: list[float], current: float) -> float | None:
    if len(history) < 5:
        return None
    mu = mean(history)
    sigma = pstdev(history)
    if sigma == 0:
        return None
    return (current - mu) / sigma


def ewma(values: list[float], span_periods: int = 42) -> float | None:
    if not values:
        return None
    alpha = 2 / (span_periods + 1)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def oi_pct_change_24h(history: list[float], current: float) -> float | None:
    if not history:
        return None
    ref = history[-1]
    if ref == 0:
        return None
    return (current - ref) / ref * 100


def classify_scenario(symbol, oi_change_pct, funding_now, spot_vol, perp_vol):
    if oi_change_pct is None or funding_now is None:
        return {"scenario": "INSUFFICIENT_DATA", "note": "OI или funding не получены"}

    tier = tier_of(symbol)
    soft, hard = OI_THRESHOLD[tier]
    perp_over_spot = (spot_vol and perp_vol and perp_vol > spot_vol)

    if oi_change_pct >= hard and funding_now > FUNDING_LONG_SQUEEZE[tier] and perp_over_spot:
        return {
            "scenario": "B_SQUEEZE_SETUP",
            "note": (
                f"OI +{oi_change_pct:.1f}% (порог {hard}%), funding {funding_now:.3f}%/8ч "
                f"> порога {FUNDING_LONG_SQUEEZE[tier]}%, perp-объём доминирует над спотом."
            ),
        }
    if soft * 0.5 <= oi_change_pct < hard and abs(funding_now) <= FUNDING_LONG_SQUEEZE[tier] * 0.8:
        return {
            "scenario": "A_HEALTHY_TREND",
            "note": f"OI +{oi_change_pct:.1f}% плавно, funding {funding_now:.3f}%/8ч в норме.",
        }
    if funding_now <= FUNDING_SHORT_SQUEEZE[tier]:
        return {
            "scenario": "SHORT_SQUEEZE_RISK",
            "note": f"Funding {funding_now:.3f}%/8ч глубоко отрицательный.",
        }
    return {"scenario": "NEUTRAL", "note": "Нет однозначного паттерна."}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

# symbol -> (OKX ccy, OKX USDT-swap instId, OKX spot instId, OKX coin-margined instId или None)
ASSET_MAP = {
    "BTC": ("BTC", "BTC-USDT-SWAP", "BTC-USDT", "BTC-USD-SWAP"),
    "ETH": ("ETH", "ETH-USDT-SWAP", "ETH-USDT", "ETH-USD-SWAP"),
    "SOL": ("SOL", "SOL-USDT-SWAP", "SOL-USDT", "SOL-USD-SWAP"),
    "LINK": ("LINK", "LINK-USDT-SWAP", "LINK-USDT", None),
    "ONDO": ("ONDO", "ONDO-USDT-SWAP", "ONDO-USDT", None),
    "AVAX": ("AVAX", "AVAX-USDT-SWAP", "AVAX-USDT", None),
    "XRP": ("XRP", "XRP-USDT-SWAP", "XRP-USDT", None),
    "SUI": ("SUI", "SUI-USDT-SWAP", "SUI-USDT", None),
    "TIA": ("TIA", "TIA-USDT-SWAP", "TIA-USDT", None),
    "IMX": ("IMX", "IMX-USDT-SWAP", "IMX-USDT", None),
    "LTC": ("LTC", "LTC-USDT-SWAP", "LTC-USDT", None),
    "BCH": ("BCH", "BCH-USDT-SWAP", "BCH-USDT", None),
    "NEAR": ("NEAR", "NEAR-USDT-SWAP", "NEAR-USDT", None),
    "RENDER": ("RENDER", "RENDER-USDT-SWAP", "RENDER-USDT", None),
    "WLD": ("WLD", "WLD-USDT-SWAP", "WLD-USDT", None),
    "XLM": ("XLM", "XLM-USDT-SWAP", "XLM-USDT", None),
    "ZEC": ("ZEC", "ZEC-USDT-SWAP", "ZEC-USDT", None),
    # AKT, AR — как правило отсутствуют на OKX деривативах, добавить вручную при наличии
}
HYPERLIQUID_NATIVE = {"HYPE"}


def build_asset_block(symbol: str) -> dict:
    if symbol in HYPERLIQUID_NATIVE:
        hl = get_hyperliquid_asset(symbol)
        if "_error" in hl:
            return {"symbol": symbol, "source": "hyperliquid", "error": hl["_error"]}
        return {
            "symbol": symbol,
            "source": "hyperliquid_native",
            "openInterest_tokens": hl["openInterest"],
            "funding_pct_8h": hl["funding"],
            "markPrice": hl["markPrice"],
            "note": "USDC-only маржа — coin-margined squeeze-риск (Сценарий Б) неприменим нативно.",
        }

    mapping = ASSET_MAP.get(symbol)
    if not mapping:
        return {"symbol": symbol, "error": "нет пары в ASSET_MAP — требует добавления вручную"}

    ccy, swap_id, spot_id, coinm_id = mapping
    tier = tier_of(symbol)

    oi_now = get_oi_current(swap_id)
    oi_hist = get_oi_history(ccy, days=30)
    oi_change = oi_pct_change_24h(oi_hist, oi_now) if oi_now is not None else None
    oi_z = rolling_zscore(oi_hist, oi_now) if oi_now is not None else None

    funding_now = get_funding_now(swap_id)
    funding_hist = get_funding_history(swap_id)
    funding_ewma_val = ewma(funding_hist) if funding_hist else None

    spot_vol = get_ticker_vol(spot_id)
    perp_vol = get_ticker_vol(swap_id)

    top_trader_okx = get_top_trader_ratio_okx(ccy)
    oi_coinm = get_oi_current(coinm_id) if coinm_id else None

    scenario = classify_scenario(symbol, oi_change, funding_now, spot_vol, perp_vol)

    spot_perp_ratio = (spot_vol / perp_vol) if (spot_vol and perp_vol) else None

    return {
        "symbol": symbol,
        "tier": tier,
        "source": "okx",
        "oi_current": oi_now,
        "oi_change_24h_pct": round(oi_change, 2) if oi_change is not None else None,
        "oi_zscore_30d": round(oi_z, 2) if oi_z is not None else None,
        "oi_coinm_current": oi_coinm,
        "funding_now_pct_8h": round(funding_now, 4) if funding_now is not None else None,
        "funding_ewma_14d_pct": round(funding_ewma_val, 4) if funding_ewma_val is not None else None,
        "spot_perp_volume_ratio": round(spot_perp_ratio, 3) if spot_perp_ratio is not None else None,
        "spot_perp_dominated_warning": (spot_perp_ratio is not None and spot_perp_ratio < SPOT_PERP_RATIO_WARN),
        "top_trader_ls_ratio_okx": top_trader_okx,
        "options_skew_applicable": symbol not in ALT_STACK_ONLY,
        **scenario,
    }


def main():
    assets = sys.argv[1:] if len(sys.argv) > 1 else list(ASSET_MAP.keys()) + list(HYPERLIQUID_NATIVE)
    result = {
        "section": "Mark50_Section10_Derivatives_Positioning",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets": [],
    }
    for sym in assets:
        result["assets"].append(build_asset_block(sym))
        time.sleep(0.15)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
