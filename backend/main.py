"""
FastAPI backend — serves trade data and live prices via REST + WebSocket.
The bot and this server share a SQLite DB (or Postgres on Railway).
"""

import os, json, asyncio, time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Field, SQLModel, create_engine, Session, select
import uvicorn

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///arb_bot.db")

# Railway Postgres URLs start with postgres:// — SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)


# ── Models ────────────────────────────────────────────────────────────────────

class Trade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_pct: float
    qty: float
    profit_usdt: float
    dry_run: bool = True


class PriceTick(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float
    symbol: str
    exchange_a_bid: float
    exchange_a_ask: float
    exchange_b_bid: float
    exchange_b_ask: float
    spread_pct: float


SQLModel.metadata.create_all(engine)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Arb Bot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

manager = ConnectionManager()


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary():
    with Session(engine) as s:
        trades = s.exec(select(Trade)).all()
        total_profit = sum(t.profit_usdt for t in trades)
        total_trades = len(trades)
        wins = [t for t in trades if t.profit_usdt > 0]
        last_24h = [t for t in trades if time.time() - t.timestamp < 86400]
        return {
            "total_trades": total_trades,
            "total_profit_usdt": round(total_profit, 4),
            "win_rate": round(len(wins) / total_trades * 100, 1) if trades else 0,
            "trades_24h": len(last_24h),
            "profit_24h": round(sum(t.profit_usdt for t in last_24h), 4),
        }

@app.get("/api/trades")
def get_trades(limit: int = 100):
    with Session(engine) as s:
        trades = s.exec(select(Trade).order_by(Trade.timestamp.desc()).limit(limit)).all()
        return [t.model_dump() for t in trades]

@app.get("/api/prices")
def get_prices(limit: int = 200):
    with Session(engine) as s:
        ticks = s.exec(select(PriceTick).order_by(PriceTick.timestamp.desc()).limit(limit)).all()
        return [t.model_dump() for t in reversed(ticks)]

@app.get("/api/profit-chart")
def profit_chart():
    """Cumulative profit over time for the chart."""
    with Session(engine) as s:
        trades = s.exec(select(Trade).order_by(Trade.timestamp)).all()
        cumulative = 0.0
        points = []
        for t in trades:
            cumulative += t.profit_usdt
            points.append({
                "time": datetime.fromtimestamp(t.timestamp, tz=timezone.utc).isoformat(),
                "profit": round(cumulative, 4),
                "trade_profit": round(t.profit_usdt, 4),
                "symbol": t.symbol,
            })
        return points

@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.time()}


# ── WebSocket for live updates ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ── Called by bot to record events ───────────────────────────────────────────

@app.post("/api/trade")
async def record_trade(trade: Trade):
    with Session(engine) as s:
        s.add(trade)
        s.commit()
        s.refresh(trade)
    await manager.broadcast({"type": "trade", "data": trade.model_dump()})
    return {"ok": True}

@app.post("/api/tick")
async def record_tick(tick: PriceTick):
    with Session(engine) as s:
        s.add(tick)
        s.commit()
    await manager.broadcast({"type": "tick", "data": tick.model_dump()})
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
