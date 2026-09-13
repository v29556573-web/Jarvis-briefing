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

[РЕШЕНИЕ VIKTOR 29.08.2026, append-лог вердиктов — закрывает дефект R3′]:
BLOCK_OUTPUT_FILE (skew_block_output.json) пишется в режиме "w" — каждый
прогон полностью перезаписывает предыдущий. Это означает, что z_detrended,
z_classical и classification_combined ЖИВУТ только до следующего запуска;
разбор задним числом (аудит правила §10.3, накопление кейсов skew→структура)
опирался фактически на телеграм-скриншоты Pre-Market/Daily Check, а не на
данные в репозитории. Добавлен VERDICT_HISTORY_FILE (skew_verdict_history.json,
append-only, НЕ last-write-wins на дату — в отличие от skew_history.json,
здесь намеренно сохраняется КАЖДЫЙ прогон отдельной записью, включая
множественные запуски в один день: это и есть строки, которых не хватало
для аудита разброса времени съёма, найденного 29.08.2026). Не входит в
расчёт z-score/baseline (тот считается только по skew_history.json,
как раньше) — чисто аудит-лог эмитированных вердиктов.

[РЕШЕНИЕ VIKTOR 28.08.2026 → В КОД 01.09.2026, R3′ двойного чтения]:
combined_classification() РАНЕЕ реализовывал правило R2 (акт 23.08):
CRITICAL только при согласии обеих ног ≥2.0/знак; NORMAL если обе <1.5;
иначе DIVERGENT+escalate. Поле "rule" в append-логе при этом писало
"R3prime-2026-08-28", хотя код выдавал R2 — расхождение зафиксировано
29.08 (аудит показывал бы ложные срабатывания R3′). Настоящей правкой
R3′ внесён в код, поле "rule" перестаёт врать.

  R3′ — правило ОТЧЁТНОСТИ, не предиктор. Против цены не оценивается
  ни при каком исходе (решение Viktor 28.08). Полосы на ногу:
    N |z|<thr_S · S thr_S≤|z|<thr_C · C |z|≥thr_C
  1) обе ноги вне N И знаки различаются → DIVERGENT, escalate
     [документированная инверсия]
  2) пара полос (N,C) в любом порядке → DIVERGENT, escalate
     [разрыв в две полосы]
  3) иначе → вердикт по ноге с бо́льшим |z|, её знак, escalate=False.
     CRITICAL требует ОБЕ ноги в полосе C (сохранение акта 23.08);
     пара (C,S) даёт SIGNAL по знаку старшей ноги.
  ВРЕМЕННОЕ. Точка пересмотра: 3-е стресс-событие ИЛИ 30.09.2026, что позже.

=====================================================================
[ПАТЧ ЭТАП 1, 12.09.2026 — ратифицировано Viktor 12.09.2026]
=====================================================================
Три исправления АРИФМЕТИКИ. Ни одно не является подбором параметра,
калибровки не требуют. Логика R3′ НЕ МЕНЯЛАСЬ.

  A. ДЕЛИТЕЛЬ. Остатки регрессии делились на n (pstdev). Оценены ДВА
     параметра (наклон, сдвиг) -> несмещённый делитель n-2. Классическая
     нога: выборочный std требует n-1, было n.

  B. ДИСПЕРСИЯ ПРОГНОЗА. Остаток берётся против ЭКСТРАПОЛЯЦИИ тренда на
     t=W. У предсказания своя погрешность: разброс (факт - прогноз) шире
     sigma в sqrt(1 + 1/W + (W-tb)^2/Sxx) раз. При W=14 = 1.1483.
     Нормировка шла на голую sigma.

  C. ПОЛОСЫ В ВЕРОЯТНОСТИ, НЕ В СИГМАХ. Пороги 2.0/1.5 подразумевают
     хвосты 4.55%/13.36%. При ОЦЕНЁННОЙ sigma распределение t(W-2), не
     нормальное. Пороги = t-квантили для тех же вероятностей. При W=14:
     C=2.2314, S=1.6090. При W->inf сходятся к 2.0/1.5.

  D. ОКРУГЛЕНИЕ. round(z,2) шло в combined_classification для детренд-ноги,
     а классическая передавалась сырой — асимметрия. z=1.9951 округлялся
     до 2.0 и попадал в полосу C. Округление убрано из управляющего пути.

  E. ПОВТОРНЫЙ ПРОГОН В СУТКИ. load_history() читает файл, уже содержащий
     сегодняшнюю точку от раннего прогона; append_today() чистит дубль
     ПОСЛЕ расчёта. Baseline второго прогона включал собственную утреннюю
     запись, а includes_current:False лгало. Чистим ДО расчёта.

СОВОКУПНЫЙ ЭФФЕКТ на ряде 47 точек (26.07-12.09.2026):
  вердикт меняется на 10 днях из 33 · |z| в полосе C: 27.3% -> 15.2%
  эскалаций DIVERGENT: 8 -> 5

ЗАЯВЛЕНО ДО ВНЕДРЕНИЯ: Этап 1 калибровку НЕ ЗАКРЫВАЕТ. Цель 4.6%,
остаётся превышение в 3.3x — это ДЛИНА ОКНА, Этап 2 (пре-регистрация,
выборка строго с 13.09.2026). Коридор проверки через 30 дней: 2-9%.

НЕ СДЕЛАНО ЭТИМ ПАТЧЕМ (отдельные пункты, молча не вносить):
  - длина окна W=14 -> Этап 2
  - skew_crosscheck.py НЕ ПРИВЕДЁН В СООТВЕТСТВИЕ: там pstdev (делитель n),
    здесь stdev (n-1). Отношение sqrt(14/13)=1.0377 — две ноги одной
    методологии считают РАЗНЫМИ формулами. Докстринг ниже утверждает
    тождественность — на 12.09.2026 это НЕВЕРНО. Требует правки.
  - гейт кросс-чека на critical_breach вместо escalate
  - строка "Skew в пределах нормы" при вердикте DIVERGENT
  - переименование меток CRITICAL_PUT_PREMIUM -> PUT_PREMIUM_SPIKE
  - NORMAL-маскировка при z_detr is None (§12, зарегистрирован 29.08)
  - контаминированные точки 28.08 и 29.08 внутри рабочей базы
  - первые 19 точек ряда без поля value_type

=====================================================================
[СБОР VRP, 12.09.2026 — ЗАЧЕМ СУЩЕСТВУЕТ vol_history.json]
=====================================================================
>>> ЭТОТ БЛОК НИЧЕГО НЕ РЕШАЕТ И НИ НА ЧТО НЕ ВЛИЯЕТ. <<<
Он только КОПИТ данные. Ни одна рутина, ни один вердикт, ни одно
торговое решение его не читают. Если однажды окажется, что кто-то
завёл на него логику — это ДЕФЕКТ, а не задумка.

ПОЧЕМУ ЗАВЕДЁН (исследование VRP, 08.09.2026):
  Проверялась гипотеза "премия IV-RV на BTC систематически положительна".
  Результат [MEASURED, n=170, 19.03-04.09.2026]: ПОДТВЕРЖДЕНА.
    медиана премии +4.99 п.п. · доля положительных дней 84.12%
    три механических окна (трети выборки), включая окно с ценой -23.19%
    все три пререгистрированных критерия §13.8 пройдены
    худшая просадка -39.60 vol-pts за 10 дней = 7.1 дня типичной прибыли

  НО посчитать настоящую симуляцию продажи волатильности НЕ УДАЛОСЬ.
  Причина ровно одна: у нас не было УРОВНЕЙ implied vol.
    skew_history.json копит РАЗНОСТЬ put_iv - call_iv, не уровни.
    skew_block_output.json перезаписывается каждый прогон.
    DVOL (Deribit) — индекс, а не котировка инструмента.
  Пришлось считать линейное приближение IV-RV вместо P&L со страйками.
  Приближение ОПТИМИСТИЧНО по построению: у проданного стрэддла убыток
  растёт с КВАДРАТОМ движения (гамма), линейная мера этого не видит.

ЧТО ЭТОТ СБОР ДАСТ ЧЕРЕЗ 6-12 МЕСЯЦЕВ:
  Возможность прогнать симуляцию на РЕАЛЬНЫХ страйках и тенорах —
  с гаммой, вегой и издержками, а не на приближении. То есть ответить
  на вопрос, на который 08.09.2026 ответить было нечем: сколько ДЕНЕГ,
  а не сколько vol-points.

ЦЕНА (проверено до внедрения, вопрос Viktor о нагрузке на GitHub):
  новых API-запросов к Deribit для 25d/ATM:  0
      — тикеры всех страйков в ±25% УЖЕ перебираются в find_25delta_skew,
        уровни IV уже лежат в meta, ATM уже в памяти. Раньше выбрасывались.
  новых запросов:  +1 (DVOL, отдельный эндпоинт)
  новых workflow:  0      новых cron-заданий:  0
  прирост времени прогона:  ~0.3 сек (один HTTP-запрос)
  размер файла:  ~300 байт/день -> ~110 КБ/год
      — для сравнения: skew_history.json на 12.09.2026 ~4.5 КБ

ЕСЛИ VRP БУДЕТ ЗАБРАКОВАН — файл всё равно не вредит: 110 КБ/год и
ноль влияния на логику. Удаление сбора тогда — отдельный акт Viktor,
молча не вносить.

СТАТУС VRP на 12.09.2026: премия ДОКАЗАНА, стратегия НЕ ДОКАЗАНА,
допуск к исполнению НЕ ДАН. Хвост охарактеризован на ОДНОМ случае
(n=1 для просадки) — для допуска этого мало.

Зависимости: requests
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from statistics import mean, pstdev, stdev

import requests

DERIBIT_BASE = "https://www.deribit.com/api/v2"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_history.json")
INTRADAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_intraday.json")
BLOCK_OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_block_output.json")
VERDICT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skew_verdict_history.json")
# [СБОР VRP] Накопительный файл УРОВНЕЙ IV. Потребителей НЕТ — см. докстринг.
VOL_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vol_history.json")
HISTORY_MAX_DAYS = 90
INTRADAY_MAX_DAYS = 7
VERDICT_HISTORY_MAX_RECORDS = 500  # append-only; несколько записей в сутки — норма, не дедуплицируем по дате
# [СБОР VRP] ~3 года. VRP-симуляция требует длинного ряда с разными режимами,
# обрезать раньше бессмысленно. При ~300 байт/день это ~330 КБ на горизонте.
VOL_HISTORY_MAX_DAYS = 1100
BASELINE_WINDOW = 14  # канон [РЕШЕНИЕ VIKTOR 15.08.2026], единый для skew_crosscheck.py и skew_block.py
TARGET_DELTA = 0.25
TARGET_TENOR_DAYS = 30  # ищем экспирацию ближе всего к 30 дням вперёд
TIMEOUT = 10

# [ПРЕДЫДУЩЕЕ, 12.09.2026] Фиксированные полосы R3′ [РЕШЕНИЕ VIKTOR 28.08.2026].
# Заменены t-квантилями (патч Этап 1, дефект C) — при ОЦЕНЁННОЙ sigma
# распределение t(W-2), фиксированные 2.0/1.5 дают хвост шире заявленного.
# НЕ УДАЛЕНЫ (принцип: устаревшие параметры помечаются, не удаляются молча).
# В расчёте НЕ УЧАСТВУЮТ — управляющие пороги даёт band_thresholds().
SIGNAL_THRESHOLD = 1.5
CRITICAL_THRESHOLD = 2.0

SESSION = requests.Session()

# ---------------------------------------------------------------------------
# [ПАТЧ ЭТАП 1, 12.09.2026] t-КВАНТИЛИ ПОЛОС (дефект C)
# ---------------------------------------------------------------------------
# Пороги для хвостов 4.55% (полоса C) и 13.36% (полоса S) — те же
# вероятности, что подразумевались фиксированными 2.0/1.5.
# Таблица вместо scipy: прод-окружение зависимостей не имеет.
# Ключ = BASELINE_WINDOW, df = W-2.
T_QUANTILES = {
    10: (2.366419, 1.669345),
    11: (2.319809, 1.648750),
    12: (2.283682, 1.632614),
    13: (2.254866, 1.619633),
    14: (2.231351, 1.608964),
    15: (2.211801, 1.600042),
    16: (2.195291, 1.592469),
    17: (2.181166, 1.585962),
    18: (2.168943, 1.580310),
    19: (2.158263, 1.575355),
    20: (2.148852, 1.570977),
    21: (2.140497, 1.567079),
    22: (2.133028, 1.563587),
    23: (2.126313, 1.560441),
    24: (2.120243, 1.557592),
    25: (2.114729, 1.554999),
    26: (2.109699, 1.552630),
    27: (2.105091, 1.550457),
    28: (2.100854, 1.548456),
    29: (2.096945, 1.546608),
    30: (2.093328, 1.544896),
    31: (2.089971, 1.543305),
    32: (2.086847, 1.541823),
    33: (2.083933, 1.540440),
    34: (2.081207, 1.539145),
    35: (2.078654, 1.537930),
    36: (2.076255, 1.536789),
    37: (2.073999, 1.535715),
    38: (2.071873, 1.534701),
    39: (2.069865, 1.533744),
    40: (2.067966, 1.532838),
    41: (2.066168, 1.531979),
    42: (2.064462, 1.531165),
    43: (2.062842, 1.530390),
    44: (2.061302, 1.529654),
    45: (2.059835, 1.528952),
    46: (2.058437, 1.528283),
    47: (2.057102, 1.527644),
    48: (2.055828, 1.527034),
    49: (2.054608, 1.526450),
    50: (2.053441, 1.525890),
    51: (2.052323, 1.525354),
    52: (2.051251, 1.524840),
    53: (2.050221, 1.524346),
    54: (2.049233, 1.523871),
    55: (2.048282, 1.523415),
    56: (2.047368, 1.522976),
    57: (2.046487, 1.522553),
    58: (2.045638, 1.522145),
    59: (2.044820, 1.521752),
    60: (2.044031, 1.521372),
}


def band_thresholds(window=BASELINE_WINDOW):
    """Пороги (C, S) для базы длины window.
    Явный отказ вместо тихой подстановки 2.0/1.5 — иначе дефект C
    вернётся незаметно при первой же смене окна (Этап 2)."""
    if window not in T_QUANTILES:
        raise ValueError(
            f"нет t-квантилей для BASELINE_WINDOW={window}. "
            f"Добавить в T_QUANTILES явно. НЕ подставлять 2.0/1.5."
        )
    return T_QUANTILES[window]


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


# ---------------------------------------------------------------------------
# [СБОР VRP, 12.09.2026] DVOL — индекс волатильности Deribit
# ---------------------------------------------------------------------------

def get_dvol(currency="BTC"):
    """Последнее закрытие DVOL. ЕДИНСТВЕННЫЙ дополнительный сетевой запрос,
    добавленный сбором VRP (~0.3 сек).

    Свеча: [timestamp, open, high, low, close]. Берём close последней.
    Окно в 2 суток назад — гарантия, что свеча есть даже при сдвиге прогона.

    Мягкий отказ: при любой ошибке возвращает None. DVOL — накопительное
    поле, не управляющее; ронять из-за него боевой прогон НЕЛЬЗЯ.
    """
    try:
        now_ms = int(time.time() * 1000)
        result, err = safe_get(
            f"{DERIBIT_BASE}/public/get_volatility_index_data",
            {
                "currency": currency,
                "start_timestamp": now_ms - 2 * 86400 * 1000,
                "end_timestamp": now_ms,
                "resolution": 86400,
            },
        )
        if err or not result:
            return None
        data = result.get("data") or []
        if not data:
            return None
        return data[-1][4]  # close последней свечи
    except Exception:
        return None


def find_25delta_skew(currency="BTC"):
    """
    Возвращает (skew_pct, meta) или (None, error_str).
    meta содержит expiry, call/put strikes и их IV/delta — для прозрачности.

    [СБОР VRP, 12.09.2026] В те же циклы добавлен поиск ATM-страйка
    (ближайший к споте). Дополнительных сетевых запросов НЕТ: тикеры
    всех страйков в ±25% уже перебираются, ATM просто перестал
    выбрасываться. Поля atm_* уходят в meta и далее в vol_history.json.
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
    atm_call, atm_call_diff = None, None  # [СБОР VRP]
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
            best_call = (c["instrument_name"], ticker["mark_iv"], delta, c["strike"])
        # [СБОР VRP] ATM-колл: минимум |strike - spot| среди тех же тикеров
        sdiff = abs(c["strike"] - underlying_price)
        if atm_call_diff is None or sdiff < atm_call_diff:
            atm_call_diff = sdiff
            atm_call = (c["strike"], ticker["mark_iv"], delta)

    best_put, best_put_diff = None, None
    atm_put, atm_put_diff = None, None  # [СБОР VRP]
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
            best_put = (p["instrument_name"], ticker["mark_iv"], delta, p["strike"])
        # [СБОР VRP] ATM-пут
        sdiff = abs(p["strike"] - underlying_price)
        if atm_put_diff is None or sdiff < atm_put_diff:
            atm_put_diff = sdiff
            atm_put = (p["strike"], ticker["mark_iv"], delta)

    if not best_call or not best_put:
        return None, "could not find ~25-delta call/put (greeks unavailable)"

    call_name, call_iv, call_delta, call_strike = best_call
    put_name, put_iv, put_delta, put_strike = best_put
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
        # [СБОР VRP] страйки 25d — нужны для восстановления позиции в симуляции
        "call_strike": call_strike,
        "put_strike": put_strike,
        # [СБОР VRP] ATM — УРОВЕНЬ волатильности, а не разность
        "atm_call_strike": atm_call[0] if atm_call else None,
        "atm_call_iv": atm_call[1] if atm_call else None,
        "atm_put_strike": atm_put[0] if atm_put else None,
        "atm_put_iv": atm_put[1] if atm_put else None,
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


# ---------------------------------------------------------------------------
# [СБОР VRP, 12.09.2026] vol_history.json — УРОВНИ IV, НЕ РАЗНОСТЬ
# ---------------------------------------------------------------------------
# Потребителей нет. Только накопление. Подробное "зачем" — в докстринге модуля.
# Конвенция last-write-wins на дату, как у skew_history.json: аудит разброса
# времени съёма уже обеспечен skew_verdict_history.json, дублировать незачем.

def load_vol_history():
    if not os.path.exists(VOL_HISTORY_FILE):
        return []
    try:
        with open(VOL_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_vol_history(records):
    with open(VOL_HISTORY_FILE, "w") as f:
        json.dump(records[-VOL_HISTORY_MAX_DAYS:], f, indent=2, ensure_ascii=False)


def append_vol_snapshot(records, *, timestamp_iso, meta, dvol, skew_value):
    """Одна запись в сутки, last-write-wins.

    dte считается от момента съёма до экспирации — ВАЖНО для симуляции:
    тенор плавает (берётся ближайшая к 30 дням экспирация, точного
    30-дневного контракта на рынке обычно нет), и без записанного dte
    восстановить позицию задним числом невозможно.
    """
    today = timestamp_iso[:10]
    records = [r for r in records if r.get("date") != today]

    expiry_ts = meta.get("expiry_ts")
    dte = None
    if expiry_ts:
        dte = round((expiry_ts / 1000 - time.time()) / 86400.0, 3)

    records.append({
        "date": today,
        "snapshot_utc": timestamp_iso,
        "underlying": meta.get("underlying_price"),
        "expiry_ts": expiry_ts,
        "dte": dte,
        # уровни ATM — то, чего не хватило для симуляции VRP 08.09.2026
        "atm_call_strike": meta.get("atm_call_strike"),
        "atm_call_iv": meta.get("atm_call_iv"),
        "atm_put_strike": meta.get("atm_put_strike"),
        "atm_put_iv": meta.get("atm_put_iv"),
        # уровни 25-дельта (в skew_history.json хранится только их РАЗНОСТЬ)
        "c25_strike": meta.get("call_strike"),
        "c25_iv": meta.get("call_iv"),
        "c25_delta": meta.get("call_delta"),
        "p25_strike": meta.get("put_strike"),
        "p25_iv": meta.get("put_iv"),
        "p25_delta": meta.get("put_delta"),
        # дублирует разность из skew_history.json НАМЕРЕННО — файл должен
        # быть самодостаточен при анализе, без склейки с другими файлами
        "skew_25d": skew_value,
        "dvol": dvol,
        "collector_version": "vrp-collect-2026-09-12",
    })
    return records


# ---------------------------------------------------------------------------
# ВЕРДИКТ-ЛОГ [РЕШЕНИЕ VIKTOR 29.08.2026] — append-only аудит classification_combined
# ---------------------------------------------------------------------------

def load_verdict_history():
    if not os.path.exists(VERDICT_HISTORY_FILE):
        return []
    try:
        with open(VERDICT_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_verdict_history(records):
    # append-only, без дедупликации по дате — несколько прогонов в сутки
    # намеренно сохраняются как отдельные записи (см. докстринг модуля).
    # Ограничиваем только по КОЛИЧЕСТВУ записей, не по дате.
    with open(VERDICT_HISTORY_FILE, "w") as f:
        json.dump(records[-VERDICT_HISTORY_MAX_RECORDS:], f, indent=2, ensure_ascii=False)


def append_verdict(records, *, timestamp_iso, skew_pct, z_detrended, z_classical, combined):
    # [ПАТЧ ЭТАП 1] Обе ноги пишутся В ПОЛНОЙ ТОЧНОСТИ. Было: детренд полный,
    # классика round(...,2) — асимметрия в аудит-логе. formula_version
    # обязателен: после патча лог содержит смесь v1 и v2, без тега история
    # непригодна для анализа и Этап 2 сравнивать не с чем.
    records.append({
        "date": timestamp_iso[:10],
        "snapshot_utc": timestamp_iso,
        "skew_pct": skew_pct,
        "z_detrended": z_detrended,
        "z_classical": z_classical,
        "verdict": combined.get("verdict"),
        "agreement": combined.get("agreement"),
        "escalate": combined.get("escalate"),
        "note": combined.get("note"),
        "rule": "R3prime-2026-08-28",
        "formula_version": "v2-corrected-2026-09-12",
        "thresholds": list(band_thresholds(BASELINE_WINDOW)),
    })
    return records


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

    # [ПАТЧ ЭТАП 1] A: делитель n-2, оценены ДВА параметра (наклон, сдвиг).
    # pstdev делил на n -> систематическое занижение sigma -> завышение |z|.
    ss_res = sum(r * r for r in residuals)
    residual_std = (ss_res / (lookback_days - 2)) ** 0.5

    predicted_current = slope * lookback_days + intercept  # экстраполяция на индекс 14
    residual_current = current_skew - predicted_current

    # [ПАТЧ ЭТАП 1] B: остаток берётся против ЭКСТРАПОЛЯЦИИ. У прогноза своя
    # погрешность: разброс (факт - прогноз) шире sigma в sqrt(1+leverage) раз.
    # При W=14 фактор = 1.1483. Нормировка шла на голую sigma.
    mean_x = (lookback_days - 1) / 2.0
    sxx = sum((i - mean_x) ** 2 for i in xs)
    leverage = 1.0 / lookback_days + (lookback_days - mean_x) ** 2 / sxx
    pred_factor = (1.0 + leverage) ** 0.5

    denom = residual_std * pred_factor
    z = residual_current / denom if denom != 0 else None

    return {
        # [ПАТЧ ЭТАП 1] D: округление УБРАНО из управляющего значения.
        # round(z,2) шло в combined_classification, а классическая нога —
        # нет: асимметрия. z=1.9951 округлялся до 2.0 и попадал в полосу C.
        "zscore": z,
        "zscore_rounded": round(z, 2) if z is not None else None,
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
        "residual_std_divisor": "n-2",
        "pred_factor": round(pred_factor, 4),
        "formula_version": "v2-corrected-2026-09-12",
    }


def rolling_zscore_legacy(history_values, current):
    """[ПРЕДЫДУЩЕЕ, 16.08.2026] Z-score по ВСЕЙ истории от статичного
    среднего. Заменён детрендингом (compute_detrended_zscore) как основной
    критерий классификации — монотонный дрейф накапливал z сам на себя.
    Оставлен для сравнения/истории решения, не управляет classification.

    [ПАТЧ ЭТАП 1] НЕ ТРОГАЛСЯ: pstdev по всей истории сохранён как есть.
    Ветка reference-only, не для алертинга; её df отличается от W-2,
    поэтому и пороги к ней применяются старые фиксированные 2.0/1.5."""
    if len(history_values) < 5:
        return None
    mu = mean(history_values)
    sigma = pstdev(history_values)
    if sigma == 0:
        return None
    return (current - mu) / sigma


def compute_classical_zscore(prior_history, current_skew, lookback_days=BASELINE_WINDOW):
    """Классический z: (текущее - mean(baseline)) / ВЫБОРОЧНЫЙ std(baseline)
    по тем же 14 точкам, что и детренд (exclude current). Канон §10.3,
    вторая нога двойного чтения [РЕШЕНИЕ VIKTOR 23.08.2026].

    [ПАТЧ ЭТАП 1] A: делитель n-1 (stdev), было n (pstdev).

    🔴 РАСХОЖДЕНИЕ, ОТКРЫТО НА 12.09.2026: skew_crosscheck.compute_skew_zscore
    по-прежнему использует pstdev (делитель n). До приведения его в
    соответствие две ноги одной методологии считают РАЗНЫМИ формулами,
    отношение sqrt(14/13) = 1.0377. Прежний докстринг утверждал
    тождественность — с момента этого патча и до правки crosscheck
    утверждение НЕВЕРНО.

    Слепая зона классического: медленный монотонный дрейф (уровень уходит
    маленькими шагами, каждый \"нормальный\") — его ловит детренд. Слепая зона
    детренда: скачок уровня + залипание, где детренд инвертирует знак — его
    страхует классический. Методы взаимодополняющие, поэтому читаются оба.
    """
    if len(prior_history) < lookback_days:
        return None
    baseline = [pt["skew"] for pt in prior_history[-lookback_days:]]
    mu = mean(baseline)
    # [ПАТЧ ЭТАП 1] A: выборочный std требует делителя n-1, было n.
    sigma = stdev(baseline)
    if sigma == 0:
        return None
    return (current_skew - mu) / sigma


def _band(magnitude):
    """Полоса ноги по |z| для R3'.
    [ПАТЧ ЭТАП 1] C: пороги — t-квантили для хвостов 4.55%/13.36%,
    не фиксированные 2.0/1.5. При W=14: C=2.2314, S=1.6090.
    Логика R3' НЕ МЕНЯЕТСЯ — меняются только границы полос."""
    thr_c, thr_s = band_thresholds(BASELINE_WINDOW)
    if magnitude >= thr_c:
        return "C"
    if magnitude >= thr_s:
        return "S"
    return "N"


def combined_classification(z_detr, z_class):
    """Двойное чтение по правилу R3′ [РЕШЕНИЕ VIKTOR 28.08.2026, в код 01.09.2026].
    НЕ усредняет — маркирует. ВРЕМЕННОЕ правило, точка пересмотра: 3-е
    стресс-событие ИЛИ 30.09.2026, что позже.

    Полосы на ногу: N |z|<thr_S · S thr_S≤|z|<thr_C · C |z|≥thr_C.
    [ПАТЧ ЭТАП 1] Пороги берутся из band_thresholds() (t-квантили),
    прежние фиксированные 1.5/2.0 помечены [ПРЕДЫДУЩЕЕ]. Логика ниже
    НЕ МЕНЯЛАСЬ — изменились только границы полос.

    1) Обе ноги вне N И знаки различаются → DIVERGENT, escalate.
       [документированная инверсия] — соответствует правилу Judge при
       directional conflict: не сглаживать, требовать ручного решения Viktor.
    2) Пара полос (N, C) в любом порядке → DIVERGENT, escalate.
       [разрыв в две полосы] — одна нога критична, вторая в норме.
    3) Иначе → вердикт по ноге с бо́льшим |z|, её знак, escalate=False.
       Исключение: CRITICAL требует ОБЕ ноги в полосе C (сохранение акта
       23.08). Пара (C, S) → SIGNAL по знаку старшей ноги.

    [ДЕФЕКТ, ЗАРЕГИСТРИРОВАН 29.08.2026, НЕ ИСПРАВЛЕН — см. §12 реестр]:
    при z_detr is None (< BASELINE_WINDOW точек истории) da подставляется
    как 0.0 (полоса N), а не как "нет данных". Если при этом вторая нога
    тоже в N, функция вернёт NORMAL, хотя корректный вердикт —
    INSUFFICIENT_HISTORY. Сейчас неактуально (истории > 14 точек), но
    станет риском при сбросе контейнера или добавлении нового инструмента
    (напр. ETH skew) без накопленной истории. Не исправлено в этой правке —
    исправление не запрошено, только сохранена регистрация дефекта.
    """
    da = abs(z_detr) if z_detr is not None else 0.0
    ca = abs(z_class) if z_class is not None else 0.0
    band_d = _band(da)
    band_c = _band(ca)
    bands = {band_d, band_c}

    both_present = z_detr is not None and z_class is not None
    signs_differ = both_present and (z_detr > 0) != (z_class > 0)

    # R3′ п.1 — обе ноги вне N и знаки противоположны: документированная инверсия
    if band_d != "N" and band_c != "N" and signs_differ:
        return {
            "verdict": "DIVERGENT",
            "agreement": "DISAGREE",
            "escalate": True,
            "note": ("R3′ п.1: обе ноги вне полосы N, знаки противоположны — "
                     "документированная инверсия, авто-вердикта нет, "
                     "ручное решение Viktor (§10.3 двойное чтение)."),
        }

    # R3′ п.2 — разрыв в две полосы (N, C): одна нога критична, вторая в норме
    if bands == {"N", "C"}:
        return {
            "verdict": "DIVERGENT",
            "agreement": "DISAGREE",
            "escalate": True,
            "note": ("R3′ п.2: разрыв в две полосы (N,C) — одна нога в полосе C, "
                     "другая в N; авто-вердикта нет, ручное решение "
                     "Viktor (§10.3 двойное чтение)."),
        }

    # R3′ п.3 — вердикт по старшей ноге (бо́льший |z|), её знак, без эскалации.
    if da >= ca:
        senior_z, senior_mag, senior_leg = z_detr, da, "detrend"
    else:
        senior_z, senior_mag, senior_leg = z_class, ca, "classical"
    senior_band = _band(senior_mag)

    if senior_band == "N":
        return {
            "verdict": "NORMAL",
            "agreement": "AGREE",
            "escalate": False,
            "note": "R3′ п.3: обе ноги в полосе N.",
        }

    side = "PUT_PREMIUM" if (senior_z is not None and senior_z > 0) else "CALL_PREMIUM"

    # CRITICAL требует ОБЕ ноги в полосе C (сохранение акта 23.08). К этому
    # месту обе ноги в C означает согласие знаков — противоположные знаки при
    # обеих вне N уже отсеяны п.1.
    if band_d == "C" and band_c == "C":
        return {
            "verdict": f"CRITICAL_{side}",
            "agreement": "AGREE",
            "escalate": False,
            "note": (f"R3′ п.3: обе ноги в полосе C, знак согласован — "
                     f"CRITICAL по старшей ноге ({senior_leg})."),
        }

    # Пара (C,S) либо (S,S)/(N,S): старшая нога в S или C, но не обе C → SIGNAL
    return {
        "verdict": f"SIGNAL_{side}",
        "agreement": "AGREE",
        "escalate": False,
        "note": (f"R3′ п.3: вердикт по старшей ноге ({senior_leg}, "
                 f"|z|={round(senior_mag, 2)}); CRITICAL не выдан — "
                 f"вторая нога не в полосе C."),
    }


def classify_skew(z, thresholds=None):
    """[ПАТЧ ЭТАП 1] C: пороги параметром.
    thresholds=None -> t-квантили текущего окна (управляющие поля).
    thresholds=(2.0, 1.5) -> legacy-ветка: full-history z имеет ДРУГОЙ df,
    t-квантиль окна к нему неприменим. Legacy помечен reference-only."""
    if z is None:
        return "INSUFFICIENT_HISTORY"
    thr_c, thr_s = thresholds if thresholds else band_thresholds(BASELINE_WINDOW)
    if z >= thr_c:
        return "CRITICAL_PUT_PREMIUM"  # институциональный tail-hedge, рынок закладывает падение
    if z >= thr_s:
        return "SIGNAL_PUT_PREMIUM"
    if z <= -thr_c:
        return "CRITICAL_CALL_PREMIUM"
    if z <= -thr_s:
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

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now_iso[:10]

    history = load_history()
    # [ПАТЧ ЭТАП 1] E: при ПОВТОРНОМ прогоне в сутки файл уже содержит
    # сегодняшнюю точку от раннего прогона. append_today() чистит дубль
    # ПОСЛЕ расчёта, поэтому baseline включал бы собственную утреннюю
    # запись, а includes_current:False лгало бы. Чистим ДО расчёта.
    prior_history = [h for h in history if h.get("date") != today]
    rerun_today = len(prior_history) != len(history)

    prior_values = [h["skew"] for h in prior_history]

    detrended = compute_detrended_zscore(prior_history, skew)
    z = detrended["zscore"] if detrended else None

    z_legacy = rolling_zscore_legacy(prior_values, skew)
    z_classical = compute_classical_zscore(prior_history, skew)

    history = append_today(history, skew, now_iso)
    save_history(history)

    intraday = load_intraday()
    intraday = append_intraday_snapshot(intraday, skew, now_iso)
    save_intraday(intraday)

    # [СБОР VRP, 12.09.2026] Накопление уровней IV. НЕ участвует ни в одном
    # расчёте и ни в одном вердикте — только пишется на диск.
    # try обязателен: сбой накопительного блока НЕ ДОЛЖЕН ронять боевой прогон.
    vol_collect_error = None
    try:
        dvol = get_dvol("BTC")
        vol_history = load_vol_history()
        vol_history = append_vol_snapshot(
            vol_history,
            timestamp_iso=now_iso,
            meta=meta,
            dvol=dvol,
            skew_value=skew,
        )
        save_vol_history(vol_history)
    except Exception as e:
        vol_collect_error = str(e)

    combined = combined_classification(z, z_classical)

    # [РЕШЕНИЕ VIKTOR 29.08.2026]: append-лог вердикта — ДО перезаписи
    # BLOCK_OUTPUT_FILE, чтобы classification_combined не терялся между
    # прогонами (см. докстринг модуля).
    verdict_history = load_verdict_history()
    verdict_history = append_verdict(
        verdict_history,
        timestamp_iso=now_iso,
        skew_pct=round(skew, 3),
        z_detrended=z,
        z_classical=z_classical,
        combined=combined,
    )
    save_verdict_history(verdict_history)

    result.update({
        "skew_pct": round(skew, 3),
        "zscore": z,
        "classification": classify_skew(z),
        "trend": detrended if detrended else "INSUFFICIENT_HISTORY",
        "zscore_legacy_full_history": round(z_legacy, 2) if z_legacy is not None else None,
        # legacy: full-history z, df != W-2 -> t-квантиль окна неприменим.
        # Оставлен на 2.0/1.5. Reference only, не для алертинга.
        "classification_legacy": classify_skew(z_legacy, thresholds=(2.0, 1.5)),
        "zscore_classical": round(z_classical, 2) if z_classical is not None else None,
        "classification_classical": classify_skew(z_classical),
        "classification_combined": combined,
        "history_points": len(prior_values),
        "meta": meta,
        "rerun_same_day": rerun_today,
        "formula_version": "v2-corrected-2026-09-12",
        # [СБОР VRP] диагностика накопительного блока. None = сбор прошёл.
        "vol_collect_error": vol_collect_error,
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
