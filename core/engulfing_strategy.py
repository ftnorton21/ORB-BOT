"""
15-Minute ORB Engulfing Strategy
==================================
Step 1: 9:30-9:45am ET — mark the 15-min opening candle wick to wick (full range)
Step 2: 1min chart — wait for price to break OUTSIDE the range
Step 3: Wait for price to RETEST the breakout level (pull back to the level)
Step 4: Look for an ENGULFING candle on 1min rejecting that level
Step 5: Enter on the next 1min candle after the engulf
Step 6: Stop loss at the high of the 15min candle (short) or low (long)
Step 7: Target = opposite side of the 15min range (liquidity)
"""

import logging

log = logging.getLogger(__name__)


class EngulfingORBStrategy:
    def __init__(self):
        self.buffer_pct       = 0.001   # 0.1% buffer for breakout confirmation
        self.retest_pct       = 0.002   # price must come within 0.2% of breakout level
        self.engulf_min_ratio = 0.5     # engulfing candle must cover at least 50% of previous candle

    def check_breakout_1min(
        self,
        bars_1min: list[dict],
        orb_high: float,
        orb_low: float,
    ) -> str | None:
        """
        Check if price has broken outside the 15-min range on the 1-min chart.
        Returns 'BUY', 'SELL', or None.
        """
        if len(bars_1min) < 3:
            return None

        # Look at recent bars for a breakout
        for bar in bars_1min[-10:]:
            close  = bar["c"]
            buffer = orb_high * self.buffer_pct

            if close > orb_high + buffer:
                return "BUY"
            elif close < orb_low - buffer:
                return "SELL"

        return None

    def check_retest(
        self,
        bars_1min: list[dict],
        direction: str,
        orb_high: float,
        orb_low: float,
    ) -> bool:
        """
        After breakout, check if price has pulled back to retest the breakout level.
        BUY: price broke above orb_high, now pulls back close to orb_high
        SELL: price broke below orb_low, now pulls back close to orb_low
        """
        if not bars_1min:
            return False

        retest_level = orb_high if direction == "BUY" else orb_low
        threshold    = retest_level * self.retest_pct

        # Check last 5 bars for a retest
        for bar in bars_1min[-5:]:
            low  = bar["l"]
            high = bar["h"]

            if direction == "BUY":
                # Price dipped back down toward the orb_high level
                if low <= retest_level + threshold:
                    log.info(f"Retest detected (BUY) — price touched {retest_level:.4f}")
                    return True
            else:
                # Price bounced back up toward the orb_low level
                if high >= retest_level - threshold:
                    log.info(f"Retest detected (SELL) — price touched {retest_level:.4f}")
                    return True

        return False

    def check_engulfing(
        self,
        bars_1min: list[dict],
        direction: str,
    ) -> bool:
        """
        Check for an engulfing candle rejecting the retest level.
        BUY: bullish engulfing (green candle engulfs previous red candle)
        SELL: bearish engulfing (red candle engulfs previous green candle)
        """
        if len(bars_1min) < 2:
            return False

        prev = bars_1min[-2]
        curr = bars_1min[-1]

        prev_body = abs(prev["c"] - prev["o"])
        curr_body = abs(curr["c"] - curr["o"])

        if curr_body < prev_body * self.engulf_min_ratio:
            return False  # current candle too small

        if direction == "BUY":
            # Bullish engulfing: prev is red (close < open), curr is green and engulfs it
            prev_bearish = prev["c"] < prev["o"]
            curr_bullish = curr["c"] > curr["o"]
            engulfs      = curr["o"] <= prev["c"] and curr["c"] >= prev["o"]

            if prev_bearish and curr_bullish and engulfs:
                log.info(f"Bullish engulfing detected — curr body={curr_body:.4f} prev body={prev_body:.4f}")
                return True

        else:  # SELL
            # Bearish engulfing: prev is green (close > open), curr is red and engulfs it
            prev_bullish = prev["c"] > prev["o"]
            curr_bearish = curr["c"] < curr["o"]
            engulfs      = curr["o"] >= prev["c"] and curr["c"] <= prev["o"]

            if prev_bullish and curr_bearish and engulfs:
                log.info(f"Bearish engulfing detected — curr body={curr_body:.4f} prev body={prev_body:.4f}")
                return True

        return False

    def generate_signal(
        self,
        symbol: str,
        current_price: float,
        orb_high: float,
        orb_low: float,
        bars_1min: list[dict],
    ) -> dict | None:
        """
        Full 15-min engulfing ORB signal generation.
        Requires: breakout → retest → engulfing confirmation.
        """
        # Step 1 — Check 1min breakout
        direction = self.check_breakout_1min(bars_1min, orb_high, orb_low)
        if not direction:
            return None

        # Step 2 — Check retest of breakout level
        retested = self.check_retest(bars_1min, direction, orb_high, orb_low)
        if not retested:
            log.info(f"{symbol}: 1min breakout ({direction}) confirmed — waiting for retest")
            return None

        # Step 3 — Check engulfing candle rejection
        engulfed = self.check_engulfing(bars_1min, direction)
        if not engulfed:
            log.info(f"{symbol}: Retest confirmed — waiting for engulfing candle")
            return None

        # Step 4 — Build signal
        entry     = current_price
        orb_range = orb_high - orb_low

        if direction == "BUY":
            stop_loss   = orb_low    # stop at 15min candle low
            take_profit = orb_high + orb_range  # target opposite liquidity
        else:
            stop_loss   = orb_high   # stop at 15min candle high
            take_profit = orb_low - orb_range   # target opposite liquidity

        risk   = abs(entry - stop_loss)
        reward = abs(entry - take_profit)
        rr     = round(reward / risk, 1) if risk > 0 else 0

        # Confidence
        confidence = min(75 + int(rr * 3), 95)

        return {
            "symbol":      symbol,
            "direction":   direction,
            "entry":       round(entry, 4),
            "stop_loss":   round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "orb_high":    round(orb_high, 4),
            "orb_low":     round(orb_low, 4),
            "rr":          f"1:{rr}",
            "confidence":  confidence,
            "strategy":    "15min Engulfing ORB",
            "reason":      (
                f"{'Bullish' if direction == 'BUY' else 'Bearish'} 15-min ORB — "
                f"1min breakout → retest → engulfing rejection confirmed"
            ),
            "timestamp":   __import__("datetime").datetime.now().isoformat(),
        }
