"""
Skew-breach Cross-check — дополнение к Mark50 Section 10
==========================================================
Протокол (зафиксирован 08.08.2026): при пробое BTC 25-delta skew критического
порога (±2.0σ) не интерпретировать сигнал изолированно — сверять против
независимых метрик Bybit, чтобы отличить "bullish dealer hedge unwind" от
"vol crush precursor to violent move".

Автоматизировано в этом модуле (все — публичные Bybit эндпоинты, без ключа):
  1. Funding rate      — near-zero/flat = органичный анвайнд
  2. L/S Position Ratio — стабильность = не панический сдвиг толпы
  3. Real-time basis    — норма = нет межрыночного стресса

НЕ автоматизировано (оставлено как ручной шаг):
  - CME vs aggregate exchange OI divergence. Coinglass per-exchange breakdown
    рендерится через JS — не отдаёт данные через обычный HTTP fetch (см. аудит
    источников). Институциональный OI пока проверяется вручную на
    coinglass.com/ru/open-interest/BTC.

HARD RULE: при сбое запроса — "ДАННЫЕ НЕ ПОЛУЧЕНЫ", никогда не выдумывается.

Запуск:
  pip install requests --break-system-packages
  python3 skew_crosscheck.py

Работает в двух режимах:
  1. Читает skew_history.json из текущей директории (если он там есть после
     git clone в GH Actions) и сам определяет, есть ли критический пробой.
  2. Если файла нет или он недоступен — можно вызвать force_check=True для
     ручного прогона независимо от текущего значения skew.
"""

import json
import os
from datetime import datetime, timezone

import requests

NO_DATA = "ДАННЫЕ НЕ ПОЛУЧЕНЫ"

SKEW_HISTORY_PATH = os.path.join(os.getcwd(), "skew_history.json")
OUTPUT_PATH = os.path.join(os.getcwd(), "skew_crosscheck_output.json")

BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest"
BYBIT_LS_RATIO_URL = "https://api.bybit.com/v5/market/account-ratio"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"

SYMBOL = "BTCUSDT"

# Пороги по протоколу (Mark50 Section 10 addendum, 08.08.2026)
SKEW_CRITICAL_SIGMA = 2.0
SKEW_SIGNAL_SIGMA = 1.5
FUNDING_FLAT_THRESHOLD = 0.01     # % за 8ч, ниже — считаем "плоско"
LS_RATIO_STABILITY_WINDOW = 5     # последних точек для оценки стабильности


def safe_get(url, params=None, timeout=15):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            print(f"[WARN] Bybit API error: {data.get('retMsg')}")
            return None
        return data.get("result")
    except Exception as e:
        print(f"[WARN] fetch failed: {url} -> {e}")
        return None


def load_skew_history():
    if not os.path.exists(SKEW_HISTORY_PATH):
        return None
    try:
        with open(SKEW_HISTORY_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] cannot read skew_history.json: {e}")
        return None


def compute_skew_zscore(history, lookback_days=14):
    """Z-score последней точки против trailing mean/std."""
    if not history or len(history) < lookback_days + 1:
        return None, None, None

    values = [pt["skew"] for pt in history[-(lookback_days + 1):]]
    baseline = values[:-1]
    latest = values[-1]

    mean = sum(baseline) / len(baseline)
    variance = sum((v - mean) ** 2 for v in baseline) / len(baseline)
    std = variance ** 0.5

    if std == 0:
        return latest, mean, None

    z = (latest - mean) / std
    return latest, mean, z


def get_funding_rate():
    result = safe_get(BYBIT_FUNDING_URL, params={"category": "linear", "symbol": SYMBOL, "limit": 1})
    if not result or not result.get("list"):
        return None
    rate = float(result["list"][0]["fundingRate"]) * 100  # в %
    return rate


def get_ls_ratio_stability():
    """Возвращает (текущее значение, стабильно ли за последние N точек)."""
    result = safe_get(BYBIT_LS_RATIO_URL, params={"category": "linear", "symbol": SYMBOL, "period": "5min", "limit": LS_RATIO_STABILITY_WINDOW})
    if not result or not result.get("list"):
        return None, None

    ratios = [float(pt["buyRatio"]) for pt in result["list"]]
    if len(ratios) < 2:
        return ratios[0] if ratios else None, None

    spread = max(ratios) - min(ratios)
    stable = spread < 0.05  # менее 5 п.п. разброса за окно = стабильно
    return ratios[-1], stable


def get_basis():
    result = safe_get(BYBIT_TICKERS_URL, params={"category": "linear", "symbol": SYMBOL})
    if not result or not result.get("list"):
        return None, None

    ticker = result["list"][0]
    mark_price = float(ticker.get("markPrice", 0))
    index_price = float(ticker.get("indexPrice", 0))
    if index_price == 0:
        return None, None

    basis_abs = mark_price - index_price
    basis_pct = basis_abs / index_price * 100
    return basis_abs, basis_pct


def classify_convergence(funding, ls_stable, basis_pct):
    """Применяет Convergence Rule из протокола.

    Возвращает вердикт на основе трёх автоматизированных метрик.
    CME/retail OI divergence (4й фактор) остаётся ручным — помечается
    отдельно в итоговом отчёте.
    """
    signals = []

    if funding is not None:
        if abs(funding) < FUNDING_FLAT_THRESHOLD:
            signals.append(("funding", "benign", f"{funding:.4f}% — плоский"))
        else:
            direction = "перегретые лонги" if funding > 0 else "перегретые шорты"
            signals.append(("funding", "diverging", f"{funding:.4f}% — {direction}"))
    else:
        signals.append(("funding", NO_DATA, NO_DATA))

    if ls_stable is not None:
        if ls_stable:
            signals.append(("ls_ratio", "benign", "стабильно, нет резкого сдвига толпы"))
        else:
            signals.append(("ls_ratio", "diverging", "резкий сдвиг — возможен паникующий рынок"))
    else:
        signals.append(("ls_ratio", NO_DATA, NO_DATA))

    if basis_pct is not None:
        if abs(basis_pct) < 0.5:
            signals.append(("basis", "benign", f"{basis_pct:.3f}% — норма"))
        else:
            signals.append(("basis", "diverging", f"{basis_pct:.3f}% — широкий разрыв, межрыночный стресс"))
    else:
        signals.append(("basis", NO_DATA, NO_DATA))

    benign_count = sum(1 for _, verdict, _ in signals if verdict == "benign")
    diverging_count = sum(1 for _, verdict, _ in signals if verdict == "diverging")
    no_data_count = sum(1 for _, verdict, _ in signals if verdict == NO_DATA)

    if diverging_count > 0:
        overall = "AMBIGUOUS — минимум одна метрика расходится, ждать подтверждения ценой (CHoCH/BOS)"
    elif no_data_count == len(signals):
        overall = NO_DATA
    elif benign_count >= 2:
        overall = "LEANS BENIGN/BULLISH — метрики согласованы, склоняется к accumulation/hedge-unwind, не к vol crush"
    else:
        overall = "INCONCLUSIVE — недостаточно данных для уверенного вывода"

    return signals, overall


def run(force_check=False):
    timestamp = datetime.now(timezone.utc).isoformat()
    history = load_skew_history()

    latest, mean, z = (None, None, None)
    if history:
        latest, mean, z = compute_skew_zscore(history)

    breach = force_check
    if z is not None and abs(z) >= SKEW_CRITICAL_SIGMA:
        breach = True

    result = {
        "timestamp_utc": timestamp,
        "skew_latest": latest if latest is not None else NO_DATA,
        "skew_trailing_mean_14d": round(mean, 3) if mean is not None else NO_DATA,
        "skew_zscore": round(z, 2) if z is not None else NO_DATA,
        "critical_breach": breach,
    }

    if not breach:
        result["note"] = "Skew в пределах нормы, кросс-чек не запускался. Используй force_check=True для ручного прогона."
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    funding = get_funding_rate()
    ls_latest, ls_stable = get_ls_ratio_stability()
    basis_abs, basis_pct = get_basis()

    signals, overall = classify_convergence(funding, ls_stable, basis_pct)

    result["crosscheck"] = {
        "funding_rate_pct_8h": funding if funding is not None else NO_DATA,
        "ls_ratio_latest": ls_latest if ls_latest is not None else NO_DATA,
        "ls_ratio_stable": ls_stable if ls_stable is not None else NO_DATA,
        "basis_pct": round(basis_pct, 4) if basis_pct is not None else NO_DATA,
        "signals": [{"metric": m, "verdict": v, "detail": d} for m, v, d in signals],
        "overall_verdict": overall,
        "not_automated": "CME vs aggregate exchange OI divergence — Coinglass per-exchange breakdown is JS-rendered, requires manual check at coinglass.com/ru/open-interest/BTC",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nЗаписано в {OUTPUT_PATH}")
    return result


if __name__ == "__main__":
    # force_check=True прогоняет сразу, независимо от текущего значения skew —
    # удобно для первого теста. В проде (cron/Routine) держать False, чтобы
    # кросс-чек запускался только при реальном критическом пробое.
    run(force_check=True)
