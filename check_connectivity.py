"""
Run this first to diagnose exactly what your network can/cannot reach.
python check_connectivity.py
"""
import urllib.request
import urllib.error
import socket
import sys

TESTS = [
    # (label, url)
    ("Cloudflare DoH",        "https://1.1.1.1/dns-query?name=api.kucoin.com&type=A"),
    ("Google DoH",            "https://8.8.8.8/dns-query?name=api.kucoin.com&type=A"),
    ("KuCoin API",            "https://api.kucoin.com/api/v1/timestamp"),
    ("Binance API",           "https://api.binance.com/api/v3/ping"),
    ("WazirX API",            "https://api.wazirx.com/sapi/v1/time"),
    ("Bybit API",             "https://api.bybit.com/v5/market/time"),
    ("CoinDCX public",        "https://public.coindcx.com/market_data/orderbook?pair=B-BTC_USDT"),
    ("Google (baseline)",     "https://www.google.com"),
]

print(f"Python {sys.version}\n")
print(f"{'Test':<25} {'Result'}")
print("-" * 60)

for label, url in TESTS:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"{label:<25} OK  (HTTP {r.status})")
    except urllib.error.HTTPError as e:
        print(f"{label:<25} OK  (HTTP {e.code} — still reachable)")
    except urllib.error.URLError as e:
        print(f"{label:<25} FAIL  {e.reason}")
    except Exception as e:
        print(f"{label:<25} FAIL  {e}")