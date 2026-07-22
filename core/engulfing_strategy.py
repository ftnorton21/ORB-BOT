"""
15-Minute ORB Engulfing Strategy
Step 1: 9:30-9:45am ET — mark the 15-min opening candle wick to wick
Step 2: 1min chart — wait for price to break OUTSIDE the range
Step 3: Wait for price to RETEST the breakout level
Step 4: Look for an ENGULFING candle rejecting that level
Step 5: Enter on next 1min candle after engulf
Step 6: Stop loss at 15min high (short) or low (long)
Step 7: Target = opposite side of the 15min range
"""

import logging

log = logging.getLogger(__name__)


class EngulfingORBStrategy:
    def __init__(self):
        self.buffer_pct       = 0.001
        self.retest_pct       = 0.002
        self.engulf_min_ratio = 0.5

    def check_breakout_1min(self, bars_1min, orb_high, orb_low) -> str | None:
        if len(bars_1min) < 3:
            return None
        for bar in bars_1min[-10:]:
            close  = bar["c"]
            buffer = orb_high * self.buffer_pct
            if close > orb_high + buffer:
                return "BUY"
            elif close < orb_low - buffer:
                return "SELL"
        return None

    def check_retest(self, bars_1min, direction, orb_high, orb_low) -> bool:
        if not bars_1min:
            return False
        retest_level = orb_high if direction == "BUY" else orb_low
        threshold    = retest_level * self.retest_pct
        for bar in bars_1min[-5:]:
            if direction == "BUY":
                if bar["l"] <= retest_level + threshold:
                    log.info(f"Retest detected (BUY) — price touched {retest_level:.4f}")
                    return True
            else:
                if bar["h"] >= retest_level - threshold:
                    log.info(f"Retest detected (SELL) — price touched {retest_level:.4f}")
                    return True
        return False

    def check_engulfing(self, bars_1min, direction) -> bool:
        if len(bars_1min) < 2:
            return False
        prev      = bars_1min[-2]
        curr      = bars_1min[-1]
        prev_body = abs(prev["c"] - prev["o"])
        curr_body = abs(curr["c"] - curr["o"])
        if curr_body < prev_body * self.engulf_min_ratio:
            return False
        if direction == "BUY":
            if prev["c"] < prev["o"] and curr["c"] > curr["o"] and curr["o"] <= prev["c"] and curr["c"] >= prev["o"]:
                log.info(f"Bullish engulfing detected")
                return True
        else:
            if prev["c"] > prev["o"] and curr["c"] < curr["o"] and curr["o"] >= prev["c"] and curr["c"] <= prev["o"]:
                log.info(f"Bearish engulfing detected")
                return True
        return False

    def generate_signal(self, symbol, current_price, orb_high, orb_low, bars_1min) -> dict | None:
        direction = self.check_breakout_1min(bars_1min, orb_high, orb_low)
        if not direction:
            return None
        if not self.check_retest(bars_1min, direction, orb_high, orb_low):
            log.info(f"{symbol}: 1min breakout ({direction}) confirmed — waiting for retest")
            return None
        if not self.check_engulfing(bars_1min, direction):
            log.info(f"{symbol}: Retest confirmed — waiting for engulfing candle")
            return None

        entry     = current_price
        orb_range = orb_high - orb_low
        stop_loss   = orb_low  if direction == "BUY" else orb_high
        take_profit = orb_high + orb_range if direction == "BUY" else orb_low - orb_range

        risk       = abs(entry - stop_loss)
        reward     = abs(entry - take_profit)
        rr         = round(reward / risk, 1) if risk > 0 else 0
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
            "reason":      f"{'Bullish' if direction == 'BUY' else 'Bearish'} 15-min ORB — 1min breakout → retest → engulfing rejection confirmed",
            "timestamp":   __import__("datetime").datetime.now().isoformat(),
        }
