"""
Multi-Timeframe ORB Strategy
=============================
Step 1: 9:30-10:00am ET — mark 30-min opening range high and low
Step 2: 10min chart — wait for a 10min candle to CLOSE outside the range
Step 3: 2min chart — add 20 EMA, wait for price to touch/interact with it
Step 4: Enter on next 2min candle after EMA touch
Step 5: Stop loss above 30min high (long) or below 30min low (short)
Step 6: Target = low of day (short) or high of day (long)

Works on both stocks and crypto, both long and short.
"""

import pandas as pd
import logging

log = logging.getLogger(__name__)


class MultiTFORBStrategy:
    def __init__(self):
        self.ema_period      = 20
        self.buffer_pct      = 0.001   # 0.1% buffer beyond range to confirm breakout
        self.ema_touch_pct   = 0.002   # price within 0.2% of EMA counts as a touch

    def calculate_ema(self, prices: list[float], period: int) -> list[float]:
        """Calculate EMA for a list of prices."""
        if len(prices) < period:
            return []
        series = pd.Series(prices)
        ema = series.ewm(span=period, adjust=False).mean()
        return ema.tolist()

    def check_10min_breakout(
        self,
        bars_10min: list[dict],
        orb_high: float,
        orb_low: float,
    ) -> str | None:
        """
        Check if the most recent 10min candle closed OUTSIDE the opening range.
        Returns 'BUY', 'SELL', or None.
        """
        if not bars_10min:
            return None

        last_bar = bars_10min[-1]
        close    = last_bar["c"]
        buffer   = orb_high * self.buffer_pct

        # Candle must CLOSE outside the range (not just wick)
        if close > orb_high + buffer:
            log.info(f"10min candle closed ABOVE range — bullish breakout confirmed (close={close:.4f} ORB high={orb_high:.4f})")
            return "BUY"
        elif close < orb_low - buffer:
            log.info(f"10min candle closed BELOW range — bearish breakout confirmed (close={close:.4f} ORB low={orb_low:.4f})")
            return "SELL"

        return None

    def check_ema_touch(
        self,
        bars_2min: list[dict],
        direction: str,
    ) -> bool:
        """
        On the 2min chart, check if price has touched or interacted with the 20 EMA.
        For BUY: price dips down to touch EMA from above (pullback)
        For SELL: price bounces up to touch EMA from below (pullback)
        """
        if len(bars_2min) < self.ema_period + 2:
            return False

        closes = [b["c"] for b in bars_2min]
        ema_values = self.calculate_ema(closes, self.ema_period)

        if not ema_values:
            return False

        # Check last 3 candles for EMA interaction
        for i in range(-3, 0):
            price = closes[i]
            ema   = ema_values[i]
            touch_threshold = ema * self.ema_touch_pct

            if direction == "BUY":
                # Price came down to touch EMA from above
                low = bars_2min[i]["l"]
                if low <= ema + touch_threshold and closes[i] >= ema - touch_threshold:
                    log.info(f"2min EMA touch detected (BUY) — price={price:.4f} EMA={ema:.4f}")
                    return True
            else:  # SELL
                # Price bounced up to touch EMA from below
                high = bars_2min[i]["h"]
                if high >= ema - touch_threshold and closes[i] <= ema + touch_threshold:
                    log.info(f"2min EMA touch detected (SELL) — price={price:.4f} EMA={ema:.4f}")
                    return True

        return False

    def generate_signal(
        self,
        symbol: str,
        current_price: float,
        orb_high: float,
        orb_low: float,
        bars_10min: list[dict],
        bars_2min: list[dict],
        day_high: float,
        day_low: float,
    ) -> dict | None:
        """
        Full multi-timeframe signal generation.
        Returns signal dict or None.
        """
        # Step 1 — Check 10min breakout confirmation
        direction = self.check_10min_breakout(bars_10min, orb_high, orb_low)
        if not direction:
            return None

        # Step 2 — Check 2min EMA touch
        ema_touched = self.check_ema_touch(bars_2min, direction)
        if not ema_touched:
            log.info(f"{symbol}: 10min breakout confirmed ({direction}) but waiting for 2min EMA touch")
            return None

        # Step 3 — Calculate entry, stop, target
        entry = current_price

        if direction == "BUY":
            stop_loss   = orb_low   # stop below 30min low
            take_profit = day_high  # target high of day
        else:  # SELL
            stop_loss   = orb_high  # stop above 30min high
            take_profit = day_low   # target low of day

        # Risk/reward
        risk   = abs(entry - stop_loss)
        reward = abs(entry - take_profit)
        rr     = round(reward / risk, 1) if risk > 0 else 0

        # 2min EMA values for signal info
        closes     = [b["c"] for b in bars_2min]
        ema_values = self.calculate_ema(closes, self.ema_period)
        current_ema = round(ema_values[-1], 4) if ema_values else None

        # Confidence — based on breakout strength and R:R
        breakout_dist = abs(current_price - (orb_high if direction == "BUY" else orb_low))
        orb_range     = orb_high - orb_low
        strength      = breakout_dist / orb_range if orb_range > 0 else 0
        confidence    = min(int(65 + strength * 20 + min(rr * 3, 15)), 95)

        return {
            "symbol":      symbol,
            "direction":   direction,
            "entry":       round(entry, 4),
            "stop_loss":   round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "orb_high":    round(orb_high, 4),
            "orb_low":     round(orb_low, 4),
            "ema_20":      current_ema,
            "day_high":    round(day_high, 4),
            "day_low":     round(day_low, 4),
            "rr":          f"1:{rr}",
            "confidence":  confidence,
            "strategy":    "Multi-TF ORB",
            "reason":      (
                f"{'Bullish' if direction == 'BUY' else 'Bearish'} ORB breakout confirmed on 10min — "
                f"price pulled back to 20 EMA on 2min — "
                f"entry on next candle"
            ),
            "timestamp":   __import__("datetime").datetime.now().isoformat(),
        }
