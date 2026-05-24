"""
Run this to see the exact underlying error from ccxt.
python debug_ssl.py
"""
import sys, os, ssl, certifi, asyncio, traceback

_CA = certifi.where()
os.environ["SSL_CERT_FILE"] = _CA
os.environ["REQUESTS_CA_BUNDLE"] = _CA

_real = ssl.create_default_context
def _patched(*a, **kw):
    kw.setdefault("cafile", _CA)
    return _real(*a, **kw)
ssl.create_default_context = _patched
ssl._create_default_https_context = _patched

import ccxt.async_support as ccxt

async def main():
    ex = ccxt.kucoin({"enableRateLimit": True})
    try:
        await ex.load_markets()
        print("SUCCESS — markets loaded:", len(ex.markets))
    except Exception:
        print("FULL ERROR:")
        traceback.print_exc()
    finally:
        await ex.close()

asyncio.run(main())