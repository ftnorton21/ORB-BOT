"""
Opening Range Breakout (ORB) Trading Bot
=========================================
Designed for GitHub Actions - runs once per trigger then exits.
GitHub Actions cron triggers every 15 mins during US market hours.

- Pulls 9:30-10:00am ET opening range from Alpaca for crypto + stocks
- Fires BUY when price breaks above opening range high
- Fires SELL when price breaks below opening range low
- Fixed $1000 per trade, one trade per asset per day
- Auto-closes all positions at 3:45pm ET
- Sends signals to Discord
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
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

WATCHLIST = {
    "crypto": ["BTC/USD", "ETH/USD"],
    "stocks": ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META", "MSFT", "AAPL", "AMZN"],
}

FIXED_TRADE_SIZE_USD = 1000
ORB_WINDOW_MINUTES   = 30
EOD_CLOSE_TIME       = time(15, 45)


class ORBEngine:
    def __init__(self):
        self.alpaca   = AlpacaClient()
        self.strategy = ORBStrategy(orb_minutes=ORB_WINDOW_MINUTES)
        self.discord  = DiscordSender(DISCORD_WEBHOOK_URL)

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _is_market_open(self) -> bool:
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)

    def _is_orb_window(self) -> bool:
        now = self._now_et()
        return time(9, 30) <= now.time() <= time(10, 0)

    def _is_eod(self) -> bool:
        return self._now_et().time() >= EOD_CLOSE_TIME

    async def run(self):
        """Run one scan cycle then exit - GitHub Actions handles scheduling."""
        now_et = self._now_et()
        log.info(f"ORB Bot triggered - ET time: {now_et.strftime('%A %I:%M %p')}")

        if not self._is_market_open():
            log.info("Market closed - nothing to do")
            return

        # End of day - close all positions
        if self._is_eod():
            log.info("EOD - closing all positions")
            await self._close_all_positions()
            return

        all_symbols = WATCHLIST["crypto"] + WATCHLIST["stocks"]

        # During ORB window - just log, don't trade yet
        if self._is_orb_window():
            log.info(f"Inside ORB window (9:30-10:00am ET) - building opening range")
            for symbol in all_symbols:
                bars = await self.alpaca.get_bars(symbol, timeframe="1Min", limit=30)
                if bars:
                    orb_high = max(b["h"] for b in bars)
                    orb_low  = min(b["l"] for b in bars)
                    log.info(f"ORB range: {symbol} H={orb_high:.4f} L={orb_low:.4f}")
            return

        # After ORB window - scan for breakouts
        log.info("Scanning for ORB breakouts...")
        open_positions = await self.alpaca.get_open_positions()

        for symbol in all_symbols:
            try:
                # Get opening range bars (first 30 mins of today)
                bars = await self.alpaca.get_bars(symbol, timeframe="1Min", limit=100)
                if not bars or len(bars) < 5:
                    continue

                # First 30 mins = opening range
                orb_bars = bars[:30] if len(bars) >= 30 else bars
                orb_high = max(b["h"] for b in orb_bars)
                orb_low  = min(b["l"] for b in orb_bars)

                # Current price
                current_price = await self.alpaca.get_latest_price(symbol)
                if not current_price:
                    continue

                # Check for breakout
                signal = self.strategy.check_breakout(
                    symbol=symbol,
                    current_price=current_price,
                    orb_high=orb_high,
                    orb_low=orb_low,
                )

                if not signal:
                    log.info(f"No breakout: {symbol} price={current_price:.4f} ORB={orb_low:.4f}-{orb_high:.4f}")
                    continue

                # Check if already in a position for this symbol
                alpaca_sym = symbol.replace("/", "")
                if symbol in open_positions or alpaca_sym in open_positions:
                    log.info(f"Skipping {symbol} - position already open")
                    continue

                # Crypto only goes long — Alpaca doesn't support crypto shorts
                is_crypto = "/" in symbol
                if is_crypto and signal["direction"] == "SELL":
                    log.info(f"Skipping SELL on {symbol} - crypto long only")
                    continue

                # Place trade
                order = await self.alpaca.place_order(
                    symbol=symbol,
                    side="buy" if signal["direction"] == "BUY" else "sell",
                    trade_size_usd=FIXED_TRADE_SIZE_USD,
                    entry_price=current_price,
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
