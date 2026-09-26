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

РЕДАКЦИЯ 2 — Mark50-Judge, 26.09.2026 [одобрено Viktor 26.09.2026]
  1. Поправка TVL на цену нативного токена (Solana → SOL, Base → ETH).
     TVL в USD растёт вместе с ценой токена; сценарии A и C читали рост цены
     как приток капитала. Считается tvl_pct_price_adj = (1+TVL)/(1+цена) − 1.
     Допущение: весь TVL несёт бету нативного токена — поправка ЗАВЫШЕНА
     (стейблы внутри TVL цену не несут). Консервативно: C труднее получить.
  2. Классификация идёт по скорректированному TVL. Исходный вердикт
     сохраняется в поле scenario_raw (для сравнения и аудита).
  3. Гистерезис: поле scenario (подтверждённое) меняется, только если
     новый вердикт повторился в 2 запусках подряд. Иначе остаётся прежний,
     кандидат виден в scenario_candidate.
  4. История ДОПИСЫВАЕТСЯ: history[chain]["runs"] — список всех запусков
     (последние 400). Поля dominance / last_run_utc сохранены для совместимости.
  HARD RULE сохранён: цена не получена → поправка НЕ делается, поле
  "ДАННЫЕ НЕ ПОЛУЧЕНЫ", вердикт по исходному TVL с пометкой.
"""

import json
import os
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------

TARGET_CHAINS = ["Base", "Solana"]  # расширяемо: "Arbitrum", "Ethereum" и т.д.
# нативный токен сети для поправки TVL на цену (id DeFiLlama coins API)
NATIVE_TOKEN = {"Base": "coingecko:ethereum", "Solana": "coingecko:solana"}
HYSTERESIS_RUNS = 2      # сколько запусков подряд нужно для смены сценария
HISTORY_MAX_RUNS = 400
LOOKBACK_DAYS = 7  # окно для % изменения mcap/TVL

Z_THRESHOLD = 5.0
TVL_FLAT_THRESHOLD = 3.0
DOMINANCE_SHIFT_THRESHOLD = 3.0

STABLECOIN_CHART_URL = "https://stablecoins.llama.fi/stablecoincharts/{chain}"
STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins"  # даёт peggedAssets с chainCirculating
CHAIN_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl/{chain}"
PRICE_HIST_URL = "https://coins.llama.fi/prices/historical/{ts}/{coin}"

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
    # даты точек (unix, сек) — по ним берётся цена токена, окно то же самое
    get_tvl_change.last_dates = (data[-(lookback_days + 1)].get("date"), data[-1].get("date"))
    return old_val, new_val, pct_change(old_val, new_val)


get_tvl_change.last_dates = (None, None)


def get_price_at(coin, ts):
    if coin is None or ts is None:
        return None
    data = safe_get(PRICE_HIST_URL.format(ts=int(ts), coin=coin))
    try:
        return float(data["coins"][coin]["price"])
    except Exception:
        return None


def price_adjust(tvl_pct, px_pct):
    """TVL в USD, очищенный от изменения цены нативного токена, в %."""
    if tvl_pct is None or px_pct is None:
        return None
    return round(((1 + tvl_pct / 100) / (1 + px_pct / 100) - 1) * 100, 2)


def scenario_code(label):
    """'C — разгар…' → 'C'; 'NEUTRAL — …' → 'NEUTRAL'; NO_DATA → NO_DATA."""
    return label.split(" ")[0] if isinstance(label, str) else str(label)


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

        scenario_raw = classify_scenario(stable_pct, tvl_pct)

        # --- ред. 2: поправка TVL на цену нативного токена
        d_old, d_new = get_tvl_change.last_dates
        coin = NATIVE_TOKEN.get(chain)
        px_old, px_new = get_price_at(coin, d_old), get_price_at(coin, d_new)
        px_pct = pct_change(px_old, px_new)
        tvl_adj = price_adjust(tvl_pct, px_pct)
        if tvl_adj is not None:
            candidate = classify_scenario(stable_pct, tvl_adj)
            basis = "по TVL с поправкой на цену"
        else:
            candidate = scenario_raw
            basis = "ЦЕНА НЕ ПОЛУЧЕНА — по исходному TVL, без поправки"

        # --- ред. 2: гистерезис
        runs = history.get(chain, {}).get("runs", [])
        prev_confirmed = history.get(chain, {}).get("confirmed_scenario")
        prev_candidate = runs[-1].get("candidate") if runs else None
        if prev_confirmed is None:
            confirmed = candidate                       # первый запуск ред. 2
        elif scenario_code(candidate) == scenario_code(prev_confirmed):
            confirmed = candidate
        elif prev_candidate is not None and scenario_code(prev_candidate) == scenario_code(candidate):
            confirmed = candidate                       # повторился HYSTERESIS_RUNS=2 раза подряд
        else:
            confirmed = prev_confirmed                  # одиночный переход не засчитан
        scenario = confirmed

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
            "native_token_price": {
                "coin": coin,
                "old_usd": px_old if px_old is not None else NO_DATA,
                "new_usd": px_new if px_new is not None else NO_DATA,
                "pct_change": px_pct if px_pct is not None else NO_DATA,
            },
            "tvl_pct_price_adj": tvl_adj if tvl_adj is not None else NO_DATA,
            "scenario": scenario,
            "scenario_candidate": candidate,
            "scenario_raw": scenario_raw,
            "scenario_basis": f"{basis}; гистерезис {HYSTERESIS_RUNS} запуска",
        }

        runs = runs + [{
            "run_utc": timestamp,
            "stable_pct": stable_pct,
            "tvl_pct": tvl_pct,
            "native_px_pct": px_pct,
            "tvl_pct_price_adj": tvl_adj,
            "scenario_raw": scenario_code(scenario_raw),
            "candidate": scenario_code(candidate),
            "confirmed": scenario_code(confirmed),
        }]
        history[chain] = {
            "dominance": dominance,
            "last_run_utc": timestamp,
            "confirmed_scenario": confirmed,
            "runs": runs[-HISTORY_MAX_RUNS:],
        }

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
