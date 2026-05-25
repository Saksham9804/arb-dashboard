"""
Crypto Arbitrage Bot — KuCoin vs WazirX
"""

import sys, os, ssl, certifi, asyncio, logging, time
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
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("arb_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("arb_bot")

# ── API endpoint for the dashboard ───────────────────────────────────────────
API_URL = os.environ.get("API_URL", "http://localhost:8000")


def _make_session() -> aiohttp.ClientSession:
    ssl_ctx   = ssl.create_default_context(cafile=_CA)
    resolver  = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, resolver=resolver)
    return aiohttp.ClientSession(connector=connector)


async def _noop_dict(*a, **kw): return {}
async def _noop_list(*a, **kw): return []
async def _noop_none(*a, **kw): return None


async def _load_kucoin_spot_only(ex: ccxt.kucoin):
    response = await ex.publicGetSymbols()
    raw_markets = response.get("data", [])
    markets = {}
    for m in raw_markets:
        if not m.get("enableTrading", False):
            continue
        base  = m.get("baseCurrency", "")
        quote = m.get("quoteCurrency", "")
        symbol = f"{base}/{quote}"
        markets[symbol] = {
            "id":        m.get("symbol"),
            "symbol":    symbol,
            "base":      base,
            "quote":     quote,
            "active":    m.get("enableTrading", False),
            "precision": {
                "amount": m.get("baseIncrement"),
                "price":  m.get("priceIncrement"),
            },
            "limits": {
                "amount": {"min": float(m.get("baseMinSize", 0)),
                           "max": float(m.get("baseMaxSize", 0)) or None},
                "price":  {"min": float(m.get("priceIncrement", 0))},
                "cost":   {"min": float(m.get("quoteMinSize", 0))},
            },
            "info": m,
            "type": "spot",
        }
    ex.markets            = markets
    ex.markets_by_id      = {v["id"]: v for v in markets.values()}
    ex.symbols            = sorted(markets.keys())
    ex.markets_loading    = None
    log.info(f"  kucoin: {len(markets)} spot markets loaded (direct fetch)")


@dataclass
class ArbitrageOpportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_pct: float
    timestamp: float


def _make_exchange(name: str) -> ccxt.Exchange:
    name = name.lower()
    common = {
        "enableRateLimit": True,
        "session": _make_session(),
        "options": {"fetchCurrencies": False},
    }
    if name == "kucoin":
        ex = ccxt.kucoin({**common,
                          "apiKey":   config.KUCOIN_API_KEY,
                          "secret":   config.KUCOIN_SECRET,
                          "password": config.KUCOIN_PASSPHRASE})
    elif name == "binance":
        common["options"]["defaultType"] = "spot"
        ex = ccxt.binance({**common,
                           "apiKey": config.BINANCE_API_KEY,
                           "secret": config.BINANCE_SECRET})
    elif name == "wazirx":
        ex = ccxt.wazirx({**common,
                          "apiKey": config.WAZIRX_API_KEY,
                          "secret": config.WAZIRX_SECRET})
    elif name == "bybit":
        ex = ccxt.bybit({**common,
                         "apiKey": config.BYBIT_API_KEY,
                         "secret": config.BYBIT_SECRET})
    else:
        raise ValueError(f"Unknown exchange: {name}")

    ex.fetch_currencies = _noop_dict
    return ex


class ArbitrageBot:
    def __init__(self):
        self.exchange_a: Optional[ccxt.Exchange] = None
        self.exchange_b: Optional[ccxt.Exchange] = None
        self.name_a = config.EXCHANGE_A
        self.name_b = config.EXCHANGE_B
        self.running = False
        self.trade_count = 0
        self.total_profit_usdt = 0.0
        self._fail_counts: dict = {}
        self._common_symbols: list = []
        self._api_session: Optional[aiohttp.ClientSession] = None

    async def _get_api_session(self) -> aiohttp.ClientSession:
        if self._api_session is None or self._api_session.closed:
            self._api_session = aiohttp.ClientSession()
        return self._api_session

    async def _post_trade_to_api(self, opp: ArbitrageOpportunity, profit: float, dry_run: bool):
        """Post trade data to the dashboard API. Fails silently so bot keeps running."""
        try:
            session = await self._get_api_session()
            payload = {
                "symbol":       opp.symbol,
                "buy_exchange":  opp.buy_exchange,
                "sell_exchange": opp.sell_exchange,
                "buy_price":    opp.buy_price,
                "sell_price":   opp.sell_price,
                "spread":       opp.spread_pct,
                "profit":       profit,
                "dry_run":      dry_run,
                "time":         time.time(),
            }
            async with session.post(
                f"{API_URL}/api/post-trade",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status != 200:
                    log.warning(f"API post returned {resp.status}")
        except Exception as e:
            log.debug(f"API post failed (non-critical): {e}")

    async def _close_all(self):
        for ex in [self.exchange_a, self.exchange_b]:
            if ex:
                try:
                    if hasattr(ex, "session") and ex.session and not ex.session.closed:
                        await ex.session.close()
                    await ex.close()
                except Exception:
                    pass
        if self._api_session and not self._api_session.closed:
            await self._api_session.close()
        await asyncio.sleep(0.25)

    async def _load_markets(self):
        log.info(f"Loading {self.name_a} markets...")
        self.exchange_a = _make_exchange(self.name_a)
        if self.name_a == "kucoin":
            await _load_kucoin_spot_only(self.exchange_a)
        else:
            await self.exchange_a.load_markets()
            log.info(f"  {self.name_a}: {len(self.exchange_a.markets)} markets loaded")

        log.info(f"Loading {self.name_b} markets...")
        self.exchange_b = _make_exchange(self.name_b)
        if self.name_b == "kucoin":
            await _load_kucoin_spot_only(self.exchange_b)
        else:
            await self.exchange_b.load_markets()
            log.info(f"  {self.name_b}: {len(self.exchange_b.markets)} markets loaded")

        syms_a = set(self.exchange_a.markets.keys())
        syms_b = set(self.exchange_b.markets.keys())
        self._common_symbols = [s for s in config.SYMBOLS if s in syms_a and s in syms_b]
        skipped = [s for s in config.SYMBOLS if s not in self._common_symbols]
        log.info(f"Pairs on both exchanges: {self._common_symbols}")
        if skipped:
            log.warning(f"Skipped (not on both): {skipped}")

    async def fetch_prices(self, symbol: str) -> dict:
        try:
            ob_a, ob_b = await asyncio.gather(
                self.exchange_a.fetch_order_book(symbol, limit=20),
                self.exchange_b.fetch_order_book(symbol, limit=20),
            )
            self._fail_counts[symbol] = 0
            return {
                self.name_a: {
                    "bid": ob_a["bids"][0][0] if ob_a["bids"] else None,
                    "ask": ob_a["asks"][0][0] if ob_a["asks"] else None,
                },
                self.name_b: {
                    "bid": ob_b["bids"][0][0] if ob_b["bids"] else None,
                    "ask": ob_b["asks"][0][0] if ob_b["asks"] else None,
                },
            }
        except Exception as e:
            self._fail_counts[symbol] = self._fail_counts.get(symbol, 0) + 1
            backoff = min(2 ** self._fail_counts[symbol], 60)
            log.error(f"Price fetch failed {symbol}: {e} (backoff {backoff}s)")
            await asyncio.sleep(backoff)
            return {}

    def find_opportunity(self, symbol: str, prices: dict) -> Optional[ArbitrageOpportunity]:
        if not prices:
            return None
        a = prices[self.name_a]
        b = prices[self.name_b]
        if None in (a["bid"], a["ask"], b["bid"], b["ask"]):
            return None
        net_fee = config.FEE_A + config.FEE_B
        spread1 = (b["bid"] - a["ask"]) / a["ask"] * 100
        spread2 = (a["bid"] - b["ask"]) / b["ask"] * 100
        if spread1 - net_fee > config.MIN_SPREAD_PCT:
            return ArbitrageOpportunity(symbol, self.name_a, self.name_b,
                                        a["ask"], b["bid"], spread1, time.time())
        if spread2 - net_fee > config.MIN_SPREAD_PCT:
            return ArbitrageOpportunity(symbol, self.name_b, self.name_a,
                                        b["ask"], a["bid"], spread2, time.time())
        return None

    async def get_usdt_balance(self, name: str) -> float:
        ex = self.exchange_a if name == self.name_a else self.exchange_b
        try:
            bal = await ex.fetch_balance()
            return float(bal["USDT"]["free"])
        except Exception as e:
            log.error(f"Balance fetch failed on {name}: {e}")
            return 0.0

    async def execute_trade(self, opp: ArbitrageOpportunity) -> bool:
        buy_ex  = self.exchange_a if opp.buy_exchange  == self.name_a else self.exchange_b
        sell_ex = self.exchange_a if opp.sell_exchange == self.name_a else self.exchange_b

        if config.DRY_RUN:
            trade_usdt = config.MAX_TRADE_USDT
        else:
            usdt_bal   = await self.get_usdt_balance(opp.buy_exchange)
            trade_usdt = min(usdt_bal * config.MAX_TRADE_FRACTION, config.MAX_TRADE_USDT)
            if trade_usdt < config.MIN_TRADE_USDT:
                log.warning(f"Insufficient balance: {trade_usdt:.2f} USDT on {opp.buy_exchange}")
                return False

        amount = Decimal(str(trade_usdt / opp.buy_price)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN)
        log.info(f"[ARB] {opp.symbol} | Buy {opp.buy_exchange} @ {opp.buy_price:.4f} "
                 f"| Sell {opp.sell_exchange} @ {opp.sell_price:.4f} "
                 f"| Spread {opp.spread_pct:.3f}% | Qty {amount}")

        if config.DRY_RUN:
            if trade_usdt < config.MIN_TRADE_USDT:
                trade_usdt = config.MAX_TRADE_USDT
                amount = Decimal(str(trade_usdt / opp.buy_price)).quantize(
                    Decimal("0.0001"), rounding=ROUND_DOWN)
            profit = float(amount) * (opp.sell_price - opp.buy_price)
            log.info(f"[DRY RUN] {opp.symbol} | Buy {opp.buy_exchange} @ {opp.buy_price} "
                     f"| Sell {opp.sell_exchange} @ {opp.sell_price} "
                     f"| Spread {opp.spread_pct:.3f}% | Profit {profit:.4f} USDT")
            self.trade_count += 1
            self.total_profit_usdt += profit
            await self._post_trade_to_api(opp, profit, dry_run=True)
            return True

        try:
            buy_ord, sell_ord = await asyncio.gather(
                buy_ex.create_market_buy_order(opp.symbol, float(amount)),
                sell_ex.create_market_sell_order(opp.symbol, float(amount)),
            )
            profit = float(amount) * (opp.sell_price - opp.buy_price)
            self.trade_count += 1
            self.total_profit_usdt += profit
            log.info(f"[OK] Buy {buy_ord['id']} | Sell {sell_ord['id']} | Profit {profit:.4f} USDT")
            await self._post_trade_to_api(opp, profit, dry_run=False)
            return True
        except Exception as e:
            log.error(f"[FAIL] Trade failed: {e}")
            return False

    async def run(self):
        log.info(f"Arbitrage bot starting ({self.name_a} <-> {self.name_b})...")
        log.info(f"Pairs: {config.SYMBOLS} | Min spread: {config.MIN_SPREAD_PCT}% | Dry run: {config.DRY_RUN}")
        try:
            await self._load_markets()
        except Exception as e:
            log.error(f"Startup failed: {e}")
            await self._close_all()
            return
        if not self._common_symbols:
            log.error("No common symbols. Update config.SYMBOLS.")
            await self._close_all()
            return
        self.running = True
        try:
            while self.running:
                for sym in self._common_symbols:
                    prices = await self.fetch_prices(sym)
                    opp = self.find_opportunity(sym, prices)
                    if opp:
                        await self.execute_trade(opp)
                    elif prices:
                        a = prices[self.name_a]
                        b = prices[self.name_b]
                        if a["ask"] and b["bid"]:
                            spread = (b["bid"] - a["ask"]) / a["ask"] * 100
                            log.debug(f"{sym} spread: {spread:.4f}%")
                await asyncio.sleep(config.POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            log.info("Bot stopped.")
        finally:
            await self._close_all()
            log.info(f"Session: {self.trade_count} trades | "
                     f"Est. profit: {self.total_profit_usdt:.4f} USDT")

    def stop(self):
        self.running = False


async def main():
    bot = ArbitrageBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
