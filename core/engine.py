"""
Multi-Strategy ORB Bot Engine
Runs two strategies simultaneously:
1. Multi-TF ORB: 30min range → 10min confirm → 2min EMA entry
2. 15min Engulfing ORB: 15min range → 1min breakout → retest → engulfing entry
GitHub Actions runs every 5 minutes during US market hours Mon-Fri.
"""

import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv
load_dotenv()

from core.orb_strategy import MultiTFORBStrategy
from core.engulfing_strategy import EngulfingORBStrategy
from core.alpaca_client import AlpacaClient
from signals.discord_sender import DiscordSender

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_URL", "")
FIXED_TRADE_SIZE_USD = 1000
EOD_CLOSE_TIME       = time(15, 45)

WATCHLIST = {
    "stocks": ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AMD", "META", "MSFT", "AAPL", "AMZN"],
    "crypto": ["BTC/USD", "ETH/USD"],
}


class ORBEngine:
    def __init__(self):
        self.multitf_strategy   = MultiTFORBStrategy()
        self.engulfing_strategy = EngulfingORBStrategy()
        self.alpaca             = AlpacaClient()
        self.discord            = DiscordSender(DISCORD_WEBHOOK_URL)

    def _now_et(self):
        return datetime.now(ET)

    def _is_market_open(self):
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)

    def _is_orb_window_30(self):
        return time(9, 30) <= self._now_et().time() < time(10, 0)

    def _is_eod(self):
        return self._now_et().time() >= EOD_CLOSE_TIME

    async def run(self):
        now_et = self._now_et()
        log.info(f"ORB Bot triggered - ET: {now_et.strftime('%A %I:%M %p')}")

        if not self._is_market_open():
            log.info("Market closed — nothing to do")
            return

        if self._is_eod():
            log.info("EOD — closing all positions")
            await self._close_all_positions()
            return

        if self._is_orb_window_30():
            log.info("Building opening ranges (9:30-10:00am ET) — no trades yet")
            return

        log.info("Scanning both strategies...")
        open_positions = await self.alpaca.get_open_positions()
        all_symbols    = WATCHLIST["stocks"] + WATCHLIST["crypto"]

        for symbol in all_symbols:
            try:
                is_crypto  = "/" in symbol
                alpaca_sym = symbol.replace("/", "") if is_crypto else symbol

                if symbol in open_positions or alpaca_sym in open_positions:
                    log.info(f"Skipping {symbol} — position already open")
                    continue

                current_price = await self.alpaca.get_latest_price(symbol)
                if not current_price:
                    continue

                signal = None

                # Multi-TF ORB after 10:00am
                if now_et.time() >= time(10, 0):
                    signal = await self._run_multitf(symbol, current_price)

                # 15min Engulfing after 9:45am
                if not signal and now_et.time() >= time(9, 45):
                    signal = await self._run_engulfing(symbol, current_price)

                if signal:
                    # Skip crypto shorts
                    if is_crypto and signal["direction"] == "SELL":
                        log.info(f"Skipping SELL on {symbol} — crypto long only")
                        continue
                    await self._execute(signal, symbol)

            except Exception as e:
                log.error(f"Error processing {symbol}: {e}")

        log.info("Scan complete")

    async def _run_multitf(self, symbol, price):
        bars_30 = await self.alpaca.get_bars(symbol, "30Min", 10)
        bars_10 = await self.alpaca.get_bars(symbol, "10Min", 20)
        bars_2  = await self.alpaca.get_bars(symbol, "2Min",  60)
        if not bars_30 or not bars_10 or not bars_2:
            return None
        return self.multitf_strategy.generate_signal(
            symbol=symbol, current_price=price,
            orb_high=bars_30[0]["h"], orb_low=bars_30[0]["l"],
            bars_10min=bars_10, bars_2min=bars_2,
            day_high=max(b["h"] for b in bars_30),
            day_low=min(b["l"] for b in bars_30),
        )

    async def _run_engulfing(self, symbol, price):
        bars_15 = await self.alpaca.get_bars(symbol, "15Min", 10)
        bars_1  = await self.alpaca.get_bars(symbol, "1Min",  60)
        if not bars_15 or not bars_1:
            return None
        return self.engulfing_strategy.generate_signal(
            symbol=symbol, current_price=price,
            orb_high=bars_15[0]["h"], orb_low=bars_15[0]["l"],
            bars_1min=bars_1,
        )

    async def _execute(self, signal, symbol):
        order = await self.alpaca.place_order(
            symbol=symbol,
            side="buy" if signal["direction"] == "BUY" else "sell",
            trade_size_usd=FIXED_TRADE_SIZE_USD,
            entry_price=signal["entry"],
            signal=signal,
        )
        if order:
            log.info(f"Trade placed: {symbol} {signal['direction']} ({signal['strategy']})")
            await self.discord.send(signal)
        else:
            log.error(f"Trade failed: {symbol} ({signal['strategy']})")

    async def _close_all_positions(self):
        positions = await self.alpaca.get_open_positions()
        if not positions:
            log.info("No open positions to close")
            return
        for symbol in positions:
            if await self.alpaca.close_position(symbol):
                await self.discord.send_alert(f"EOD close: {symbol}")


if __name__ == "__main__":
    engine = ORBEngine()
    asyncio.run(engine.run())
