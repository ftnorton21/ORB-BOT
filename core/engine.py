"""
Multi-Strategy ORB Bot Engine
================================
Runs two strategies simultaneously:

1. Multi-TF ORB: 30min range → 10min breakout confirm → 2min EMA touch entry
2. 15min Engulfing ORB: 15min range → 1min breakout → retest → engulfing entry

GitHub Actions runs this every 5 minutes during US market hours.
"""

import asyncio
import logging
from datetime import datetime, time, date
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv
load_dotenv()

from core.orb_strategy import MultiTFORBStrategy
from core.engulfing_strategy import EngulfingORBStrategy
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
        self.multitf_strategy   = MultiTFORBStrategy()
        self.engulfing_strategy = EngulfingORBStrategy()
        self.alpaca             = AlpacaClient()
        self.discord            = DiscordSender(DISCORD_WEBHOOK_URL)

    def _now_et(self) -> datetime:
        return datetime.now(ET)

    def _is_market_open(self) -> bool:
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)

    def _is_orb_window_30(self) -> bool:
        now = self._now_et()
        return time(9, 30) <= now.time() < time(10, 0)

    def _is_orb_window_15(self) -> bool:
        now = self._now_et()
        return time(9, 30) <= now.time() < time(9, 45)

    def _is_eod(self) -> bool:
        return self._now_et().time() >= EOD_CLOSE_TIME

    def _is_crypto(self, symbol: str) -> bool:
        return "/" in symbol

    async def run(self):
        now_et = self._now_et()
        log.info(f"ORB Bot triggered - ET: {now_et.strftime('%A %I:%M %p')}")

        if not self._is_market_open():
            log.info("Market closed — nothing to do")
            return

        if self._is_eod():
            log.info("EOD — closing all open positions")
            await self._close_all_positions()
            return

        if self._is_orb_window_30():
            log.info("Building 30-min and 15-min opening ranges — no trades yet")
            return

        log.info("Scanning both strategies...")
        open_positions = await self.alpaca.get_open_positions()
        all_symbols    = WATCHLIST["crypto"] + WATCHLIST["stocks"]

        for symbol in all_symbols:
            try:
                alpaca_sym = symbol.replace("/", "") if self._is_crypto(symbol) else symbol
                if symbol in open_positions or alpaca_sym in open_positions:
                    log.info(f"Skipping {symbol} — position already open")
                    continue

                current_price = await self.alpaca.get_latest_price(symbol)
                if not current_price:
                    continue

                # ── Strategy 1: Multi-TF ORB (after 10:00am only) ─────────────
                if self._now_et().time() >= time(10, 0):
                    signal = await self._run_multitf(symbol, current_price)
                    if signal:
                        await self._execute(signal, symbol)
                        continue  # don't run second strategy if first already fired

                # ── Strategy 2: 15min Engulfing ORB (after 9:45am) ────────────
                if self._now_et().time() >= time(9, 45):
                    signal = await self._run_engulfing(symbol, current_price)
                    if signal:
                        await self._execute(signal, symbol)

            except Exception as e:
                log.error(f"Error processing {symbol}: {e}")

        log.info("Scan complete")

    async def _run_multitf(self, symbol: str, current_price: float) -> dict | None:
        bars_30min = await self.alpaca.get_bars(symbol, timeframe="30Min", limit=10)
        bars_10min = await self.alpaca.get_bars(symbol, timeframe="10Min", limit=20)
        bars_2min  = await self.alpaca.get_bars(symbol, timeframe="2Min", limit=60)

        if not bars_30min or not bars_10min or not bars_2min:
            return None

        orb_bar  = bars_30min[0]
        orb_high = orb_bar["h"]
        orb_low  = orb_bar["l"]
        day_high = max(b["h"] for b in bars_30min)
        day_low  = min(b["l"] for b in bars_30min)

        return self.multitf_strategy.generate_signal(
            symbol=symbol,
            current_price=current_price,
            orb_high=orb_high,
            orb_low=orb_low,
            bars_10min=bars_10min,
            bars_2min=bars_2min,
            day_high=day_high,
            day_low=day_low,
        )

    async def _run_engulfing(self, symbol: str, current_price: float) -> dict | None:
        # 15min range = first 15min candle (1 bar of 15Min timeframe)
        bars_15min = await self.alpaca.get_bars(symbol, timeframe="15Min", limit=10)
        bars_1min  = await self.alpaca.get_bars(symbol, timeframe="1Min", limit=60)

        if not bars_15min or not bars_1min:
            return None

        orb_bar  = bars_15min[0]  # first 15min candle = opening range
        orb_high = orb_bar["h"]   # wick to wick
        orb_low  = orb_bar["l"]

        return self.engulfing_strategy.generate_signal(
            symbol=symbol,
            current_price=current_price,
            orb_high=orb_high,
            orb_low=orb_low,
            bars_1min=bars_1min,
        )

    async def _execute(self, signal: dict, symbol: str):
        # Skip crypto shorts
        if self._is_crypto(symbol) and signal["direction"] == "SELL":
            log.info(f"Skipping SELL on {symbol} — crypto long only")
            return

        order = await self.alpaca.place_order(
            symbol=symbol,
            side="buy" if signal["direction"] == "BUY" else "sell",
            trade_size_usd=FIXED_TRADE_SIZE_USD,
            entry_price=signal["entry"],
            signal=signal,
        )

        if order:
            log.info(f"Trade placed: {symbol} {signal['direction']} @ {signal['entry']} ({signal['strategy']})")
            await self.discord.send(signal)
        else:
            log.error(f"Trade failed: {symbol} ({signal['strategy']})")

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
