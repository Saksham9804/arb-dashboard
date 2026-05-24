"""
config.py — Universal config for the Arbitrage Bot
Set EXCHANGE_A and EXCHANGE_B to any supported pair.

Supported exchanges: kucoin, binance, wazirx, bybit
The bot will auto-resolve their IPs via Cloudflare DoH to bypass ISP blocks.
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:

    # ── Which exchanges to arb between ───────────────────────────────────
    # Change these two lines to switch exchange pairs instantly.
    EXCHANGE_A: str = "kucoin"
    EXCHANGE_B: str = "wazirx"

    # ── KuCoin ────────────────────────────────────────────────────────────
    KUCOIN_API_KEY:    str = os.getenv("KUCOIN_API_KEY", "")
    KUCOIN_SECRET:     str = os.getenv("KUCOIN_SECRET", "")
    KUCOIN_PASSPHRASE: str = os.getenv("KUCOIN_PASSPHRASE", "")

    # ── Binance ───────────────────────────────────────────────────────────
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET:  str = os.getenv("BINANCE_SECRET", "")

    # ── WazirX ────────────────────────────────────────────────────────────
    WAZIRX_API_KEY: str = os.getenv("WAZIRX_API_KEY", "")
    WAZIRX_SECRET:  str = os.getenv("WAZIRX_SECRET", "")

    # ── Bybit ─────────────────────────────────────────────────────────────
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_SECRET:  str = os.getenv("BYBIT_SECRET", "")

    # ── Pairs to monitor ─────────────────────────────────────────────────
    SYMBOLS: List[str] = field(default_factory=lambda: [
        "BTC/USDT",
        "ETH/USDT",
        "XRP/USDT",
        "SOL/USDT",
        "BNB/USDT",
    ])

    # ── Fees (taker %) — update when exchange promos end ─────────────────
    FEE_A: float = 0.1   # KuCoin taker
    FEE_B: float = 0.0   # WazirX: 0% promo fee (revert to 0.2 when normal fees resume)

    # ── Strategy ─────────────────────────────────────────────────────────
    MIN_SPREAD_PCT: float = 0.05   # lower from 0.3 to 0.05

    # ── Trade sizing ─────────────────────────────────────────────────────
    MAX_TRADE_FRACTION: float = 0.95
    MAX_TRADE_USDT:     float = 100.0
    MIN_TRADE_USDT:     float = 10.0

    # ── Timing ───────────────────────────────────────────────────────────
    POLL_INTERVAL_SEC: float = 1.0

    # ── Safety — always test with DRY_RUN=true first! ────────────────────
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"


config = Config()