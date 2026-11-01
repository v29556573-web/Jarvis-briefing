"""Одноразовая диагностика (01.11.2026, Mark50-Judge): какие источники L/S и
фандинга отвечают с раннеров GitHub. Ничего не пишет. Не норма, не источник §2.8."""
import time, requests
now = int(time.time())
T = [
 ("Binance L/S (global acc)", "https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol":"BTCUSDT","period":"5m","limit":5}),
 ("BingX premiumIndex", "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex", {"symbol":"BTC-USDT"}),
 ("BingX fundingRate hist", "https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate", {"symbol":"BTC-USDT","limit":3}),
 ("BingX L/S (guess 1)", "https://open-api.bingx.com/openApi/swap/v2/quote/longShortRatio", {"symbol":"BTC-USDT"}),
 ("BingX L/S (guess 2)", "https://open-api.bingx.com/openApi/swap/v1/market/longShortRatio", {"symbol":"BTC-USDT"}),
 ("Kraken Fut tickers", "https://futures.kraken.com/derivatives/api/v3/tickers/PF_XBTUSD", {}),
 ("Kraken Fut L/S ratio", "https://futures.kraken.com/api/charts/v1/analytics/PF_XBTUSD/long-short-ratio", {"since":now-3600,"interval":300}),
 ("Kraken Fut L/S info", "https://futures.kraken.com/api/charts/v1/analytics/PF_XBTUSD/long-short-info", {"since":now-3600,"interval":300}),
 ("MEXC funding history", "https://contract.mexc.com/api/v1/contract/funding_rate/history", {"symbol":"BTC_USDT","page_num":1,"page_size":3}),
 ("MEXC funding_rate", "https://contract.mexc.com/api/v1/contract/funding_rate/BTC_USDT", {}),
]
for name,url,p in T:
    try:
        r = requests.get(url, params=p, timeout=15, headers={"User-Agent":"jarvis-probe/1.0"})
        print(f"[PROBE] {r.status_code} | {name} | {r.text[:260]!r}")
    except Exception as e:
        print(f"[PROBE] ERR | {name} | {e}")
