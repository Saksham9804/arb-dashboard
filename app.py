"""
Crypto Arbitrage Bot — Streamlit Dashboard
Binance × KuCoin | Live & Dry-Run modes
"""

import asyncio
import time
import threading
import queue
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import ccxt.async_support as ccxt

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Arb Bot · Binance × KuCoin",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@400;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Syne', sans-serif; }
code, .mono { font-family: 'IBM Plex Mono', monospace; }

[data-testid="stSidebar"] {
    background: #0a0a0f;
    border-right: 1px solid #1e1e2e;
}
[data-testid="stSidebar"] * { color: #e2e2f0 !important; }
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stNumberInput label { color: #7f7f9a !important; font-size: 12px; }

.metric-card {
    background: #0d0d1a;
    border: 1px solid #1e1e2e;
    border-radius: 12px;
    padding: 18px 20px;
    text-align: center;
}
.metric-card .label { font-size: 11px; color: #5f5f7a; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 6px; }
.metric-card .value { font-size: 26px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; }
.metric-card .value.green { color: #00e5a0; }
.metric-card .value.red   { color: #ff4f6d; }
.metric-card .value.blue  { color: #5b8cff; }
.metric-card .value.white { color: #e2e2f0; }

.log-entry { font-family: 'IBM Plex Mono', monospace; font-size: 12px; padding: 3px 0; }
.log-entry.ok   { color: #00e5a0; }
.log-entry.warn { color: #ffb347; }
.log-entry.err  { color: #ff4f6d; }
.log-entry.info { color: #5b8cff; }
.log-entry.dim  { color: #5f5f7a; }

.pill-running {
    display: inline-block;
    background: rgba(0,229,160,0.12);
    color: #00e5a0;
    border: 1px solid rgba(0,229,160,0.3);
    border-radius: 999px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .05em;
}
.pill-stopped {
    display: inline-block;
    background: rgba(255,79,109,0.12);
    color: #ff4f6d;
    border: 1px solid rgba(255,79,109,0.3);
    border-radius: 999px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .05em;
}
.pill-dryrun {
    display: inline-block;
    background: rgba(91,140,255,0.12);
    color: #5b8cff;
    border: 1px solid rgba(91,140,255,0.3);
    border-radius: 999px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .05em;
}

h1, h2, h3 { font-family: 'Syne', sans-serif !important; }
div[data-testid="stHorizontalBlock"] > div { gap: 12px; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ─────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "running": False,
        "dry_run": True,
        "trade_count": 0,
        "total_profit": 0.0,
        "opps_found": 0,
        "best_spread": 0.0,
        "log": [],
        "spread_history": {p: [] for p in ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT"]},
        "price_data": {},
        "trade_history": [],
        "bot_thread": None,
        "stop_event": None,
        "msg_queue": queue.Queue(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ── Bot engine (runs in background thread) ─────────────────────────────────────
class BotEngine:
    def __init__(self, cfg, msg_queue, stop_event):
        self.cfg = cfg
        self.q = msg_queue
        self.stop = stop_event

    def push(self, kind, data):
        self.q.put({"kind": kind, "data": data, "ts": time.time()})

    async def fetch_prices(self, binance, kucoin, symbol):
        try:
            ob_b, ob_k = await asyncio.gather(
                binance.fetch_order_book(symbol, limit=5),
                kucoin.fetch_order_book(symbol, limit=5),
            )
            return {
                "binance": {
                    "bid": ob_b["bids"][0][0] if ob_b["bids"] else None,
                    "ask": ob_b["asks"][0][0] if ob_b["asks"] else None,
                },
                "kucoin": {
                    "bid": ob_k["bids"][0][0] if ob_k["bids"] else None,
                    "ask": ob_k["asks"][0][0] if ob_k["asks"] else None,
                },
            }
        except Exception as e:
            self.push("log", {"msg": f"Price fetch error {symbol}: {e}", "cls": "err"})
            return {}

    def calc_spread(self, prices):
        b, k = prices.get("binance", {}), prices.get("kucoin", {})
        if None in (b.get("bid"), b.get("ask"), k.get("bid"), k.get("ask")):
            return 0, None
        s1 = (b["bid"] - k["ask"]) / k["ask"] * 100
        s2 = (k["bid"] - b["ask"]) / b["ask"] * 100
        if s1 > s2:
            return s1, ("kucoin", "binance", k["ask"], b["bid"])
        return s2, ("binance", "kucoin", b["ask"], k["bid"])

    async def execute(self, binance, kucoin, opp, symbol):
        buy_ex_name, sell_ex_name, buy_price, sell_price = opp
        buy_ex  = binance if buy_ex_name  == "binance" else kucoin
        sell_ex = binance if sell_ex_name == "binance" else kucoin
        trade_usdt = min(self.cfg["max_trade_usdt"], self.cfg["max_trade_usdt"])
        amount = float(Decimal(str(trade_usdt / buy_price)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN))
        est_profit = amount * (sell_price - buy_price)

        if self.cfg["dry_run"]:
            self.push("trade", {
                "symbol": symbol, "buy_ex": buy_ex_name, "sell_ex": sell_ex_name,
                "buy_price": buy_price, "sell_price": sell_price,
                "amount": amount, "profit": est_profit, "dry": True
            })
            self.push("log", {"msg": f"[DRY] {symbol} buy@{buy_price:.4f} sell@{sell_price:.4f} | +{est_profit:.4f} USDT", "cls": "ok"})
            return

        try:
            buy_order, sell_order = await asyncio.gather(
                buy_ex.create_market_buy_order(symbol, amount),
                sell_ex.create_market_sell_order(symbol, amount),
            )
            self.push("trade", {
                "symbol": symbol, "buy_ex": buy_ex_name, "sell_ex": sell_ex_name,
                "buy_price": buy_price, "sell_price": sell_price,
                "amount": amount, "profit": est_profit, "dry": False
            })
            self.push("log", {"msg": f"✅ LIVE {symbol} | +{est_profit:.4f} USDT | IDs {buy_order['id']} / {sell_order['id']}", "cls": "ok"})
        except Exception as e:
            self.push("log", {"msg": f"❌ Trade failed: {e}", "cls": "err"})

    async def run_async(self):
        cfg = self.cfg
        fee_total = cfg["binance_fee"] + cfg["kucoin_fee"]

        binance = ccxt.binance({"apiKey": cfg["binance_key"], "secret": cfg["binance_secret"], "enableRateLimit": True})
        kucoin  = ccxt.kucoin({"apiKey": cfg["kucoin_key"], "secret": cfg["kucoin_secret"], "password": cfg["kucoin_pass"], "enableRateLimit": True})

        self.push("log", {"msg": f"Bot started | pairs: {cfg['symbols']} | min spread: {cfg['min_spread']}%", "cls": "info"})

        try:
            while not self.stop.is_set():
                for symbol in cfg["symbols"]:
                    prices = await self.fetch_prices(binance, kucoin, symbol)
                    if not prices:
                        continue
                    spread, opp_data = self.calc_spread(prices)
                    self.push("prices", {"symbol": symbol, "prices": prices, "spread": spread})

                    net = spread - fee_total
                    if net > cfg["min_spread"] and opp_data:
                        self.push("log", {"msg": f"⚡ Opp on {symbol} | spread {spread:.3f}% | net {net:.3f}%", "cls": "warn"})
                        await self.execute(binance, kucoin, opp_data, symbol)

                await asyncio.sleep(cfg["poll_interval"])
        finally:
            await binance.close()
            await kucoin.close()
            self.push("log", {"msg": "Bot stopped cleanly.", "cls": "dim"})

    def run(self):
        asyncio.run(self.run_async())


def start_bot(cfg):
    stop_event = threading.Event()
    engine = BotEngine(cfg, st.session_state.msg_queue, stop_event)
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    st.session_state.bot_thread = t
    st.session_state.stop_event = stop_event
    st.session_state.running = True

def stop_bot():
    if st.session_state.stop_event:
        st.session_state.stop_event.set()
    st.session_state.running = False


# ── Drain message queue into session state ──────────────────────────────────────
def drain_queue():
    q = st.session_state.msg_queue
    while not q.empty():
        msg = q.get_nowait()
        kind = msg["kind"]
        if kind == "log":
            st.session_state.log.append({"ts": datetime.fromtimestamp(msg["ts"]).strftime("%H:%M:%S"), **msg["data"]})
            if len(st.session_state.log) > 200:
                st.session_state.log = st.session_state.log[-200:]
        elif kind == "prices":
            sym = msg["data"]["symbol"]
            st.session_state.price_data[sym] = msg["data"]["prices"]
            hist = st.session_state.spread_history[sym]
            hist.append({"t": msg["ts"], "spread": msg["data"]["spread"]})
            if len(hist) > 120:
                st.session_state.spread_history[sym] = hist[-120:]
        elif kind == "trade":
            d = msg["data"]
            st.session_state.trade_count += 1
            st.session_state.total_profit += d["profit"]
            if d["profit"] > 0:
                st.session_state.opps_found += 1
            st.session_state.trade_history.append({
                "Time": datetime.fromtimestamp(msg["ts"]).strftime("%H:%M:%S"),
                "Pair": d["symbol"],
                "Buy on": d["buy_ex"].capitalize(),
                "Sell on": d["sell_ex"].capitalize(),
                "Buy price": round(d["buy_price"], 4),
                "Sell price": round(d["sell_price"], 4),
                "Amount": round(d["amount"], 4),
                "Profit (USDT)": round(d["profit"], 5),
                "Mode": "Dry run" if d["dry"] else "🔴 Live",
            })

drain_queue()


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ Arb Bot Config")
    st.markdown("---")

    st.markdown("### API Keys")
    binance_key    = st.text_input("Binance API Key",    type="password", placeholder="Enter key...")
    binance_secret = st.text_input("Binance Secret",     type="password", placeholder="Enter secret...")
    st.markdown("---")
    kucoin_key     = st.text_input("KuCoin API Key",     type="password", placeholder="Enter key...")
    kucoin_secret  = st.text_input("KuCoin Secret",      type="password", placeholder="Enter secret...")
    kucoin_pass    = st.text_input("KuCoin Passphrase",  type="password", placeholder="Enter passphrase...")
    st.markdown("---")

    st.markdown("### Strategy")
    symbols = st.multiselect(
        "Trading pairs",
        ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","DOGE/USDT","ADA/USDT"],
        default=["BTC/USDT","ETH/USDT","SOL/USDT"],
    )
    min_spread     = st.slider("Min net spread (%)", 0.1, 2.0, 0.3, 0.05)
    max_trade_usdt = st.number_input("Max trade size (USDT)", 10, 5000, 100, 10)
    poll_interval  = st.slider("Poll interval (sec)", 0.5, 5.0, 1.0, 0.5)
    binance_fee    = st.number_input("Binance taker fee (%)", 0.01, 0.5, 0.1, 0.01)
    kucoin_fee     = st.number_input("KuCoin taker fee (%)",  0.01, 0.5, 0.1, 0.01)
    st.markdown("---")

    dry_run = st.toggle("Dry run (no real trades)", value=True)
    if not dry_run:
        st.warning("⚠️ Live mode will place real orders!")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Start", use_container_width=True, disabled=st.session_state.running):
            cfg = {
                "binance_key": binance_key, "binance_secret": binance_secret,
                "kucoin_key": kucoin_key, "kucoin_secret": kucoin_secret, "kucoin_pass": kucoin_pass,
                "symbols": symbols or ["BTC/USDT"],
                "min_spread": min_spread, "max_trade_usdt": max_trade_usdt,
                "poll_interval": poll_interval, "binance_fee": binance_fee, "kucoin_fee": kucoin_fee,
                "dry_run": dry_run,
            }
            start_bot(cfg)
            st.rerun()
    with col2:
        if st.button("⏹ Stop", use_container_width=True, disabled=not st.session_state.running):
            stop_bot()
            st.rerun()


# ── Main header ────────────────────────────────────────────────────────────────
c1, c2 = st.columns([3, 1])
with c1:
    st.markdown("# ⚡ Crypto Arbitrage Bot")
    st.markdown("Binance × KuCoin · Real-time spread monitor")
with c2:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.session_state.running:
        mode = "DRY RUN" if dry_run else "LIVE"
        cls = "pill-dryrun" if dry_run else "pill-running"
        st.markdown(f'<span class="{cls}">● {mode}</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="pill-stopped">● STOPPED</span>', unsafe_allow_html=True)

st.markdown("---")

# ── Metric cards ────────────────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
profit_color = "green" if st.session_state.total_profit >= 0 else "red"
profit_sign  = "+" if st.session_state.total_profit >= 0 else ""

with m1:
    st.markdown(f"""<div class="metric-card">
        <div class="label">Total profit</div>
        <div class="value {profit_color}">{profit_sign}{st.session_state.total_profit:.4f}</div>
        <div style="font-size:11px;color:#5f5f7a;margin-top:4px">USDT</div>
    </div>""", unsafe_allow_html=True)
with m2:
    st.markdown(f"""<div class="metric-card">
        <div class="label">Trades executed</div>
        <div class="value white">{st.session_state.trade_count}</div>
        <div style="font-size:11px;color:#5f5f7a;margin-top:4px">total</div>
    </div>""", unsafe_allow_html=True)
with m3:
    st.markdown(f"""<div class="metric-card">
        <div class="label">Best spread seen</div>
        <div class="value blue">{st.session_state.best_spread:.3f}%</div>
        <div style="font-size:11px;color:#5f5f7a;margin-top:4px">this session</div>
    </div>""", unsafe_allow_html=True)
with m4:
    st.markdown(f"""<div class="metric-card">
        <div class="label">Opportunities</div>
        <div class="value white">{st.session_state.opps_found}</div>
        <div style="font-size:11px;color:#5f5f7a;margin-top:4px">above threshold</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Charts ─────────────────────────────────────────────────────────────────────
chart_col, log_col = st.columns([3, 2])

with chart_col:
    st.markdown("#### Spread History")
    active_syms = symbols if symbols else ["BTC/USDT"]
    tab_names = active_syms[:5]
    if tab_names:
        tabs = st.tabs(tab_names)
        for i, sym in enumerate(tab_names):
            with tabs[i]:
                hist = st.session_state.spread_history.get(sym, [])
                if hist:
                    df = pd.DataFrame(hist)
                    df["t"] = pd.to_datetime(df["t"], unit="s")
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df["t"], y=df["spread"],
                        fill="tozeroy", fillcolor="rgba(0,229,160,0.08)",
                        line=dict(color="#00e5a0", width=2),
                        name="Spread %",
                    ))
                    fig.add_hline(
                        y=min_spread, line_dash="dash",
                        line_color="#ff4f6d", annotation_text=f"threshold {min_spread}%",
                        annotation_font_color="#ff4f6d",
                    )
                    fig.update_layout(
                        height=260, margin=dict(l=0,r=0,t=10,b=0),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#7f7f9a", family="IBM Plex Mono"),
                        xaxis=dict(showgrid=False, color="#3f3f5a"),
                        yaxis=dict(showgrid=True, gridcolor="#1e1e2e", color="#7f7f9a"),
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.caption("Waiting for price data...")
    else:
        st.caption("Select at least one pair in the sidebar.")

with log_col:
    st.markdown("#### Activity Log")
    log_lines = st.session_state.log[-40:][::-1]
    log_html = "".join(
        f'<div class="log-entry {e.get("cls","dim")}">'
        f'<span style="color:#3f3f5a">[{e["ts"]}]</span> {e["msg"]}</div>'
        for e in log_lines
    ) if log_lines else '<div class="log-entry dim">No activity yet.</div>'
    st.markdown(
        f'<div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:10px;'
        f'padding:14px;height:300px;overflow-y:auto;font-size:12px">{log_html}</div>',
        unsafe_allow_html=True
    )

st.markdown("<br>", unsafe_allow_html=True)

# ── Live price table ───────────────────────────────────────────────────────────
st.markdown("#### Live Prices")
price_rows = []
for sym in (symbols or ["BTC/USDT"]):
    pd_ = st.session_state.price_data.get(sym, {})
    b = pd_.get("binance", {})
    k = pd_.get("kucoin", {})
    b_ask = b.get("ask") or "—"
    k_ask = k.get("ask") or "—"
    b_bid = b.get("bid") or "—"
    k_bid = k.get("bid") or "—"
    spread = "—"
    if b_ask != "—" and k_bid != "—":
        s = (float(k_bid) - float(b_ask)) / float(b_ask) * 100
        spread = f"{s:.4f}%"
    price_rows.append({
        "Pair": sym,
        "Binance Ask": round(b_ask, 4) if b_ask != "—" else "—",
        "KuCoin Ask":  round(k_ask, 4) if k_ask != "—" else "—",
        "Binance Bid": round(b_bid, 4) if b_bid != "—" else "—",
        "KuCoin Bid":  round(k_bid, 4) if k_bid != "—" else "—",
        "Spread (KuCoin→Binance)": spread,
    })
if price_rows:
    st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True)

# ── Trade history table ────────────────────────────────────────────────────────
if st.session_state.trade_history:
    st.markdown("#### Trade History")
    st.dataframe(
        pd.DataFrame(st.session_state.trade_history[::-1]),
        use_container_width=True, hide_index=True
    )

# ── Auto-refresh ───────────────────────────────────────────────────────────────
if st.session_state.running:
    time.sleep(1.5)
    st.rerun()