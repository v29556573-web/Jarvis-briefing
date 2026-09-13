"""
Skew-breach Cross-check — дополнение к Mark50 Section 10
==========================================================
Протокол (зафиксирован 08.08.2026): при пробое BTC 25-delta skew критического
порога не интерпретировать сигнал изолированно — сверять против независимых
метрик Bybit, чтобы отличить "bullish dealer hedge unwind" от "vol crush
precursor to violent move".

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

=====================================================================
[ПАТЧ 13.09.2026 — Mark50-Judge]
=====================================================================

ГРУППА A — тождественность с skew_block.py [РАТИФИЦИРОВАНО 12.09.2026]

  A1. ДЕЛИТЕЛЬ. Было population std (делитель n). После патча Этапа 1
      skew_block.compute_classical_zscore использует выборочный std
      (делитель n-1). Докстринг там утверждает тождественность методов —
      без этой правки два файла считали бы одну величину разными
      формулами, отношение sqrt(14/13) = 1.0377.
      ЗАМЕР 13.09.2026: skew_block дал z_classical 0.9666,
      этот файл на старой формуле дал бы 1.0031.

  A2. ПОРОГИ. Фиксированные 2.0/1.5 подразумевают хвосты 4.55%/13.36%.
      При ОЦЕНЁННОЙ sigma распределение t(W-2), не нормальное.
      Пороги = t-квантили для тех же вероятностей. При W=14:
      C=2.231351, S=1.608964. Значения ИДЕНТИЧНЫ таблице T_QUANTILES
      в skew_block.py.

ГРУППА B — условие запуска кросс-чека

  ОСНОВАНИЕ. Кросс-чек НЕ читал вердикт skew_block. Он заново считал СВОЙ
  z — только классическую ногу, детренд-ноги у него нет — и гейтился по
  abs(z) >= 2.0. Детренд-нога, та самая, что уходит в полосу C и порождает
  DIVERGENT, была недоступна в принципе.

  ЗАМЕР на ряде 47 точек (33 дня, 10.08-12.09.2026):
      дни, где детренд в полосе C ....... 9
      дни, где кросс-чек сработал ....... 7
      ПЕРЕСЕЧЕНИЕ ....................... 3      <- треть

      Детренд критичен, кросс-чек МОЛЧАЛ (6 дней):
         15.08  z_detr= 2.334  z_class=-0.364
         16.08  z_detr= 4.594  z_class= 0.847
         28.08  z_detr= 3.420  z_class=-0.008   <- ЭСКАЛАЦИЯ
         29.08  z_detr= 3.285  z_class= 0.419
         11.09  z_detr= 4.244  z_class= 1.272   <- ЭСКАЛАЦИЯ
         12.09  z_detr= 2.061  z_class= 0.929   <- ЭСКАЛАЦИЯ

      Кросс-чек сработал, детренд в норме (4 дня):
         09.08 · 13.08 · 20.08 · 23.08 — все на стороне CALL_PREMIUM

  ВСЕ ТРИ фактические эскалации попали в молчание. Ветка
  classify_convergence исполнялась 7 раз, все в августе, ни разу при том
  расхождении, ради разрешения которого писалась.

  B1. ПУТИ. os.getcwd() -> директория скрипта. skew_block.py использует
      os.path.dirname(__file__); при запуске из другого cwd файлы
      указывали бы на РАЗНЫЕ skew_history.json.
  B2. ГЕЙТ по вердикту skew_block (escalate=True либо CRITICAL), а не по
      собственному z. Частота 21.2% -> 18.2%, состав меняется на 2/3.
  B3. СТРОКА СТАТУСА. "Skew в пределах нормы" печаталась при ЛЮБОМ
      breach=False, включая вердикт DIVERGENT (12.09 — так и произошло).
      Класс §12 №34, NORMAL-маскировка. Теперь печатается ФАКТ.
  B4. ПРОВЕРКА СВЕЖЕСТИ skew_block_output.json. Файл перезаписывается
      каждым прогоном; если skew_block упал или ещё не отработал, здесь
      читался бы вчерашний вердикт молча.

ЧЕГО ПАТЧ НЕ ДЕЛАЕТ — зарегистрировано, не правится без калибровки
(правка кабинетного порога без данных = дефект, пойманный трижды:
фандинг ±0.04%, нога 1.0xATR, коридор [0.85,1.20]):

  FUNDING_FLAT_THRESHOLD = 0.01 %/8ч
     🔴 КОНФЛИКТ: §10.2 Manual задаёт мёртвую зону |funding| < 0.005%.
     Здесь ВДВОЕ шире. Замеры 12.09: BTC 0.0056%, ETH 0.0073% — оба
     "плоские" по здешнему порогу и оба ВНЕ мёртвой зоны по §10.2.
     Один факт классифицируется противоположно в двух местах системы.

  basis_pct порог 0.5%
     🔴 §12 №35: перп-базис Bybit отрицателен 99.00% дней, как нога
     ВЫЧЕРКНУТ из §10.5. Здесь продолжает голосовать. Замеры: -0.05%,
     -0.04% -> всегда "норма". Нога ничего не различает.

  ls_stable: spread < 0.05 за 5 точек по 5 минут (окно 25 минут)
  classify_convergence: diverging_count > 0 -> AMBIGUOUS (1 из 3 убивает)

Запуск:
  pip install requests --break-system-packages
  python3 skew_crosscheck.py
"""

import json
import os
from datetime import datetime, timezone

import requests

NO_DATA = "ДАННЫЕ НЕ ПОЛУЧЕНЫ"

# [ПАТЧ B1, 13.09.2026] os.getcwd() -> директория скрипта.
# skew_block.py использует os.path.dirname(__file__); при запуске из
# другого cwd (GH Actions, cron) файлы указывали бы на РАЗНЫЕ пути.
_HERE = os.path.dirname(os.path.abspath(__file__))
SKEW_HISTORY_PATH = os.path.join(_HERE, "skew_history.json")
OUTPUT_PATH = os.path.join(_HERE, "skew_crosscheck_output.json")
BLOCK_OUTPUT_PATH = os.path.join(_HERE, "skew_block_output.json")

BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest"
BYBIT_LS_RATIO_URL = "https://api.bybit.com/v5/market/account-ratio"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"

SYMBOL = "BTCUSDT"

# [ПРЕДЫДУЩЕЕ, 13.09.2026] Фиксированные пороги протокола 08.08.2026.
# Заменены t-квантилями (патч A2). НЕ УДАЛЕНЫ — принцип: устаревшие
# параметры помечаются, не удаляются молча. В расчёте НЕ УЧАСТВУЮТ.
SKEW_CRITICAL_SIGMA = 2.0
SKEW_SIGNAL_SIGMA = 1.5   # был мёртв и до патча — нигде не использовался

# [ПАТЧ A2, 13.09.2026] t-квантили для хвостов 4.55% / 13.36%, W=14, df=12.
# ⛔ ИДЕНТИЧНЫ T_QUANTILES[14] в skew_block.py. При смене BASELINE_WINDOW
# обновлять В ОБОИХ ФАЙЛАХ синхронно, иначе ноги разойдутся.
T_CRITICAL = 2.231351
T_SIGNAL = 1.608964

BASELINE_WINDOW = 14      # канон [РЕШЕНИЕ VIKTOR 15.08.2026]

FUNDING_FLAT_THRESHOLD = 0.01     # % за 8ч, ниже — считаем "плоско"
LS_RATIO_STABILITY_WINDOW = 5     # последних точек для оценки стабильности

# [ПАТЧ B4] Порог устаревания skew_block_output.json, часов.
BLOCK_STALE_HOURS = 6


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


def load_block_verdict():
    """Вердикт из skew_block_output.json — ЕДИНСТВЕННЫЙ источник правды
    по §10.3. Кросс-чек не должен иметь собственного мнения о том, что
    считать пробоем: до патча 13.09.2026 он считал свой z только по
    классической ноге и пропустил все три фактические эскалации."""
    if not os.path.exists(BLOCK_OUTPUT_PATH):
        print("[WARN] skew_block_output.json не найден — откат на собственный z")
        return None
    try:
        with open(BLOCK_OUTPUT_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] cannot read skew_block_output.json: {e}")
        return None


def block_age_hours(block):
    """[ПАТЧ B4] Возраст вердикта в часах, или None если не определить.
    Файл перезаписывается каждым прогоном; если skew_block упал или ещё
    не отработал, здесь читался бы вчерашний вердикт молча."""
    if not block:
        return None
    ts = block.get("generated_at_utc")
    if not ts:
        return None
    try:
        gen = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - gen).total_seconds() / 3600.0
    except Exception:
        return None


def compute_skew_zscore(history, lookback_days=BASELINE_WINDOW):
    """Z-score последней точки против trailing mean/std.

    Канон [РЕШЕНИЕ VIKTOR 15.08.2026]: baseline = trailing 14 точек,
    EXCLUDING текущую.

    [ПАТЧ A1, 13.09.2026] ВЫБОРОЧНЫЙ std (делитель n-1), было population
    (делитель n). Тождественно skew_block.compute_classical_zscore —
    иначе две ноги одной методологии расходятся в sqrt(14/13)=1.0377 раза.
    """
    if not history or len(history) < lookback_days + 1:
        return None

    window = history[-(lookback_days + 1):]
    baseline_points = window[:-1]
    latest_point = window[-1]
    baseline_values = [pt["skew"] for pt in baseline_points]
    latest = latest_point["skew"]

    n = len(baseline_values)
    mean = sum(baseline_values) / n
    # [ПАТЧ A1] делитель n-1
    variance = sum((v - mean) ** 2 for v in baseline_values) / (n - 1)
    std = variance ** 0.5
    z = (latest - mean) / std if std != 0 else None

    return {
        "latest": latest,
        "latest_date": latest_point.get("date"),
        "mean": mean,
        "std": std,
        "zscore": z,
        "baseline_window": lookback_days,
        "baseline_start": baseline_points[0].get("date"),
        "baseline_end": baseline_points[-1].get("date"),
        "includes_current": False,
        "std_divisor": "n-1",
        "formula_version": "v2-corrected-2026-09-13",
    }


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

    ⚠️ ВСЕ ТРИ ПОРОГА КАБИНЕТНЫЕ, эмпирикой не подтверждены. Ветка
    исполнялась 7 раз (август 2026), все — CALL_PREMIUM. Ни разу при
    DIVERGENT. Пороги не правятся без калибровки — см. шапку модуля.
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


def resolve_gate(block, z, force_check):
    """[ПАТЧ B2, 13.09.2026] Условие запуска кросс-чека.

    Гейт по ВЕРДИКТУ skew_block, не по собственному z. Запуск при
    escalate=True (DIVERGENT — нужна третья нога для ручного решения
    Viktor) либо при CRITICAL (обе ноги в полосе C).

    Возвращает (breach, gate_dict).
    """
    combined = (block or {}).get("classification_combined") or {}
    verdict = combined.get("verdict")
    escalate = bool(combined.get("escalate"))
    age = block_age_hours(block)
    stale = age is not None and age > BLOCK_STALE_HOURS

    if force_check:
        breach, reason = True, "force_check=True — ручной прогон"
    elif block is None:
        # HARD RULE: молчаливого NORMAL быть не должно. Откат на
        # собственный z с t-порогом, факт отката пишется в отчёт.
        if z is not None and abs(z) >= T_CRITICAL:
            breach = True
            reason = f"ОТКАТ (нет skew_block_output.json): собственный z={z:.3f} >= {T_CRITICAL}"
        else:
            breach = False
            reason = "ОТКАТ (нет skew_block_output.json): собственный z ниже порога"
    elif escalate:
        breach, reason = True, f"escalate=True, вердикт {verdict}"
    elif verdict and str(verdict).startswith("CRITICAL"):
        breach, reason = True, f"вердикт {verdict}"
    else:
        breach, reason = False, f"вердикт {verdict or NO_DATA} — кросс-чек не требуется"

    gate = {
        "source": "skew_block_output.json" if block else "FALLBACK: собственный z",
        "block_verdict": verdict or NO_DATA,
        "block_escalate": escalate,
        "block_generated_at_utc": (block or {}).get("generated_at_utc", NO_DATA),
        "block_age_hours": round(age, 2) if age is not None else NO_DATA,
        "block_stale": stale,
        "reason": reason,
    }
    if stale:
        gate["stale_warning"] = (
            f"⚠️ skew_block_output.json старше {BLOCK_STALE_HOURS}ч "
            f"({round(age, 1)}ч). Вердикт может относиться к ПРЕДЫДУЩЕМУ дню — "
            f"skew_block не отработал или упал."
        )
        print(f"[WARN] {gate['stale_warning']}")

    return breach, gate


def run(force_check=False):
    timestamp = datetime.now(timezone.utc).isoformat()
    history = load_skew_history()
    block = load_block_verdict()

    zdata = compute_skew_zscore(history) if history else None
    z = zdata["zscore"] if zdata else None

    breach, gate = resolve_gate(block, z, force_check)

    result = {
        "timestamp_utc": timestamp,
        "skew_latest": zdata["latest"] if zdata else NO_DATA,
        "skew_trailing_mean_14d": round(zdata["mean"], 3) if zdata else NO_DATA,
        "skew_trailing_std_14d": round(zdata["std"], 4) if zdata else NO_DATA,
        "skew_zscore": round(z, 4) if z is not None else NO_DATA,
        "std_divisor": "n-1",
        "thresholds": {"critical": T_CRITICAL, "signal": T_SIGNAL},
        "formula_version": "v2-corrected-2026-09-13",
        "critical_breach": breach,
        "gate": gate,
        "baseline": {
            "window": zdata["baseline_window"],
            "start": zdata["baseline_start"],
            "end": zdata["baseline_end"],
            "includes_current": zdata["includes_current"],
            "latest_date": zdata["latest_date"],
        } if zdata else NO_DATA,
    }

    if not breach:
        # [ПАТЧ B3, 13.09.2026] Строка печатала «Skew в пределах нормы»
        # при ЛЮБОМ breach=False, включая вердикт DIVERGENT (12.09 — так
        # и произошло). Класс §12 №34, NORMAL-маскировка. Печатается ФАКТ.
        result["note"] = (
            f"Кросс-чек не запускался. Вердикт skew_block: "
            f"{gate['block_verdict']} (escalate={gate['block_escalate']}). "
            f"Причина: {gate['reason']}. Ручной прогон: force_check=True."
        )
        with open(OUTPUT_PATH, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\nЗаписано в {OUTPUT_PATH}")
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
        "thresholds_uncalibrated": (
            "⚠️ funding 0.01%/8ч КОНФЛИКТУЕТ с мёртвой зоной §10.2 (0.005%); "
            "basis 0.5% — нога вычеркнута из §10.5 по §12 №35 (отрицателен "
            "99.00% дней); ls spread 0.05 за 25 мин. Все пороги кабинетные."
        ),
        "not_automated": "CME vs aggregate exchange OI divergence — Coinglass per-exchange breakdown is JS-rendered, requires manual check at coinglass.com/ru/open-interest/BTC",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nЗаписано в {OUTPUT_PATH}")
    return result


if __name__ == "__main__":
    # force_check=True прогоняет сразу, независимо от вердикта — удобно для
    # ручной проверки. В проде (cron/Routine) держать False: кросс-чек
    # запускается по вердикту skew_block (escalate=True либо CRITICAL).
    run(force_check=False)
