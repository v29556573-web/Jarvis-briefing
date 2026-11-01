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

=====================================================================
[ПАТЧ 01.11.2026 — Mark50-Judge · «Око»: третья нога молчала с августа]
=====================================================================
  ДИАГНОЗ (лог GitHub Actions 31.10 07:15:44 UTC): все три запроса к
  api.bybit.com -> 403 Forbidden. Bybit режет облачные IP (как Colab).
  Архив 22.08 — те же «ДАННЫЕ НЕ ПОЛУЧЕНЫ»: кросс-чек не получал ни одной
  ноги минимум с 22.08, а job оставался зелёным (молчаливый сбой).

  C1. ЦЕПОЧКА ХОСТОВ Bybit: api.bybit.com -> api.bytick.com (официальный
      резервный домен Bybit, тот же источник §2.8).
  C2. РЕЗЕРВ MEXC (contract.mexc.com) для funding и basis [ВКЛЮЧЁН Viktor 01.11] — при
      ALLOW_MEXC_FALLBACK = True (решение Viktor по §2.8: MEXC — источник
      данных «пока да», 22.10; способ доступа — публичный API). L/S у MEXC
      публично нет — нога остаётся только Bybit.
  C3. ПРОВЕНАНС: в выводе у каждой ноги — фактический источник и хост.
  C4. ГРОМКИЙ СБОЙ: если кросс-чек ДОЛЖЕН был работать (breach) и не
      получил НИ ОДНОЙ ноги — выход с кодом 2 -> шаг «Fail if cross-check
      failed» краснеет. Точка skew при этом сохраняется (if: always()).
  C5. PROBE: python skew_crosscheck.py --probe — печатает HTTP-статус
      каждого источника, ничего не пишет.
  Пороги и логика classify_convergence НЕ менялись.

=====================================================================
[АКТ Viktor 01.11.2026 «Ратифицирую» — rule_version crosscheck-v1.2]
=====================================================================
  D1. ФАНДИНГ ПО §10.2: d = f − 0.01% (0.01%/8h — ноль). Мёртвая зона
      считается по |d| < 0.005% (было: |funding| < 0.01% — двойной порог
      на одном поле, [UNRECONCILED] снят актом). Зачёт — по 3 последним
      РАСЧЁТАМ (settled), не по текущему принту:
        все три |d| < 0.005%           -> benign
        все три d ≥ +0.005%            -> diverging (перегретые лонги)
        все три d ≤ −0.005%            -> НЕ ЗАЧТЕНО: §10.2 [10.08] «d ≤ 0 —
                                          не направленный сигнал» (уточнение
                                          Judge к акту — на подтверждение Viktor)
        иначе                           -> НЕ ЗАЧТЕНО (не голосует)
      Источник расчётов: Bybit (bybit -> bytick) -> MEXC history (резерв C2).
  D2. НОГА L/S ОТКЛЮЧЕНА до калибровки (Bybit/Binance закрыты для GitHub;
      Kraken вне §2.8; порог кабинетный). Не запрашивается, не голосует.
  D3. rule_version в выводе — вердикты разных версий правила не смешиваются
      (история для PR-18).
  Порог basis 0.5% не менялся (§12 №35 — открыт).

=====================================================================
[АКТ Viktor 01.11.2026 (2) — rule_version crosscheck-v1.3]
=====================================================================
  E1. Асимметрия фандинга подтверждена Viktor: d ≤ −0.005% ×3 -> НЕ ЗАЧТЕНО
      с пометкой «временно, до калибровки §10.2» (цена: настоящий
      отрицательный фандинг не голосует). Сырые d пишутся в
      crosscheck_funding_history.json КАЖДЫЙ день (и без пробоя).
  E2. БАЗИС -> НЕ ЗАЧТЕНО: порог 0.5% вычеркнут в §10.5 (§12 №35) —
      два порога на одном поле = [UNRECONCILED], нога не голосует.
      Значение пишется как описание.
  E3. voting_legs в выводе. Итог при < 2 голосующих ногах:
      «НЕДОСТАТОЧНО НОГ» — отсутствие тревоги, НЕ подтверждение;
      в R3′ и PR-18 как подтверждение не читается.

Запуск:
  pip install requests --break-system-packages
  python3 skew_crosscheck.py
"""

import json
import os
import sys
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

# [ПАТЧ C1, 01.11.2026] хосты Bybit по порядку; пути отдельно.
BYBIT_HOSTS = ["https://api.bybit.com", "https://api.bytick.com"]
BYBIT_FUNDING_PATH = "/v5/market/funding/history"
BYBIT_OI_PATH = "/v5/market/open-interest"
BYBIT_LS_RATIO_PATH = "/v5/market/account-ratio"
BYBIT_TICKERS_PATH = "/v5/market/tickers"
# [ПРЕДЫДУЩЕЕ, до 01.11.2026] BYBIT_*_URL = "https://api.bybit.com/v5/market/..."
#   — единственный хост, 403 с облачных IP GitHub.

# [ПАТЧ C2, 01.11.2026] резерв MEXC. ⛔ Включается ТОЛЬКО решением Viktor (§2.8).
ALLOW_MEXC_FALLBACK = True   # [РЕШЕНИЕ Viktor 01.11.2026: «да, включай»]
MEXC_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_SYMBOL = "BTC_USDT"
MEXC_FUNDING_HISTORY_URL = "https://contract.mexc.com/api/v1/contract/funding_rate/history"

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

# [ПРЕДЫДУЩЕЕ, до 01.11.2026] FUNDING_FLAT_THRESHOLD = 0.01 (% за 8ч, по сырому |funding|).
# В расчёте НЕ УЧАСТВУЕТ. Заменён актом D1:
RULE_VERSION = "crosscheck-v1.3-2026-11-01"
# [ПРЕДЫДУЩЕЕ] crosscheck-v1.2-2026-11-01 (базис голосовал; итог без счёта ног)
FUNDING_BASE = 0.01               # % за 8ч — кламп/ноль §10.2
FUNDING_DEAD_ZONE_D = 0.005       # |d| < этого -> нейтрально (§10.2, по d — акт 01.11)
FUNDING_SETTLEMENTS = 3           # персистентность §10.2
NOT_COUNTED = "НЕ ЗАЧТЕНО"
LS_DISABLED = "ОТКЛЮЧЕНО"
FUNDING_HISTORY_PATH = os.path.join(_HERE, "crosscheck_funding_history.json")
MIN_VOTING_LEGS = 2
LS_RATIO_STABILITY_WINDOW = 5     # последних точек для оценки стабильности

# [ПАТЧ B4] Порог устаревания skew_block_output.json, часов.
BLOCK_STALE_HOURS = 6


def safe_get(url, params=None, timeout=15):
    """Один запрос к Bybit. Возвращает result или None."""
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": "jarvis-crosscheck/1.1"})
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            print(f"[WARN] Bybit API error: {data.get('retMsg')}")
            return None
        return data.get("result")
    except Exception as e:
        print(f"[WARN] fetch failed: {url} -> {e}")
        return None


def bybit_get(path, params):
    """[ПАТЧ C1] Перебор хостов Bybit. Возвращает (result, host) или (None, None)."""
    for host in BYBIT_HOSTS:
        res = safe_get(host + path, params=params)
        if res is not None:
            return res, host
    return None, None


def mexc_ticker():
    """[ПАТЧ C2] Тикер перпа MEXC: fundingRate (текущий, доля), fairPrice, indexPrice."""
    if not ALLOW_MEXC_FALLBACK:
        return None
    try:
        r = requests.get(MEXC_TICKER_URL, params={"symbol": MEXC_SYMBOL}, timeout=15,
                         headers={"User-Agent": "jarvis-crosscheck/1.1"})
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            print(f"[WARN] MEXC API error: {d.get('code')} {d.get('message')}")
            return None
        return d.get("data")
    except Exception as e:
        print(f"[WARN] fetch failed: {MEXC_TICKER_URL} -> {e}")
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


SOURCES = {}   # [ПАТЧ C3] провенанс ног: metric -> строка источника


SETTLE_TIMES = []   # [ревью 01.11 п.7] время расчётов (UTC мс), от нового к старому


def get_funding_settlements(n=FUNDING_SETTLEMENTS):
    """[АКТ D1] Последние n РАСЧЁТОВ фандинга, в %, от нового к старому.
    [ревью 01.11 п.9] Bybit — явная сортировка по времени и защита от кривого ответа."""
    del SETTLE_TIMES[:]
    result, host = bybit_get(BYBIT_FUNDING_PATH, {"category": "linear", "symbol": SYMBOL, "limit": n})
    try:
        if result and len(result.get("list") or []) >= n:
            lst = sorted(result["list"], key=lambda x: int(x["fundingRateTimestamp"]), reverse=True)[:n]
            vals = [float(x["fundingRate"]) * 100 for x in lst]
            SETTLE_TIMES.extend(int(x["fundingRateTimestamp"]) for x in lst)
            SOURCES["funding"] = f"Bybit {host} — {n} последних расчётов"
            return vals
    except Exception as e:
        print(f"[WARN] Bybit funding: неожиданный формат ответа -> {e}")
    if ALLOW_MEXC_FALLBACK:
        try:
            r = requests.get(MEXC_FUNDING_HISTORY_URL,
                             params={"symbol": MEXC_SYMBOL, "page_num": 1, "page_size": n},
                             timeout=15, headers={"User-Agent": "jarvis-crosscheck/1.2"})
            r.raise_for_status()
            d = r.json()
            lst = ((d.get("data") or {}).get("resultList") or []) if d.get("success") else []
            if len(lst) >= n:
                lst = sorted(lst, key=lambda x: x["settleTime"], reverse=True)[:n]
                vals = [float(x["fundingRate"]) * 100 for x in lst]
                SETTLE_TIMES.extend(int(x["settleTime"]) for x in lst)
                SOURCES["funding"] = "MEXC contract API — " + f"{n} последних расчётов (settled), резерв C2"
                return vals
        except Exception as e:
            print(f"[WARN] fetch failed: {MEXC_FUNDING_HISTORY_URL} -> {e}")
    SOURCES["funding"] = NO_DATA
    return None


def classify_funding(settled):
    """[АКТ D1] Возвращает (verdict, detail) по d = f − 0.01%, 3 расчёта."""
    if not settled:
        return NO_DATA, NO_DATA
    ds = [round(f - FUNDING_BASE, 8) for f in settled]   # [ревью 01.11 п.4] округление на границе 0.005
    txt = ", ".join(f"{f:.4f}% (d {d:+.4f})" for f, d in zip(settled, ds))
    if all(abs(d) < FUNDING_DEAD_ZONE_D for d in ds):
        return "benign", f"3 расчёта в мёртвой зоне |d|<{FUNDING_DEAD_ZONE_D}%: {txt}"
    if all(d >= FUNDING_DEAD_ZONE_D for d in ds):
        return "diverging", f"3 расчёта подряд выше зоны, перегретые лонги: {txt}"
    if all(d <= -FUNDING_DEAD_ZONE_D for d in ds):
        # Не голосует ВРЕМЕННО, до калибровки §10.2 [акт 01.11].
        # ⚠ [ревью 01.11 C.2] цифра §10.2 «97.42%» посчитана для F − 0.03%, к d = F − 0.01%
        # неприменима; обоснование асимметрии — на решение Viktor.
        return NOT_COUNTED, f"d < 0 три расчёта — не голосует ВРЕМЕННО, до калибровки §10.2 [акт 01.11]: {txt}"
    return NOT_COUNTED, f"персистентность не выполнена (§10.2): {txt}"


def get_funding_rate():
    """[ПРЕДЫДУЩЕЕ, до 01.11.2026] один последний принт. В run() НЕ вызывается."""
    result, host = bybit_get(BYBIT_FUNDING_PATH, {"category": "linear", "symbol": SYMBOL, "limit": 1})
    if result and result.get("list"):
        SOURCES["funding"] = f"Bybit {host} — последний расчёт (settled)"
        return float(result["list"][0]["fundingRate"]) * 100  # в %
    t = mexc_ticker()
    if t and t.get("fundingRate") is not None:
        SOURCES["funding"] = "MEXC contract API — ТЕКУЩАЯ ставка (не settled), резерв C2"
        return float(t["fundingRate"]) * 100
    SOURCES["funding"] = NO_DATA
    return None


def get_ls_ratio_stability():
    """Возвращает (текущее значение, стабильно ли за последние N точек)."""
    result, host = bybit_get(BYBIT_LS_RATIO_PATH, {"category": "linear", "symbol": SYMBOL, "period": "5min", "limit": LS_RATIO_STABILITY_WINDOW})
    if not result or not result.get("list"):
        SOURCES["ls_ratio"] = NO_DATA + " (резерва нет: у MEXC публичного L/S нет)"
        return None, None
    SOURCES["ls_ratio"] = f"Bybit {host}"

    ratios = [float(pt["buyRatio"]) for pt in result["list"]]
    if len(ratios) < 2:
        return ratios[0] if ratios else None, None

    spread = max(ratios) - min(ratios)
    stable = spread < 0.05  # менее 5 п.п. разброса за окно = стабильно
    return ratios[-1], stable


def get_basis():
    result, host = bybit_get(BYBIT_TICKERS_PATH, {"category": "linear", "symbol": SYMBOL})
    if not result or not result.get("list"):
        t = mexc_ticker()
        if t and t.get("fairPrice") and t.get("indexPrice"):
            fp, ip = float(t["fairPrice"]), float(t["indexPrice"])
            SOURCES["basis"] = "MEXC contract API — fairPrice vs indexPrice, резерв C2"
            return fp - ip, (fp - ip) / ip * 100
        SOURCES["basis"] = NO_DATA
        return None, None
    SOURCES["basis"] = f"Bybit {host} — markPrice vs indexPrice"

    ticker = result["list"][0]
    mark_price = float(ticker.get("markPrice", 0))
    index_price = float(ticker.get("indexPrice", 0))
    if index_price == 0:
        return None, None

    basis_abs = mark_price - index_price
    basis_pct = basis_abs / index_price * 100
    return basis_abs, basis_pct


def classify_convergence(funding, ls_stable, basis_pct, funding_settled=None):
    """Применяет Convergence Rule из протокола.

    ⚠️ ВСЕ ТРИ ПОРОГА КАБИНЕТНЫЕ, эмпирикой не подтверждены. Ветка
    исполнялась 7 раз (август 2026), все — CALL_PREMIUM. Ни разу при
    DIVERGENT. Пороги не правятся без калибровки — см. шапку модуля.
    """
    signals = []

    # [АКТ D1, 01.11.2026] фандинг по d и 3 расчётам
    fv, fdet = classify_funding(funding_settled)
    signals.append(("funding", fv, fdet))

    # [АКТ D2, 01.11.2026] нога L/S отключена до калибровки
    if ls_stable == LS_DISABLED:
        signals.append(("ls_ratio", LS_DISABLED, "нога отключена до калибровки [акт Viktor 01.11.2026]"))
    elif ls_stable is not None:
        if ls_stable:
            signals.append(("ls_ratio", "benign", "стабильно, нет резкого сдвига толпы"))
        else:
            signals.append(("ls_ratio", "diverging", "резкий сдвиг — возможен паникующий рынок"))
    else:
        signals.append(("ls_ratio", NO_DATA, NO_DATA))

    # [АКТ E2, 01.11.2026] базис не голосует: порог 0.5% вычеркнут в §10.5 (§12 №35)
    if basis_pct is not None:
        signals.append(("basis", NOT_COUNTED,
                        f"{basis_pct:.3f}% — описание; порог 0.5% вычеркнут (§10.5, §12 №35) — [UNRECONCILED], нога не голосует"))
    else:
        signals.append(("basis", NO_DATA, NO_DATA))

    benign_count = sum(1 for _, verdict, _ in signals if verdict == "benign")
    diverging_count = sum(1 for _, verdict, _ in signals if verdict == "diverging")
    no_data_count = sum(1 for _, verdict, _ in signals if verdict == NO_DATA)
    active = sum(1 for _, verdict, _ in signals if verdict != LS_DISABLED)
    voting = benign_count + diverging_count
    # [ревью 01.11 п.10] знаменатель фактический (§10.6): только ноги, способные голосовать
    capable = sum(1 for m, v, _ in signals if v != LS_DISABLED and m != "basis")

    if diverging_count > 0:
        overall = "AMBIGUOUS — минимум одна метрика расходится, ждать подтверждения ценой (CHoCH/BOS)"
    elif no_data_count == active:
        overall = NO_DATA
    elif voting < MIN_VOTING_LEGS:
        overall = (f"НЕДОСТАТОЧНО НОГ — голосует {voting} из {capable}; отсутствие тревоги, "
                   f"НЕ подтверждение (в R3′ и PR-18 не читается как подтверждение)")
    elif benign_count >= 2:
        overall = "LEANS BENIGN/BULLISH — метрики согласованы, склоняется к accumulation/hedge-unwind, не к vol crush"
    else:
        overall = "INCONCLUSIVE — недостаточно данных для уверенного вывода"

    return signals, overall, f"{voting} из {capable} (L/S отключена; базис не голосует)"


def record_funding_history():
    """[АКТ E1] Ежедневная запись сырых d (append-only, по времени расчёта)."""
    settled = get_funding_settlements()
    try:
        hist = json.load(open(FUNDING_HISTORY_PATH)) if os.path.exists(FUNDING_HISTORY_PATH) else []
        if not isinstance(hist, list):
            raise ValueError("ожидался список")
    except Exception as e:
        # [ревью 01.11 п.6] испорченную историю НЕ обнуляем — громко, без перезаписи
        print(f"::error::crosscheck_funding_history.json не читается ({e}) — запись дня пропущена, файл не тронут")
        return settled
    if not settled:
        # [ревью 01.11 п.8] пропуск дня виден в истории
        hist.append({"recorded_utc": datetime.now(timezone.utc).isoformat(),
                     "source": NO_DATA, "rule_version": RULE_VERSION})
        with open(FUNDING_HISTORY_PATH, "w") as f:
            json.dump(hist, f, indent=1, ensure_ascii=False)
        return None
    hist.append({
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "settle_time_utc_ms": list(SETTLE_TIMES),
        "source": SOURCES.get("funding"),
        "settled_pct_newest_first": settled,
        "d_pct": [round(f - FUNDING_BASE, 6) for f in settled],
        "rule_version": RULE_VERSION,
    })
    with open(FUNDING_HISTORY_PATH, "w") as f:
        json.dump(hist, f, indent=1, ensure_ascii=False)
    return settled


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
    elif verdict and str(verdict).startswith(("CRITICAL", "SIGNAL")):
        # [ревью 01.11 п.3] гейт §10.3 [RATIFIED 18.09]: escalate ∨ CRITICAL_* ∨ SIGNAL_*
        # (в коде с 18.09 не был обновлён). SIGNAL — верификация, не тревога.
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
        "rule_version": RULE_VERSION,
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

    # [АКТ E1] сырые d — каждый день, независимо от пробоя
    daily_settled = record_funding_history()

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

    # [АКТ 01.11.2026] фандинг — 3 расчёта; L/S отключена; basis без изменений
    funding_settled = daily_settled
    funding = funding_settled[0] if funding_settled else None
    ls_latest, ls_stable = None, LS_DISABLED
    SOURCES["ls_ratio"] = LS_DISABLED + " (акт 01.11.2026)"
    basis_abs, basis_pct = get_basis()

    signals, overall, voting = classify_convergence(funding, ls_stable, basis_pct, funding_settled)

    result["crosscheck"] = {
        "funding_rate_pct_8h": funding if funding is not None else NO_DATA,
        "funding_settled_pct": funding_settled if funding_settled else NO_DATA,
        "funding_d_pct": [round(f - FUNDING_BASE, 6) for f in funding_settled] if funding_settled else NO_DATA,
        "ls_ratio_latest": LS_DISABLED,
        "ls_ratio_stable": LS_DISABLED,
        "basis_pct": round(basis_pct, 4) if basis_pct is not None else NO_DATA,
        "sources": dict(SOURCES),
        "voting_legs": voting,
        "signals": [{"metric": m, "verdict": v, "detail": d} for m, v, d in signals],
        "overall_verdict": overall,
        "thresholds_uncalibrated": (
            "funding: d = f − 0.01%, |d| < 0.005%, 3 расчёта; d<0 не голосует ВРЕМЕННО (§10.2, акт 01.11; калибровка не завершена); "
            "basis — описание, не голосует ([UNRECONCILED], §10.5/§12 №35); L/S отключена (акт 01.11)."
        ),
        "not_automated": "CME vs aggregate exchange OI divergence — Coinglass per-exchange breakdown is JS-rendered, requires manual check at coinglass.com/ru/open-interest/BTC",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nЗаписано в {OUTPUT_PATH}")

    # [ПАТЧ C4] громкий сбой: кросс-чек был обязан работать и не получил ничего
    if funding is None and basis_pct is None:
        print("::error::Кросс-чек §10.3: НИ ОДНОЙ ноги не получено "
              f"(источники: {SOURCES}). Вывод записан, точка skew цела.")
        sys.exit(2)
    return result


def probe():
    """[ПАТЧ C5] Только диагностика доступа: HTTP-статус каждого источника."""
    targets = [(h + BYBIT_TICKERS_PATH, {"category": "linear", "symbol": SYMBOL}) for h in BYBIT_HOSTS]
    targets.append((MEXC_TICKER_URL, {"symbol": MEXC_SYMBOL}))
    for url, params in targets:
        try:
            r = requests.get(url, params=params, timeout=15,
                             headers={"User-Agent": "jarvis-crosscheck/1.1"})
            print(f"[PROBE] {r.status_code}  {url}  {r.text[:120]!r}")
        except Exception as e:
            print(f"[PROBE] ERR  {url}  {e}")


if __name__ == "__main__":
    # force_check=True прогоняет сразу, независимо от вердикта — удобно для
    # ручной проверки. В проде (cron/Routine) держать False: кросс-чек
    # запускается по вердикту skew_block (escalate=True либо CRITICAL).
    if "--probe" in sys.argv:
        probe()
    else:
        run(force_check=False)
