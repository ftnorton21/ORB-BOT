"""
Multi-Timeframe ORB Strategy
Step 1: 9:30-10:00am ET — mark 30-min opening range high and low
Step 2: 10min chart — wait for a 10min candle to CLOSE outside the range
Step 3: 2min chart — add 20 EMA, wait for price to touch it
Step 4: Enter on next 2min candle after EMA touch
Step 5: Stop loss above 30min high (long) or below 30min low (short)
Step 6: Target = low of day (short) or high of day (long)
"""

import pandas as pd
import logging

log = logging.getLogger(__name__)


class MultiTFORBStrategy:
    def __init__(self):
        self.ema_period    = 20
        self.buffer_pct    = 0.001
        self.ema_touch_pct = 0.002

    def calculate_ema(self, prices: list[float], period: int) -> list[float]:
        if len(prices) < period:
            return []
        return pd.Series(prices).ewm(span=period, adjust=False).mean().tolist()

    def check_10min_breakout(self, bars_10min: list[dict], orb_high: float, orb_low: float) -> str | None:
        if not bars_10min:
            return None
        last_bar = bars_10min[-1]
        close    = last_bar["c"]
        buffer   = orb_high * self.buffer_pct
        if close > orb_high + buffer:
            log.info(f"10min candle closed ABOVE range — bullish breakout confirmed (close={close:.4f} ORB high={orb_high:.4f})")
            return "BUY"
        elif close < orb_low - buffer:
            log.info(f"10min candle closed BELOW range — bearish breakout confirmed (close={close:.4f} ORB low={orb_low:.4f})")
            return "SELL"
        return None

    def check_ema_touch(self, bars_2min: list[dict], direction: str) -> bool:
        if len(bars_2min) < self.ema_period + 2:
            return False
        closes     = [b["c"] for b in bars_2min]
        ema_values = self.calculate_ema(closes, self.ema_period)
        if not ema_values:
            return False
        for i in range(-3, 0):
            price     = closes[i]
            ema       = ema_values[i]
            threshold = ema * self.ema_touch_pct
            if direction == "BUY":
                low = bars_2min[i]["l"]
                if low <= ema + threshold and closes[i] >= ema - threshold:
                    log.info(f"2min EMA touch detected (BUY) — price={price:.4f} EMA={ema:.4f}")
                    return True
            else:
                high = bars_2min[i]["h"]
                if high >= ema - threshold and closes[i] <= ema + threshold:
                    log.info(f"2min EMA touch detected (SELL) — price={price:.4f} EMA={ema:.4f}")
                    return True
        return False

    def generate_signal(self, symbol, current_price, orb_high, orb_low,
                        bars_10min, bars_2min, day_high, day_low) -> dict | None:
        direction = self.check_10min_breakout(bars_10min, orb_high, orb_low)
        if not direction:
            return None
        if not self.check_ema_touch(bars_2min, direction):
            log.info(f"{symbol}: 10min breakout confirmed ({direction}) but waiting for 2min EMA touch")
            return None

        entry       = current_price
        stop_loss   = orb_low  if direction == "BUY" else orb_high
        take_profit = day_high if direction == "BUY" else day_low

        risk   = abs(entry - stop_loss)
        reward = abs(entry - take_profit)
        rr     = round(reward / risk, 1) if risk > 0 else 0

        closes     = [b["c"] for b in bars_2min]
        ema_values = self.calculate_ema(closes, self.ema_period)
        current_ema = round(ema_values[-1], 4) if ema_values else None

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
            "reason":      f"{'Bullish' if direction == 'BUY' else 'Bearish'} ORB breakout confirmed on 10min — price pulled back to 20 EMA on 2min",
            "timestamp":   __import__("datetime").datetime.now().isoformat(),
        }
