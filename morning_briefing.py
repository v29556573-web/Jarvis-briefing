"""
Утренний брифинг JARVIS — MEXC public API + Telegram delivery.
Запускается по расписанию через GitHub Actions (см. .github/workflows/morning-briefing.yml).

Требует два секрета в переменных окружения (задаются как GitHub Secrets):
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  TELEGRAM_CHAT_ID   — твой личный chat_id (см. инструкцию в README)

Правь список SYMBOLS и уровни POSITIONS под актуальные открытые позиции.
"""

import os
import sys
import requests

MEXC_BASE = "https://api.mexc.com/api/v3"

# --- Настрой под свои текущие открытые позиции ---
SYMBOLS = ["BTCUSDT", "LTCUSDT", "ETCUSDT"]

POSITIONS = {
    "BTCUSDT": {"side": "long", "entry": 62737, "stop": 61096, "target": 67067},
    # Добавляй/убирай по мере открытия-закрытия позиций
    # "LTCUSDT": {"side": "short", "entry": 44.13, "stop": 47.17},
}
# ---------------------------------------------------


def fetch_ticker_24hr(symbol: str) -> dict:
    resp = requests.get(f"{MEXC_BASE}/ticker/24hr", params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def format_position_line(symbol: str, price: float) -> str:
    pos = POSITIONS.get(symbol)
    if not pos:
        return ""
    entry = pos["entry"]
    stop = pos["stop"]
    side = pos["side"]

    if side == "long":
        pnl_pct = (price - entry) / entry * 100
        to_stop_pct = (price - stop) / price * 100
    else:
        pnl_pct = (entry - price) / entry * 100
        to_stop_pct = (stop - price) / price * 100

    risk = abs(entry - stop)
    r_multiple = (price - entry) / risk if side == "long" else (entry - price) / risk

    return (
        f"  Позиция ({side}): entry {entry} | стоп {stop}\n"
        f"  От entry: {pnl_pct:+.2f}% | До стопа: {to_stop_pct:.2f}% | R: {r_multiple:+.2f}"
    )


def build_message() -> str:
    lines = ["🌅 *Утренний брифинг JARVIS*\n"]
    for symbol in SYMBOLS:
        try:
            data = fetch_ticker_24hr(symbol)
        except Exception as e:
            lines.append(f"*{symbol}*: ошибка получения данных ({e})")
            continue

        price = float(data["lastPrice"])
        change_pct = float(data["priceChangePercent"]) * 100
        high = float(data["highPrice"])
        low = float(data["lowPrice"])

        lines.append(f"*{symbol}*")
        lines.append(f"  Цена: {price:,.2f} | 24ч: {change_pct:+.2f}%")
        lines.append(f"  24ч диапазон: {low:,.2f} — {high:,.2f}")

        pos_line = format_position_line(symbol, price)
        if pos_line:
            lines.append(pos_line)
        lines.append("")

    return "\n".join(lines)


def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
        timeout=10,
    )
    resp.raise_for_status()


def main():
    message = build_message()
    print(message)  # видно в логах GitHub Actions для отладки
    send_telegram(message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
