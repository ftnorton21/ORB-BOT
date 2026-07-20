"""
Multi-Timeframe ORB Bot Engine
================================
GitHub Actions runs this every 5 minutes during US market hours.
Designed to replace the single-timeframe ORB bot.

Flow:
- 9:30-10:00am ET: Build 30-min opening range
- 10:00am-3:45pm ET: Scan for 10min breakout + 2min EMA touch entries
- 3:45pm ET: Close all positions EOD
"""

import asyncio
import logging
from datetime import datetime, time, date
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv
load_dotenv()

from core.orb_strategy import MultiTFORBStrategy
from core.alpaca_client import AlpacaClient
from signals.discord_sender import DiscordSender

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_URL", "")
FIXED_TRADE_SIZE_USD = 1000
EOD_CLOSE_TIME       = time(15, 45)

WATCHLIST = {
    "crypto": ["BTC/USD", "ETH/USD"],
    "stocks": ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META", "MSFT", "AAPL", "AMZN"],
}


class ORBEngine:
    def __init__(self):
        self.strategy = MultiTFORBStrategy()
        self.alpaca   = AlpacaClient()
        self.discord  = DiscordSender(DISCORD_WEBHOOK_URL)

        # Track breakout direction per symbol once 10min confirms
        # symbol -> "BUY" or "SELL" or None
        self.confirmed_breakouts: dict[str, str] = {}

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _is_market_open(self) -> bool:
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)

    def _is_orb_window(self) -> bool:
        now = self._now_et()
        return time(9, 30) <= now.time() < time(10, 0)

    def _is_eod(self) -> bool:
        return self._now_et().time() >= EOD_CLOSE_TIME

    async def run(self):
        """Single run cycle — GitHub Actions calls this every 5 minutes."""
        now_et = self._now_et()
        log.info(f"ORB Bot triggered - ET: {now_et.strftime('%A %I:%M %p')}")

        if not self._is_market_open():
            log.info("Market closed — nothing to do")
            return

        # EOD — close everything
        if self._is_eod():
            log.info("EOD — closing all open positions")
            await self._close_all_positions()
            return

        # During ORB window — just log the range being built
        if self._is_orb_window():
            log.info("Inside 30-min ORB window (9:30-10:00am ET) — building range, no trades")
            await self._log_opening_ranges()
            return

        # After 10:00am — scan for entries
        log.info(f"Scanning for Multi-TF ORB entries...")
        await self._scan_for_entries()

    async def _log_opening_ranges(self):
        all_symbols = WATCHLIST["crypto"] + WATCHLIST["stocks"]
        for symbol in all_symbols:
            bars = await self.alpaca.get_bars(symbol, timeframe="30Min", limit=1)
            if bars:
                log.info(f"ORB range building: {symbol} H={bars[-1]['h']:.4f} L={bars[-1]['l']:.4f}")

    async def _scan_for_entries(self):
        all_symbols  = WATCHLIST["crypto"] + WATCHLIST["stocks"]
        open_positions = await self.alpaca.get_open_positions()

        for symbol in all_symbols:
            try:
                # Skip if already in position
                alpaca_sym = symbol.replace("/", "") if "/" in symbol else symbol
                if symbol in open_positions or alpaca_sym in open_positions:
                    log.info(f"Skipping {symbol} — position already open")
                    continue

                # Get opening range (30 min bars from today's open)
                bars_30min = await self.alpaca.get_bars(symbol, timeframe="30Min", limit=10)
                if not bars_30min or len(bars_30min) < 1:
                    log.warning(f"No 30min bars for {symbol}")
                    continue

                # First bar = opening range
                orb_bar  = bars_30min[0]
                orb_high = orb_bar["h"]
                orb_low  = orb_bar["l"]

                # Get 10min bars for breakout confirmation
                bars_10min = await self.alpaca.get_bars(symbol, timeframe="10Min", limit=20)
                if not bars_10min:
                    continue

                # Get 2min bars for EMA touch
                bars_2min = await self.alpaca.get_bars(symbol, timeframe="2Min", limit=60)
                if not bars_2min:
                    continue

                # Day high/low for target
                day_high = max(b["h"] for b in bars_30min)
                day_low  = min(b["l"] for b in bars_30min)

                # Current price
                current_price = await self.alpaca.get_latest_price(symbol)
                if not current_price:
                    continue

                # Generate signal
                signal = self.strategy.generate_signal(
                    symbol=symbol,
                    current_price=current_price,
                    orb_high=orb_high,
                    orb_low=orb_low,
                    bars_10min=bars_10min,
                    bars_2min=bars_2min,
                    day_high=day_high,
                    day_low=day_low,
                )

                if not signal:
                    log.info(f"No entry yet: {symbol} price={current_price:.4f} ORB={orb_low:.4f}-{orb_high:.4f}")
                    continue

                # Skip crypto shorts
                is_crypto = "/" in symbol
                if is_crypto and signal["direction"] == "SELL":
                    log.info(f"Skipping SELL on {symbol} — crypto long only")
                    continue

                # Place trade
                order = await self.alpaca.place_order(
                    symbol=symbol,
                    side="buy" if signal["direction"] == "BUY" else "sell",
                    trade_size_usd=FIXED_TRADE_SIZE_USD,
                    entry_price=current_price,
                    signal=signal,
                )

                if order:
                    log.info(f"Trade placed: {symbol} {signal['direction']} @ {current_price}")
                    await self.discord.send(signal)
                else:
                    log.error(f"Trade failed for {symbol}")

            except Exception as e:
                log.error(f"Error processing {symbol}: {e}")

        log.info("Scan complete")

    async def _close_all_positions(self):
        positions = await self.alpaca.get_open_positions()
        if not positions:
            log.info("No open positions to close")
            return

        for symbol in positions:
            closed = await self.alpaca.close_position(symbol)
            if closed:
                await self.discord.send_alert(f"EOD close: {symbol}")


if __name__ == "__main__":
    engine = ORBEngine()
    asyncio.run(engine.run())
