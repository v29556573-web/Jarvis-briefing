#!/usr/bin/env python3
"""
JARVIS · расчётный DXY по курсам ЕЦБ · §7.5 вторичный триггер (Δ5 > +1.5%) · v1.1 · Mark50-Judge 18.10.2026

Формула ICE US Dollar Index (веса фиксированы с 1999):
  DXY = 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036
Курсы: ЕЦБ, референсные курсы EUR к валюте (EXR, D.*.EUR.SP00.A), фиксинг ~14:15 CET, публикация ~16:00 CET.
  EURUSD = USD/EUR;  USDxxx = (xxx/EUR) / (USD/EUR);  GBPUSD = (USD/EUR) / (GBP/EUR)
Δ5 = DXY_t / DXY_{t-5 фиксингов ЕЦБ} − 1.

Статус: ЭТАЛОН DXY для §7.5 [RATIFIED Viktor 18.10.2026] — заменяет ICEUS:DXY (TradingView — справочно).
⚠ Расхождения с ICEUS:DXY (TradingView): время (14:15 CET против закрытия ICE 17:00 ET), календарь
  (нет фиксинга в праздники TARGET; есть в праздники США). Скрипт печатает сверку с ledger Viktor.

Запуск (Colab / ПК / GitHub Actions):  python jarvis_dxy_ecb.py  [--start 2026-01-01]
Выход: dxy_ecb.csv (ряд), dxy_ecb_output.json (последняя точка, Δ5, триггер).
"""
import argparse, csv, io, json, math, sys, urllib.request
from datetime import datetime, timezone

K = 50.14348112
W = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091, "USDSEK": 0.042, "USDCHF": 0.036}
CUR = ("USD", "JPY", "GBP", "CAD", "SEK", "CHF")
URL = ("https://data-api.ecb.europa.eu/service/data/EXR/D." + "+".join(CUR) +
       ".EUR.SP00.A?startPeriod={start}&format=csvdata")
THR = 1.5
# Сверка: закрытия ICEUS:DXY из кадров Viktor (claude/JARVIS_DXY_ledger.md), MEASURED
LEDGER = {"2026-10-02": 101.924, "2026-10-07": 102.250, "2026-10-09": 102.231, "2026-10-12": 102.572,
          "2026-10-14": 102.528, "2026-10-15": 102.004, "2026-10-16": 101.779}


def fetch(start, path=None):
    if path:
        raw = open(path, encoding="utf-8").read(); src = path
    else:
        req = urllib.request.Request(URL.format(start=start), headers={"User-Agent": "jarvis-dxy", "Accept": "text/csv"})
        raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8"); src = "ECB data-api"
    rates = {}
    for row in csv.DictReader(io.StringIO(raw)):
        v = row.get("OBS_VALUE", "").strip()
        if not v or v == "NaN":
            continue
        rates.setdefault(row["TIME_PERIOD"], {})[row["CURRENCY"]] = float(v)
    full = {d: r for d, r in rates.items() if all(c in r for c in CUR)}
    skipped = sorted(set(rates) - set(full))
    return dict(sorted(full.items())), skipped, src


def dxy(r):
    u = r["USD"]
    px = {"EURUSD": u, "USDJPY": r["JPY"] / u, "GBPUSD": u / r["GBP"],
          "USDCAD": r["CAD"] / u, "USDSEK": r["SEK"] / u, "USDCHF": r["CHF"] / u}
    return K * math.prod(px[k] ** w for k, w in W.items()), px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--file", help="CSV ЕЦБ, сохранённый вручную (если API недоступен)")
    a, _ = ap.parse_known_args()   # Colab-ячейка передаёт свой -f … — игнорируем
    data, skipped, src = fetch(a.start, a.file)
    if len(data) < 6:
        sys.exit("Мало фиксингов ЕЦБ для Δ5")
    dates = list(data)
    rows = []
    for i, d in enumerate(dates):
        v, px = dxy(data[d])
        d5 = (v / rows[i - 5]["dxy"] - 1) * 100 if i >= 5 else None
        rows.append({"date": d, "dxy": v, "d5_pct": d5, **{k: px[k] for k in W}})
    print(f"JARVIS DXY·ЕЦБ v1.1 · {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · источник {src}")
    print(f"Фиксингов: {len(rows)} ({dates[0]} … {dates[-1]}); неполных дней пропущено: {skipped[:5] or 'нет'}")

    print("\n=== Сверка с ledger ICEUS:DXY (кадры Viktor) ===")
    print("дата        ICE (TV)   ЕЦБ-расчёт   разница")
    by = {r["date"]: r for r in rows}
    diffs = []
    for d, ice in LEDGER.items():
        if d in by:
            df = by[d]["dxy"] - ice; diffs.append(df)
            print(f"{d}  {ice:9.3f}  {by[d]['dxy']:10.3f}   {df:+.3f} ({df / ice * 100:+.2f}%)")
        else:
            print(f"{d}  {ice:9.3f}        —      нет фиксинга ЕЦБ")
    if diffs:
        m = sum(diffs) / len(diffs); mx = max(abs(x) for x in diffs)
        print(f"Средняя разница {m:+.3f}, макс |разница| {mx:.3f} пункта")

    print("\n=== Последние 12 фиксингов ===")
    print("дата         DXY·ЕЦБ    Δ5 %   триггер")
    for r in rows[-12:]:
        d5 = r["d5_pct"]
        flag = "—" if d5 is None else ("СРАБОТАЛ" if d5 > THR else "нет")
        print(f"{r['date']}  {r['dxy']:9.3f}  {'' if d5 is None else f'{d5:+6.2f}'}   {flag}")

    with open("dxy_ecb.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "dxy", "d5_pct", *W])
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
    last = rows[-1]
    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "ECB EXR reference rates",
           "status": "RATIFIED 18.10.2026 — DXY source for §7.5", "date": last["date"], "dxy_ecb": round(last["dxy"], 3),
           "d5_pct": None if last["d5_pct"] is None else round(last["d5_pct"], 3), "threshold_pct": THR,
           "trigger": bool(last["d5_pct"] is not None and last["d5_pct"] > THR)}
    json.dump(out, open("dxy_ecb_output.json", "w"), ensure_ascii=False, indent=2)
    print(f"\nПоследняя точка {last['date']}: DXY·ЕЦБ {out['dxy_ecb']}, Δ5 {out['d5_pct']}% → триггер {'ДА' if out['trigger'] else 'нет'}")
    print("Файлы: dxy_ecb.csv, dxy_ecb_output.json. Прислать весь вывод Judge.")


if __name__ == "__main__":
    main()
