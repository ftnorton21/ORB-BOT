"""
Opening Range Breakout (ORB) Trading Bot
=========================================
- Pulls 9:30-10:00am ET opening range from Alpaca for crypto + stocks
- Fires BUY when price breaks above opening range high
- Fires SELL when price breaks below opening range low  
- Fixed $1000 per trade, one trade per asset per day
- Auto-closes all positions at 3:45pm ET (before market close)
- Sends signals to Discord
- Runs 24/7 on Render (free tier)
"""

import asyncio
import logging
from datetime import datetime, time, date
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv
load_dotenv()

from core.alpaca_client import AlpacaClient
from core.orb_strategy import ORBStrategy
from signals.discord_sender import DiscordSender

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ─── Config ───────────────────────────────────────────────────────────────────

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

WATCHLIST = {
    "crypto": ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD"],
    "stocks": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
}

FIXED_TRADE_SIZE_USD = 1000
ORB_WINDOW_MINUTES   = 30    # 9:30-10:00am ET
SCAN_INTERVAL        = 60    # check every 60 seconds during market hours
EOD_CLOSE_TIME       = time(15, 45)  # close all positions at 3:45pm ET


# ─── Engine ───────────────────────────────────────────────────────────────────

class ORBEngine:
    def __init__(self):
        self.alpaca   = AlpacaClient()
        self.strategy = ORBStrategy(orb_minutes=ORB_WINDOW_MINUTES)
        self.discord  = DiscordSender(DISCORD_WEBHOOK_URL)

        # Track which assets have traded today
        self.traded_today: set[str] = set()
        self.last_reset_date: date  = date.today()

        # Opening ranges: symbol -> {high, low, established}
        self.opening_ranges: dict[str, dict] = {}

        all_symbols = WATCHLIST["crypto"] + WATCHLIST["stocks"]
        for sym in all_symbols:
            self.opening_ranges[sym] = {"high": None, "low": None, "established": False}

    def _reset_daily(self):
        today = date.today()
        if today != self.last_reset_date:
            log.info("New trading day - resetting daily state")
            self.traded_today.clear()
            for sym in self.opening_ranges:
                self.opening_ranges[sym] = {"high": None, "low": None, "established": False}
            self.last_reset_date = today

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _is_market_open(self) -> bool:
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)

    def _is_orb_window(self) -> bool:
        now = self._now_et()
        return time(9, 30) <= now.time() <= time(9, 30).replace(
            minute=30 + ORB_WINDOW_MINUTES - 30
        )

    def _is_eod_close_time(self) -> bool:
        now = self._now_et()
        return now.time() >= EOD_CLOSE_TIME

    async def run(self):
        log.info("ORB Bot started")
        log.info(f"Watching: {WATCHLIST['crypto'] + WATCHLIST['stocks']}")
        log.info(f"Trade size: ${FIXED_TRADE_SIZE_USD} | ORB window: {ORB_WINDOW_MINUTES} mins")

        while True:
            try:
                self._reset_daily()
                now_et = self._now_et()

                if not self._is_market_open():
                    log.info(f"Market closed - ET time: {now_et.strftime('%A %I:%M %p')}")
                    await asyncio.sleep(900)  # check every 15 mins outside hours
                    continue

                # End of day — close all open positions
                if self._is_eod_close_time():
                    await self._close_all_positions()
                    await asyncio.sleep(300)
                    continue

                # During ORB window — build opening ranges
                if time(9, 30) <= now_et.time() <= time(10, 0):
                    await self._update_opening_ranges()

                # After ORB window — scan for breakouts
                elif now_et.time() > time(10, 0):
                    await self._scan_for_breakouts()

            except Exception as e:
                log.error(f"Engine error: {e}")

            await asyncio.sleep(SCAN_INTERVAL)

    async def _update_opening_ranges(self):
        """Build the opening range high/low for each symbol during 9:30-10:00am."""
        all_symbols = WATCHLIST["crypto"] + WATCHLIST["stocks"]

        for symbol in all_symbols:
            try:
                bars = await self.alpaca.get_bars(symbol, timeframe="1Min", limit=30)
                if not bars:
                    continue

                orb_high = max(b["h"] for b in bars)
                orb_low  = min(b["l"] for b in bars)

                self.opening_ranges[symbol] = {
                    "high": orb_high,
                    "low":  orb_low,
                    "established": True
                }
                log.info(f"ORB set: {symbol} H={orb_high:.4f} L={orb_low:.4f}")

            except Exception as e:
                log.error(f"Error updating ORB for {symbol}: {e}")

    async def _scan_for_breakouts(self):
        """Check current price against opening range for breakout signals."""
        all_symbols = WATCHLIST["crypto"] + WATCHLIST["stocks"]

        for symbol in all_symbols:
            if symbol in self.traded_today:
                continue

            orb = self.opening_ranges.get(symbol, {})
            if not orb.get("established"):
                continue

            try:
                current_price = await self.alpaca.get_latest_price(symbol)
                if not current_price:
                    continue

                signal = self.strategy.check_breakout(
                    symbol=symbol,
                    current_price=current_price,
                    orb_high=orb["high"],
                    orb_low=orb["low"],
                )

                if signal:
                    await self._execute_signal(signal)

            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

    async def _execute_signal(self, signal: dict):
        symbol    = signal["symbol"]
        direction = signal["direction"]
        entry     = signal["entry"]

        # Check if already have open position
        open_positions = await self.alpaca.get_open_positions()
        alpaca_symbol  = symbol.replace("/", "")  # BTC/USD -> BTCUSD for stocks check
        if symbol in open_positions or alpaca_symbol in open_positions:
            log.info(f"Skipping {symbol} - position already open")
            return

        # Place the trade
        order = await self.alpaca.place_order(
            symbol=symbol,
            side="buy" if direction == "BUY" else "sell",
            trade_size_usd=FIXED_TRADE_SIZE_USD,
            entry_price=entry,
        )

        if order:
            self.traded_today.add(symbol)
            log.info(f"Trade placed: {symbol} {direction} @ {entry}")
            await self.discord.send(signal)
        else:
            log.error(f"Trade failed for {signal}")

    async def _close_all_positions(self):
        """Close all open positions at end of day."""
        positions = await self.alpaca.get_open_positions()
        if not positions:
            return

        log.info(f"EOD: closing {len(positions)} open positions")
        for symbol in positions:
            await self.alpaca.close_position(symbol)
            await self.discord.send_alert(f"EOD close: {symbol}")


if __name__ == "__main__":
    engine = ORBEngine()
    asyncio.run(engine.run())
