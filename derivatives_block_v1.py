#!/usr/bin/env python3
"""
JARVIS — Mark50 Section 10: Derivatives / Positioning Block
=============================================================
Собирает OI, funding rate, spot-to-perp ratio, top-trader L/S ratio
по core asset list, считает rolling Z-score (OI) и EWMA (funding),
классифицирует Сценарий A (здоровый тренд) / Сценарий B (long-squeeze setup)
и выдаёт готовый JSON-блок для передачи в DeepSeek locked-data режим.

Методология и пороги — см. память JARVIS "Mark50 Section 10" (16.07.2026,
4-раундовый разбор с Gemini, эндпоинты сверены с developers.binance.com).

Зависимости: requests
    pip install requests --break-system-packages

ВАЖНО: не тестировался вживую в песочнице Claude (сетевые ограничения
не пускают на binance.com/okx.com/hyperliquid.xyz). Запусти локально
или в своём GitHub Actions окружении и проверь реальный вывод перед
встраиванием в пайплайн.
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from statistics import mean, pstdev

import requests

# ---------------------------------------------------------------------------
# КОНФИГ: тиры активов и пороги (из памяти JARVIS)
# ---------------------------------------------------------------------------

MAJORS = {"BTC", "ETH", "SOL"}
ALT_STACK_ONLY = {"HYPE", "ONDO", "LINK"}  # нет ликвидных опционов, skew неприменим

# OI anomaly thresholds (% change за 24ч при флэте цены ±1%)
OI_THRESHOLD = {
    "major": (8.0, 10.0),      # (soft, hard)
    "alt": (25.0, 35.0),
}

# Funding rate thresholds (% за 8ч)
FUNDING_LONG_SQUEEZE = {"major": 0.05, "alt": 0.12}
FUNDING_SHORT_SQUEEZE = {"major": -0.05, "alt": -0.10}

# Spot-to-Perp Volume Ratio — ниже этого = деривативы доминируют
SPOT_PERP_RATIO_WARN = 0.15

# On-chain netflow как % от дневного объёма — триггер выхода для low-cap
NETFLOW_EXIT_TRIGGER_PCT = 0.5

# CME-Binance premium/discount (% отклонения)
CME_BASIS_NORMAL = (0.1, 0.3)
CME_BASIS_REDFLAG = 1.0

BINANCE_FAPI = "https://fapi.binance.com"   # USDT-M futures
BINANCE_DAPI = "https://dapi.binance.com"   # Coin-M futures
BINANCE_SPOT = "https://api.binance.com"
OKX_API = "https://www.okx.com"
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
# СБОР ДАННЫХ
# ---------------------------------------------------------------------------

def get_oi_current(symbol_usdt: str) -> float | None:
    """Текущий Open Interest, USDT-margined контракт (в монетах)."""
    data = safe_get(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": symbol_usdt})
    if "_error" in data:
        return None
    return float(data.get("openInterest", 0))


def get_oi_history(symbol_usdt: str, days: int = 30) -> list[float]:
    """Исторический ряд OI (дневные точки) для rolling Z-score."""
    data = safe_get(
        f"{BINANCE_FAPI}/futures/data/openInterestHist",
        {"symbol": symbol_usdt, "period": "1d", "limit": days},
    )
    if isinstance(data, dict) and "_error" in data:
        return []
    return [float(d["sumOpenInterest"]) for d in data]


def get_oi_coinm(symbol_pair: str) -> float | None:
    """Coin-margined (инверсный) OI — только для активов, где есть COIN-M рынок
    (на практике: BTC, ETH; для альтов из core-листа обычно недоступен)."""
    data = safe_get(f"{BINANCE_DAPI}/dapi/v1/openInterest", {"symbol": symbol_pair})
    if "_error" in data:
        return None
    return float(data.get("openInterest", 0))


def get_funding_history(symbol_usdt: str, limit: int = 42) -> list[float]:
    """История funding rate (каждые 8ч, 42 записи ≈ 14 дней) для EWMA."""
    data = safe_get(
        f"{BINANCE_FAPI}/fapi/v1/fundingRate", {"symbol": symbol_usdt, "limit": limit}
    )
    if isinstance(data, dict) and "_error" in data:
        return []
    return [float(d["fundingRate"]) * 100 for d in data]  # в %


def get_spot_perp_volumes(symbol_usdt: str) -> tuple[float | None, float | None]:
    """(spot_quote_volume, perp_quote_volume) за 24ч, в USDT."""
    spot = safe_get(f"{BINANCE_SPOT}/api/v3/ticker/24hr", {"symbol": symbol_usdt})
    perp = safe_get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", {"symbol": symbol_usdt})
    spot_v = float(spot["quoteVolume"]) if "_error" not in spot else None
    perp_v = float(perp["quoteVolume"]) if "_error" not in perp else None
    return spot_v, perp_v


def get_top_trader_ratio_binance(symbol_usdt: str, period: str = "1h") -> float | None:
    data = safe_get(
        f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio",
        {"symbol": symbol_usdt, "period": period, "limit": 1},
    )
    if isinstance(data, dict) and "_error" in data or not data:
        return None
    return float(data[-1]["longShortRatio"])


def get_top_trader_ratio_okx(ccy: str) -> float | None:
    data = safe_get(
        f"{OKX_API}/api/v5/rubik/stat/contracts/long-short-account-ratio",
        {"ccy": ccy},
    )
    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        return None
    return float(rows[0][1])  # [timestamp, ratio]


def get_hyperliquid_asset(coin: str = "HYPE") -> dict:
    """OI/funding/markPrice для токена на Hyperliquid (нативный DEX, USDC-only маржа)."""
    data = safe_get(HYPERLIQUID_API, method="POST", json_body={"type": "metaAndAssetContexts"})
    if isinstance(data, dict) and "_error" in data:
        return {"_error": data["_error"]}
    try:
        universe = data[0]["universe"]
        contexts = data[1]
        idx = next(i for i, a in enumerate(universe) if a["name"] == coin)
        ctx = contexts[idx]
        return {
            "openInterest": float(ctx.get("openInterest", 0)),
            "funding": float(ctx.get("funding", 0)) * 100,  # в %
            "markPrice": float(ctx.get("markPx", 0)),
        }
    except Exception as e:
        return {"_error": f"parse_failed: {e}"}


# ---------------------------------------------------------------------------
# РАСЧЁТЫ: Z-SCORE / EWMA / КЛАССИФИКАЦИЯ
# ---------------------------------------------------------------------------

def rolling_zscore(history: list[float], current: float) -> float | None:
    """Z = (X_t - mean_30) / std_30"""
    if len(history) < 5:
        return None
    mu = mean(history)
    sigma = pstdev(history)
    if sigma == 0:
        return None
    return (current - mu) / sigma


def ewma(values: list[float], span_periods: int = 42, weight_recent: float = 0.15) -> float | None:
    """Простая EWMA: последние сессии весят больше (alpha ~ 2/(N+1))."""
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
    ref = history[-1]  # последняя дневная точка ≈ 24ч назад
    if ref == 0:
        return None
    return (current - ref) / ref * 100


def classify_scenario(symbol: str, oi_change_pct: float | None,
                       funding_now: float | None, funding_ewma: float | None,
                       spot_vol: float | None, perp_vol: float | None) -> dict:
    """Классификация: HEALTHY_TREND (A) / SQUEEZE_SETUP (B) / NEUTRAL / INSUFFICIENT_DATA."""
    if oi_change_pct is None or funding_now is None:
        return {"scenario": "INSUFFICIENT_DATA", "note": "OI или funding не получены"}

    tier = tier_of(symbol)
    soft, hard = OI_THRESHOLD[tier]
    perp_over_spot = None
    if spot_vol and perp_vol:
        perp_over_spot = perp_vol > spot_vol

    # Сценарий Б: параболический OI + funding ускоряется + перп доминирует объёмом
    if oi_change_pct >= hard and funding_now > FUNDING_LONG_SQUEEZE[tier] and perp_over_spot:
        return {
            "scenario": "B_SQUEEZE_SETUP",
            "note": (
                f"OI +{oi_change_pct:.1f}% (порог {hard}%), funding {funding_now:.3f}%/8ч "
                f"> порога {FUNDING_LONG_SQUEEZE[tier]}%, perp-объём доминирует над спотом. "
                "Риск каскадного long squeeze — рассмотреть фиксацию/хедж."
            ),
        }

    # Сценарий А: плавный рост OI, funding стабилизируется в норме, спот не отстаёт
    if soft * 0.5 <= oi_change_pct < hard and abs(funding_now) <= FUNDING_LONG_SQUEEZE[tier] * 0.8:
        return {
            "scenario": "A_HEALTHY_TREND",
            "note": f"OI +{oi_change_pct:.1f}% плавно, funding {funding_now:.3f}%/8ч в норме.",
        }

    # Short squeeze risk
    if funding_now <= FUNDING_SHORT_SQUEEZE[tier]:
        return {
            "scenario": "SHORT_SQUEEZE_RISK",
            "note": f"Funding {funding_now:.3f}%/8ч глубоко отрицательный — ритейл шортит, риск сквиза вверх.",
        }

    return {"scenario": "NEUTRAL", "note": "Нет однозначного паттерна по текущим данным."}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

# symbol -> (Binance USDT-M pair, OKX ccy, Coin-M pair or None)
ASSET_MAP = {
    "BTC": ("BTCUSDT", "BTC", "BTCUSD_PERP"),
    "ETH": ("ETHUSDT", "ETH", "ETHUSD_PERP"),
    "SOL": ("SOLUSDT", "SOL", None),
    "LINK": ("LINKUSDT", "LINK", None),
    "ONDO": ("ONDOUSDT", "ONDO", None),
    "AVAX": ("AVAXUSDT", "AVAX", None),
    "XRP": ("XRPUSDT", "XRP", None),
    "SUI": ("SUIUSDT", "SUI", None),
    "TIA": ("TIAUSDT", "TIA", None),
    "IMX": ("IMXUSDT", "IMX", None),
    "LTC": ("LTCUSDT", "LTC", None),
    "BCH": ("BCHUSDT", "BCH", None),
    "NEAR": ("NEARUSDT", "NEAR", None),
    "RENDER": ("RENDERUSDT", "RENDER", None),
    "WLD": ("WLDUSDT", "WLD", None),
    "XLM": ("XLMUSDT", "XLM", None),
    "ZEC": ("ZECUSDT", "ZEC", None),
    # AKT, AR — часто отсутствуют на Binance Futures, обрабатываются как "нет данных"
}
HYPERLIQUID_NATIVE = {"HYPE"}  # особый путь сбора


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

    usdt_pair, okx_ccy, coinm_pair = mapping
    tier = tier_of(symbol)

    oi_now = get_oi_current(usdt_pair)
    oi_hist = get_oi_history(usdt_pair, days=30)
    oi_change = oi_pct_change_24h(oi_hist, oi_now) if oi_now is not None else None
    oi_z = rolling_zscore(oi_hist, oi_now) if oi_now is not None else None

    funding_hist = get_funding_history(usdt_pair)
    funding_now = funding_hist[-1] if funding_hist else None
    funding_ewma_val = ewma(funding_hist) if funding_hist else None

    spot_vol, perp_vol = get_spot_perp_volumes(usdt_pair)
    spot_perp_ratio = (spot_vol / perp_vol) if (spot_vol and perp_vol) else None

    top_trader_binance = get_top_trader_ratio_binance(usdt_pair)
    top_trader_okx = get_top_trader_ratio_okx(okx_ccy)

    oi_coinm = get_oi_coinm(coinm_pair) if coinm_pair else None

    scenario = classify_scenario(symbol, oi_change, funding_now, funding_ewma_val, spot_vol, perp_vol)

    block = {
        "symbol": symbol,
        "tier": tier,
        "source": "binance/okx",
        "oi_current": oi_now,
        "oi_change_24h_pct": round(oi_change, 2) if oi_change is not None else None,
        "oi_zscore_30d": round(oi_z, 2) if oi_z is not None else None,
        "oi_coinm_current": oi_coinm,
        "funding_now_pct_8h": round(funding_now, 4) if funding_now is not None else None,
        "funding_ewma_14d_pct": round(funding_ewma_val, 4) if funding_ewma_val is not None else None,
        "spot_perp_volume_ratio": round(spot_perp_ratio, 3) if spot_perp_ratio is not None else None,
        "spot_perp_dominated_warning": (spot_perp_ratio is not None and spot_perp_ratio < SPOT_PERP_RATIO_WARN),
        "top_trader_ls_ratio_binance": top_trader_binance,
        "top_trader_ls_ratio_okx": top_trader_okx,
        "options_skew_applicable": symbol not in ALT_STACK_ONLY,
        **scenario,
    }
    return block


def main():
    assets = sys.argv[1:] if len(sys.argv) > 1 else list(ASSET_MAP.keys()) + list(HYPERLIQUID_NATIVE)
    result = {
        "section": "Mark50_Section10_Derivatives_Positioning",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assets": [],
    }
    for sym in assets:
        result["assets"].append(build_asset_block(sym))
        time.sleep(0.15)  # вежливый rate-limit, все эндпоинты далеко от лимитов

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
