"""
Buoy #1 — Stablecoin Flow Radar
================================
Источник: DeFiLlama public API (без ключа, без JS-рендеринга).

Логика (по протоколу "Морской буй", ревизия после 30.06.2026 аудита источников):
  - Layer 3 (триггер входа) — реализован полностью: % изменение circulating
    stablecoin mcap по сети vs % изменение TVL сети за одно окно.
  - Layer 2 (контекст, доминирование USDT/USDC внутри сети) — реализован:
    снапшот текущей доли + сравнение с предыдущим запуском (история).
  - Layer 1 (Arkham/Nansen/DeBank per-wallet) — НЕ автоматизирован.
    Эти источники платные или без API. Остаётся ручным чек-листом.
  - CEX Tron-резервы (Binance/Bybit/OKX) — НЕ автоматизирован в этой версии.
    DeFiLlama /cex/* страницы не имеют документированного публичного JSON API,
    в отличие от /stablecoins и /v2/historicalChainTvl. Пропущено, а не
    фальсифицировано — при необходимости можно добавить отдельным модулем.

HARD RULE: при сбое запроса поле помечается "ДАННЫЕ НЕ ПОЛУЧЕНЫ", число
никогда не выдумывается.

Пороги (первые прикидочные значения — требуют калибровки на реальных данных,
как и было отмечено в оригинальном ТЗ):
  Z_THRESHOLD        = 5.0   # % изменение stablecoin mcap, значимое событие
  TVL_FLAT_THRESHOLD = 3.0   # % изменение TVL, ниже которого считаем "плоско"
  DOMINANCE_SHIFT_THRESHOLD = 3.0  # п.п. сдвига доли USDT/USDC за запуск

Запуск:
  pip install requests --break-system-packages
  python3 buoy_1_stablecoin_radar.py

Результат пишется в buoy_1_output.json (плюс buoy_1_history.json для
сравнения доминирования между запусками — аналог cme_okx_spread_history.json).
"""

import json
import os
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

TARGET_CHAINS = ["Base", "Solana"]  # расширяемо: "Arbitrum", "Ethereum" и т.д.
LOOKBACK_DAYS = 7  # окно для % изменения mcap/TVL

Z_THRESHOLD = 5.0
TVL_FLAT_THRESHOLD = 3.0
DOMINANCE_SHIFT_THRESHOLD = 3.0

STABLECOIN_CHART_URL = "https://stablecoins.llama.fi/stablecoincharts/{chain}"
STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins"  # даёт peggedAssets с chainCirculating
CHAIN_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/{chain}"

OUTPUT_PATH = os.path.join(os.getcwd(), "buoy_1_output.json")
HISTORY_PATH = os.path.join(os.getcwd(), "buoy_1_history.json")

NO_DATA = "ДАННЫЕ НЕ ПОЛУЧЕНЫ"

# Известные ID стейблкоинов на DeFiLlama (для чтения breakdown по сети)
STABLE_SYMBOLS_OF_INTEREST = ["USDT", "USDC"]


def safe_get(url, timeout=15):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] fetch failed: {url} -> {e}")
        return None


def pct_change(old, new):
    if old is None or new is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def get_stablecoin_mcap_change(chain, lookback_days=LOOKBACK_DAYS):
    """Возвращает (old_mcap, new_mcap, pct_change) для суммарного circulating
    stablecoin mcap на chain за lookback_days."""
    data = safe_get(STABLECOIN_CHART_URL.format(chain=chain))
    if not data or not isinstance(data, list) or len(data) < lookback_days + 1:
        return None, None, None

    def total_usd(point):
        try:
            return float(point["totalCirculatingUSD"]["peggedUSD"])
        except Exception:
            return None

    new_point = data[-1]
    old_point = data[-(lookback_days + 1)]
    new_val = total_usd(new_point)
    old_val = total_usd(old_point)
    return old_val, new_val, pct_change(old_val, new_val)


def get_tvl_change(chain, lookback_days=LOOKBACK_DAYS):
    data = safe_get(CHAIN_TVL_URL.format(chain=chain))
    if not data or not isinstance(data, list) or len(data) < lookback_days + 1:
        return None, None, None

    new_val = data[-1].get("tvl")
    old_val = data[-(lookback_days + 1)].get("tvl")
    return old_val, new_val, pct_change(old_val, new_val)


def get_dominance_snapshot(chain):
    """Текущая доля USDT/USDC внутри chain (по circulating mcap).

    Реальная структура https://stablecoins.llama.fi/stablecoins:
      {"peggedAssets": [ {symbol, chainCirculating: {chain: {current: {peggedUSD}}}}, ... ],
       "chains": [...]}
    """
    data = safe_get(STABLECOINS_URL)
    if not data or "peggedAssets" not in data:
        return None

    totals = {}
    grand_total = 0.0
    for stable in data["peggedAssets"]:
        symbol = stable.get("symbol")
        chain_circ = stable.get("chainCirculating", {}).get(chain)
        if not chain_circ:
            continue
        val = chain_circ.get("current", {}).get("peggedUSD")
        if val is None:
            continue
        if symbol in STABLE_SYMBOLS_OF_INTEREST:
            totals[symbol] = totals.get(symbol, 0.0) + val
        grand_total += val

    if grand_total == 0:
        return None

    shares = {sym: round(val / grand_total * 100, 2) for sym, val in totals.items()}
    return {"shares_pct": shares, "grand_total_usd": round(grand_total, 2)}


def classify_scenario(stable_pct, tvl_pct, z_threshold=Z_THRESHOLD, tvl_flat=TVL_FLAT_THRESHOLD):
    """Сценарии A/B/C по протоколу "Морской буй"."""
    if stable_pct is None or tvl_pct is None:
        return NO_DATA

    stable_up = stable_pct >= z_threshold
    stable_down = stable_pct <= -z_threshold
    tvl_flat = abs(tvl_pct) < tvl_flat
    tvl_up_strong = tvl_pct >= z_threshold

    if stable_up and tvl_flat:
        return "A — приток стейблов, деньги ещё не распределены (сильный сигнал ожидания)"
    if stable_down and tvl_up_strong:
        return "B — ложный рост TVL за счёт цены нативного токена, капитал не заходит"
    if stable_up and tvl_up_strong:
        return "C — разгар хайпа, капитал сразу идёт в работу, осторожный вход"
    return "NEUTRAL — нет чёткого сигнала по текущим порогам"


def load_history():
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


def run():
    timestamp = datetime.now(timezone.utc).isoformat()
    history = load_history()
    result = {"timestamp_utc": timestamp, "lookback_days": LOOKBACK_DAYS, "chains": {}}

    for chain in TARGET_CHAINS:
        old_m, new_m, stable_pct = get_stablecoin_mcap_change(chain)
        old_t, new_t, tvl_pct = get_tvl_change(chain)
        dominance = get_dominance_snapshot(chain)

        dominance_shift = None
        if dominance and chain in history and history[chain].get("dominance"):
            prev_shares = history[chain]["dominance"].get("shares_pct", {})
            curr_shares = dominance.get("shares_pct", {})
            dominance_shift = {
                sym: round(curr_shares.get(sym, 0) - prev_shares.get(sym, 0), 2)
                for sym in STABLE_SYMBOLS_OF_INTEREST
                if sym in curr_shares or sym in prev_shares
            }
            for sym, delta in dominance_shift.items():
                if abs(delta) >= DOMINANCE_SHIFT_THRESHOLD:
                    print(f"[SIGNAL] {chain}: доля {sym} сдвинулась на {delta} п.п. с прошлого запуска")

        scenario = classify_scenario(stable_pct, tvl_pct)

        result["chains"][chain] = {
            "stablecoin_mcap": {
                "old_usd": old_m if old_m is not None else NO_DATA,
                "new_usd": new_m if new_m is not None else NO_DATA,
                "pct_change": stable_pct if stable_pct is not None else NO_DATA,
            },
            "tvl": {
                "old_usd": old_t if old_t is not None else NO_DATA,
                "new_usd": new_t if new_t is not None else NO_DATA,
                "pct_change": tvl_pct if tvl_pct is not None else NO_DATA,
            },
            "dominance": dominance if dominance is not None else NO_DATA,
            "dominance_shift_vs_prev_run_pp": dominance_shift if dominance_shift is not None else "N/A (первый запуск или нет истории)",
            "scenario": scenario,
        }

        history[chain] = {"dominance": dominance, "last_run_utc": timestamp}

    result["not_automated"] = {
        "layer_1_smart_money_per_wallet": "Arkham/Nansen/DeBank — платный доступ или без API, ручной чек-лист",
        "cex_tron_reserves": "DeFiLlama /cex/* без документированного JSON API в этой версии",
    }

    save_history(history)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nЗаписано в {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
