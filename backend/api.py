# api.py
import os, json, time
from flask import Flask, jsonify
from flask_cors import CORS
from collections import deque

app = Flask(__name__)
CORS(app, origins=["https://crysp.com", "https://*.vercel.app", "*"])

# In-memory store (Railway restarts will clear it, but bot repopulates quickly)
trades = deque(maxlen=500)
summary = {"total_profit": 0.0, "trade_count": 0, "start_time": time.time()}

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/trades")
def get_trades():
    return jsonify(list(trades))

@app.route("/api/summary")
def get_summary():
    return jsonify(summary)

@app.route("/api/profit-chart")
def get_profit_chart():
    chart = [{"time": t["time"], "profit": t["profit"]} for t in trades]
    return jsonify(chart)

@app.route("/api/post-trade", methods=["POST"])
def post_trade():
    from flask import request
    data = request.get_json()
    trades.appendleft(data)
    summary["total_profit"] += data.get("profit", 0)
    summary["trade_count"] += 1
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
