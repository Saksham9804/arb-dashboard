"""
Crypto Arbitrage Bot — KuCoin vs WazirX
Posts trade and price data to the FastAPI server for the dashboard.
"""

import sys, os, ssl, certifi, asyncio, logging, time, httpx
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from typing import Optional

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

_CA = certifi.where()
os.environ["SSL_CERT_FILE"]      = _CA
os.environ["REQUESTS_CA_BUNDLE"] = _CA

import aiohttp
import ccxt.async_support as ccxt
from ccxt.async_support import kucoin  as _kucoin_cls
from ccxt.async_support import wazirx  as _wazirx_cls
from dotenv import load_dotenv
load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
EXCHANGE_A       = os.getenv("EXCHANGE_A", "kucoin")
EXCHANGE_B       = os.getenv("EXCHANGE_B", "wazirx")
SYMBOLS          = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "SOL/USDT", "BNB/USDT"]
MIN_SPREAD_PCT   = float(os.getenv("MIN_SPREAD_PCT", "0.3"))
FEE_A            = float(os.getenv("FEE_A", "0.1"))
FEE_B            = float(os.getenv("FEE_B", "0.0"))
MAX_TRADE_USDT   = float(os.getenv("MAX_TRADE_USDT", "100"))
MIN_TRADE_USDT   = float(os.getenv("MIN_TRADE_USDT", "10"))
MAX_TRADE_FRAC   = float(os.getenv("MAX_TRADE_FRACTION", "0.95"))
POLL_INTERVAL    = float(os.getenv("POLL_INTERVAL_SEC", "1.0"))
DRY_RUN          = os.getenv("DRY_RUN", "true").lower() == "true"
API_URL          = os.getenv("API_URL", "http://localhost:8000")

KUCOIN_API_KEY    = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_SECRET     = os.getenv("KUCOIN_SECRET", "")
KUCOIN_PASSPHRASE = os.getenv("KUCOIN_PASSPHRASE", "")
WAZIRX_API_KEY    = os.getenv("WAZIRX_API_KEY", "")
WAZIRX_SECRET     = os.getenv("WAZIRX_SECRET", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("arb_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("arb_bot")

# ── Patch ccxt class-level to skip extra API calls ────────────────────────────
async def _noop_dict(*a, **kw): return {}
async def _noop_list(*a, **kw): return []
_kucoin_cls.fetch_currencies = _noop_dict
_wazirx_cls.fetch_currencies = _noop_dict

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session():
    ssl_ctx   = ssl.create_default_context(cafile=_CA)
    resolver  = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, resolver=resolver)
    return aiohttp.ClientSession(connector=connector)


async def _load_kucoin_spot_only(ex):
    response   = await ex.publicGetSymbols()
    raw        = response.get("data", [])
    markets    = {}
    for m in raw:
        if not m.get("enableTrading", False): continue
        base, quote = m.get("baseCurrency",""), m.get("quoteCurrency","")
        sym = f"{base}/{quote}"
        markets[sym] = {
            "id": m.get("symbol"), "symbol": sym, "base": base, "quote": quote,
            "active": True, "type": "spot",
            "precision": {"amount": m.get("baseIncrement"), "price": m.get("priceIncrement")},
            "limits": {
                "amount": {"min": float(m.get("baseMinSize",0)), "max": float(m.get("baseMaxSize",0)) or None},
                "cost":   {"min": float(m.get("quoteMinSize",0))},
            },
            "info": m,
        }
    ex.markets = markets
    ex.markets_by_id = {v["id"]: v for v in markets.values()}
    ex.symbols = sorted(markets.keys())
    log.info(f"  kucoin: {len(markets)} spot markets loaded")


def _make_exchange(name):
    common = {"enableRateLimit": True, "session": _make_session(),
              "options": {"fetchCurrencies": False}}
    name = name.lower()
    if name == "kucoin":
        ex = ccxt.kucoin({**common, "apiKey": KUCOIN_API_KEY,
                          "secret": KUCOIN_SECRET, "password": KUCOIN_PASSPHRASE})
    elif name == "wazirx":
        ex = ccxt.wazirx({**common, "apiKey": WAZIRX_API_KEY, "secret": WAZIRX_SECRET})
    else:
        raise ValueError(name)
    ex.fetch_currencies = _noop_dict
    return ex


@dataclass
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_pct: float


class ArbBot:
    def __init__(self):
        self.ex_a = self.ex_b = None
        self.name_a, self.name_b = EXCHANGE_A, EXCHANGE_B
        self.running = False
        self.trade_count = 0
        self.total_profit = 0.0
        self._fails: dict = {}
        self._syms: list = []
        self._http = httpx.AsyncClient(timeout=5)

    async def _post(self, path, data):
        try:
            await self._http.post(f"{API_URL}{path}", json=data)
        except Exception:
            pass   # never let API posting crash the bot

    async def _close(self):
        for ex in [self.ex_a, self.ex_b]:
            if ex:
                try: await ex.close()
                except Exception: pass
        await self._http.aclose()

    async def _load_markets(self):
        log.info(f"Loading {self.name_a}...")
        self.ex_a = _make_exchange(self.name_a)
        if self.name_a == "kucoin":
            await _load_kucoin_spot_only(self.ex_a)
        else:
            await self.ex_a.load_markets()

        log.info(f"Loading {self.name_b}...")
        self.ex_b = _make_exchange(self.name_b)
        if self.name_b == "kucoin":
            await _load_kucoin_spot_only(self.ex_b)
        else:
            await self.ex_b.load_markets()

        sa, sb = set(self.ex_a.markets), set(self.ex_b.markets)
        self._syms = [s for s in SYMBOLS if s in sa and s in sb]
        log.info(f"Common pairs: {self._syms}")

    async def fetch_prices(self, symbol):
        try:
            ob_a, ob_b = await asyncio.gather(
                self.ex_a.fetch_order_book(symbol, limit=20),
                self.ex_b.fetch_order_book(symbol, limit=20),
            )
            self._fails[symbol] = 0
            return {
                self.name_a: {"bid": ob_a["bids"][0][0] if ob_a["bids"] else None,
                               "ask": ob_a["asks"][0][0] if ob_a["asks"] else None},
                self.name_b: {"bid": ob_b["bids"][0][0] if ob_b["bids"] else None,
                               "ask": ob_b["asks"][0][0] if ob_b["asks"] else None},
            }
        except Exception as e:
            self._fails[symbol] = self._fails.get(symbol, 0) + 1
            backoff = min(2 ** self._fails[symbol], 60)
            log.error(f"Price fetch {symbol}: {e} (backoff {backoff}s)")
            await asyncio.sleep(backoff)
            return {}

    def find_opportunity(self, symbol, prices):
        if not prices: return None
        a, b = prices[self.name_a], prices[self.name_b]
        if None in (a["bid"], a["ask"], b["bid"], b["ask"]): return None
        net = FEE_A + FEE_B
        s1 = (b["bid"] - a["ask"]) / a["ask"] * 100
        s2 = (a["bid"] - b["ask"]) / b["ask"] * 100
        if s1 - net > MIN_SPREAD_PCT:
            return Opportunity(symbol, self.name_a, self.name_b, a["ask"], b["bid"], s1)
        if s2 - net > MIN_SPREAD_PCT:
            return Opportunity(symbol, self.name_b, self.name_a, b["ask"], a["bid"], s2)
        return None

    async def execute_trade(self, opp):
        trade_usdt = MAX_TRADE_USDT if DRY_RUN else min(
            (await self._balance(opp.buy_exchange)) * MAX_TRADE_FRAC, MAX_TRADE_USDT)
        if not DRY_RUN and trade_usdt < MIN_TRADE_USDT:
            log.warning(f"Low balance: {trade_usdt:.2f} USDT")
            return
        qty = Decimal(str(trade_usdt / opp.buy_price)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        profit = float(qty) * (opp.sell_price - opp.buy_price)
        self.trade_count += 1
        self.total_profit += profit
        mode = "[DRY RUN]" if DRY_RUN else "[LIVE]"
        log.info(f"{mode} {opp.symbol} | Buy {opp.buy_exchange} @ {opp.buy_price:.4f} "
                 f"| Sell {opp.sell_exchange} @ {opp.sell_price:.4f} "
                 f"| Spread {opp.spread_pct:.3f}% | Profit {profit:.4f} USDT")

        if not DRY_RUN:
            bex = self.ex_a if opp.buy_exchange == self.name_a else self.ex_b
            sex = self.ex_a if opp.sell_exchange == self.name_a else self.ex_b
            try:
                await asyncio.gather(
                    bex.create_market_buy_order(opp.symbol, float(qty)),
                    sex.create_market_sell_order(opp.symbol, float(qty)),
                )
            except Exception as e:
                log.error(f"Trade failed: {e}")
                return

        # Post to dashboard API
        await self._post("/api/trade", {
            "timestamp": time.time(), "symbol": opp.symbol,
            "buy_exchange": opp.buy_exchange, "sell_exchange": opp.sell_exchange,
            "buy_price": opp.buy_price, "sell_price": opp.sell_price,
            "spread_pct": opp.spread_pct, "qty": float(qty),
            "profit_usdt": profit, "dry_run": DRY_RUN,
        })

    async def _balance(self, name):
        ex = self.ex_a if name == self.name_a else self.ex_b
        try:
            bal = await ex.fetch_balance()
            return float(bal["USDT"]["free"])
        except Exception:
            return 0.0

    async def run(self):
        log.info(f"Bot starting ({self.name_a} <-> {self.name_b}) | DRY_RUN={DRY_RUN}")
        try:
            await self._load_markets()
        except Exception as e:
            log.error(f"Startup failed: {e}")
            await self._close()
            return

        self.running = True
        tick_counter = 0
        try:
            while self.running:
                for sym in self._syms:
                    prices = await self.fetch_prices(sym)
                    if not prices: continue

                    a, b = prices[self.name_a], prices[self.name_b]
                    if a["ask"] and b["bid"]:
                        spread = (b["bid"] - a["ask"]) / a["ask"] * 100
                        # Post price tick every 5 seconds per symbol
                        if tick_counter % 5 == 0:
                            await self._post("/api/tick", {
                                "timestamp": time.time(), "symbol": sym,
                                "exchange_a_bid": a["bid"], "exchange_a_ask": a["ask"],
                                "exchange_b_bid": b["bid"], "exchange_b_ask": b["ask"],
                                "spread_pct": spread,
                            })

                    opp = self.find_opportunity(sym, prices)
                    if opp:
                        await self.execute_trade(opp)

                tick_counter += 1
                await asyncio.sleep(POLL_INTERVAL)

        except asyncio.CancelledError:
            pass
        finally:
            await self._close()
            log.info(f"Done: {self.trade_count} trades | {self.total_profit:.4f} USDT profit")

    def stop(self): self.running = False


async def main():
    bot = ArbBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
