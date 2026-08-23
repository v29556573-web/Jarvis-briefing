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

[РЕШЕНИЕ VIKTOR 16.08.2026, пункт 2 snapshot/intraday]: skew_history.json
хранит ОДНУ точку в сутки (last-write-wins на дату, помечена
value_type="eod_close" + source_timestamp реального момента записи).
Каждый внутридневной прогон дополнительно пишет сырой снимок в
skew_intraday.json (value_type="intraday", хранится 7 дней) — это чистый
аудит-лог фактических запусков, НЕ используется в расчёте Z-score/baseline.

[РЕШЕНИЕ VIKTOR 16.08.2026, пункт 4 критерий разворота]: старый z-score
считался по ВСЕЙ истории (mean/std от полного массива), из-за чего
монотонный дрейф со временем сам себя подсвечивал как аномалию — точка
не разворачивалась, а просто продолжала тренд, но всё равно уходила
за ±1.5σ/±2.0σ от статичного среднего. Заменено на ДЕТРЕНДИНГ: по
последним 14 точкам (тот же канон окна, что в skew_crosscheck.py —
[РЕШЕНИЕ VIKTOR 15.08.2026], exclude current, единая методология по всем
компонентам) строится линейный тренд (МНК), текущая точка сравнивается
не со средним, а с ПРОГНОЗОМ тренда на следующий шаг. Z-score теперь —
это отклонение от продолжения тренда, а не от статичного уровня:
монотонный дрейф больше не накапливает z сам на себя, критический флаг
означает уход ОТ линии тренда, а не просто высокое абсолютное значение.
Старый full-history z-score НЕ удалён — сохранён в выводе как
zscore_legacy_full_history / classification_legacy для сравнения и
истории решения (принцип: плавающие/устаревшие параметры помечаются
[ПРЕДЫДУЩЕЕ], а не удаляются молча). Управляющее поле для Daily Check
и прочих потребителей — "classification" (теперь на детренд-критерии),
не "classification_legacy".

Пороги (из памяти JARVIS "Mark50 Section 10"):
  Z-score ±1.5σ = сигнал, ±2.0σ = критично (институциональный tail-hedge)

Зависимости: requests
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from statistics import mean, pstdev

import requests

DERIBIT_BASE = "https://www.deribit.com/api/v2"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_history.json")
INTRADAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_intraday.json")
BLOCK_OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_block_output.json")
HISTORY_MAX_DAYS = 90
INTRADAY_MAX_DAYS = 7
BASELINE_WINDOW = 14  # канон [РЕШЕНИЕ VIKTOR 15.08.2026], единый для skew_crosscheck.py и skew_block.py
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


def get_ticker(instrument_name):
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


def append_today(history, skew_value, timestamp_iso):
    today = timestamp_iso[:10]
    history = [h for h in history if h["date"] != today]  # не дублировать при повторном запуске в тот же день
    history.append({
        "date": today,
        "skew": skew_value,
        "value_type": "eod_close",
        "source_timestamp": timestamp_iso,
    })
    return history


def load_intraday():
    if not os.path.exists(INTRADAY_FILE):
        return []
    try:
        with open(INTRADAY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_intraday(snapshots):
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=INTRADAY_MAX_DAYS)).strftime("%Y-%m-%d")
    snapshots = [s for s in snapshots if s["date"] >= cutoff_date]
    with open(INTRADAY_FILE, "w") as f:
        json.dump(snapshots, f, indent=2)


def append_intraday_snapshot(snapshots, skew_value, timestamp_iso):
    snapshots.append({
        "date": timestamp_iso[:10],
        "timestamp_utc": timestamp_iso,
        "skew": skew_value,
        "value_type": "intraday",
    })
    return snapshots


def linear_regression(xs, ys):
    """МНК без numpy: возвращает (slope, intercept) для y = slope*x + intercept."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den != 0 else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


def compute_detrended_zscore(prior_history, current_skew, lookback_days=BASELINE_WINDOW):
    """Z-score текущей точки относительно ПРОГНОЗА линейного тренда по
    последним lookback_days точкам (а не относительно статичного среднего).

    [РЕШЕНИЕ VIKTOR 16.08.2026, пункт 4]: монотонный дрейф не должен сам
    себя подсвечивать как аномалию — критично, только если точка уходит
    ОТ линии тренда, а не просто продолжает его на новом уровне.
    """
    if len(prior_history) < lookback_days:
        return None

    baseline = prior_history[-lookback_days:]
    xs = list(range(lookback_days))  # 0..13, порядок по возрастанию даты
    ys = [pt["skew"] for pt in baseline]

    slope, intercept = linear_regression(xs, ys)
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    residual_std = pstdev(residuals)

    predicted_current = slope * lookback_days + intercept  # экстраполяция на следующий шаг (индекс 14)
    residual_current = current_skew - predicted_current
    z = residual_current / residual_std if residual_std != 0 else None

    return {
        "zscore": round(z, 2) if z is not None else None,
        "trend_slope": round(slope, 4),
        "trend_intercept": round(intercept, 4),
        "predicted_value": round(predicted_current, 3),
        "residual": round(residual_current, 3),
        "residual_std": round(residual_std, 4),
        "baseline_window": lookback_days,
        "baseline_start": baseline[0].get("date"),
        "baseline_end": baseline[-1].get("date"),
        "includes_current": False,
        "method": "linear_detrend",
    }


def rolling_zscore_legacy(history_values, current):
    """[ПРЕДЫДУЩЕЕ, 16.08.2026] Z-score по ВСЕЙ истории от статичного
    среднего. Заменён детрендингом (compute_detrended_zscore) как основной
    критерий классификации — монотонный дрейф накапливал z сам на себя.
    Оставлен для сравнения/истории решения, не управляет classification."""
    if len(history_values) < 5:
        return None
    mu = mean(history_values)
    sigma = pstdev(history_values)
    if sigma == 0:
        return None
    return (current - mu) / sigma


def compute_classical_zscore(prior_history, current_skew, lookback_days=BASELINE_WINDOW):
    """Классический z: (текущее - mean(baseline)) / pop_std(baseline) по тем же
    14 точкам, что и детренд (exclude current). Канон §10.3, вторая нога
    двойного чтения [РЕШЕНИЕ VIKTOR 23.08.2026]. Тождественен методу
    skew_crosscheck.compute_skew_zscore, чтобы источники не расходились.

    Слепая зона классического: медленный монотонный дрейф (уровень уходит
    маленькими шагами, каждый \"нормальный\") — его ловит детренд. Слепая зона
    детренда: скачок уровня + залипание, где детренд инвертирует знак — его
    страхует классический. Методы взаимодополняющие, поэтому читаются оба.
    """
    if len(prior_history) < lookback_days:
        return None
    baseline = [pt["skew"] for pt in prior_history[-lookback_days:]]
    mu = mean(baseline)
    sigma = pstdev(baseline)
    if sigma == 0:
        return None
    return (current_skew - mu) / sigma


def combined_classification(z_detr, z_class):
    """Двойное чтение [РЕШЕНИЕ VIKTOR 23.08.2026]. НЕ усредняет — маркирует.
    - CRITICAL только при СОГЛАСИИ: оба |z|>=2.0 И совпадает знак.
    - NORMAL: оба в норме (|z|<1.5 или отсутствуют).
    - Иначе DIVERGENT: автоматического вердикта нет, эскалация Viktor.
    Соответствует правилу Judge при directional conflict: не сглаживать.
    """
    da = abs(z_detr) if z_detr is not None else 0.0
    ca = abs(z_class) if z_class is not None else 0.0
    both_present = z_detr is not None and z_class is not None
    same_sign = both_present and (z_detr > 0) == (z_class > 0)

    if both_present and da >= 2.0 and ca >= 2.0 and same_sign:
        side = "PUT_PREMIUM" if z_detr > 0 else "CALL_PREMIUM"
        return {"verdict": f"CRITICAL_{side}", "agreement": "AGREE", "escalate": False}
    if da < 1.5 and ca < 1.5:
        return {"verdict": "NORMAL", "agreement": "AGREE", "escalate": False}
    return {
        "verdict": "DIVERGENT",
        "agreement": "DISAGREE",
        "escalate": True,
        "note": "Методы расходятся — автоматического вердикта нет, требуется ручное решение Viktor (§10.3 двойное чтение).",
    }


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

    detrended = compute_detrended_zscore(history, skew)
    z = detrended["zscore"] if detrended else None

    z_legacy = rolling_zscore_legacy(prior_values, skew)
    z_classical = compute_classical_zscore(history, skew)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history = append_today(history, skew, now_iso)
    save_history(history)

    intraday = load_intraday()
    intraday = append_intraday_snapshot(intraday, skew, now_iso)
    save_intraday(intraday)

    result.update({
        "skew_pct": round(skew, 3),
        "zscore": z,
        "classification": classify_skew(z),
        "trend": detrended if detrended else "INSUFFICIENT_HISTORY",
        "zscore_legacy_full_history": round(z_legacy, 2) if z_legacy is not None else None,
        "classification_legacy": classify_skew(z_legacy),
        "zscore_classical": round(z_classical, 2) if z_classical is not None else None,
        "classification_classical": classify_skew(z_classical),
        "classification_combined": combined_classification(z, z_classical),
        "history_points": len(prior_values),
        "meta": meta,
    })

    # [РЕШЕНИЕ VIKTOR 16.08.2026]: результат коммитится в файл — Cloud-рутины
    # (JARVIS Pre-Market Check / Daily Check) должны читать classification/
    # zscore/trend ОТСЮДА, а не пересчитывать z-score самостоятельно по
    # skew_history.json своей текстовой инструкцией. Без этого файла патчи
    # методологии в этом скрипте не долетают до рутин — расхождение,
    # выявленное 16.08.2026.
    with open(BLOCK_OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
