"""
ORB Strategy Logic
Checks if current price has broken out of the opening range.
Uses trailing stop instead of fixed take profit so big moves get captured.
"""


class ORBStrategy:
    def __init__(self, orb_minutes: int = 30, buffer_pct: float = 0.001):
        self.orb_minutes    = orb_minutes
        self.buffer_pct     = buffer_pct      # 0.1% buffer to avoid false breakouts
        self.stop_loss_pct  = 0.01            # 1% stop loss
        self.trail_pct      = 0.01            # 1% trailing stop distance

    def check_breakout(
        self,
        symbol: str,
        current_price: float,
        orb_high: float,
        orb_low: float,
    ) -> dict | None:

        orb_range  = orb_high - orb_low
        if orb_range <= 0:
            return None

        buffer     = orb_high * self.buffer_pct
        bull_break = current_price > orb_high + buffer
        bear_break = current_price < orb_low - buffer

        if not bull_break and not bear_break:
            return None

        direction = "BUY" if bull_break else "SELL"
        entry     = current_price

        # Fixed stop loss at 1% from entry
        stop_distance = entry * self.stop_loss_pct
        stop_loss     = entry - stop_distance if direction == "BUY" else entry + stop_distance

        # Trailing stop — 1% trail distance, activates immediately
        # No fixed take profit — lets winners run as far as the move goes
        trail_price = entry * self.trail_pct  # trail amount in dollars

        # Confidence based on breakout strength
        breakout_strength = abs(current_price - (orb_high if bull_break else orb_low)) / orb_range
        confidence = min(int(60 + breakout_strength * 30), 95)

        return {
            "symbol":        symbol,
            "direction":     direction,
            "entry":         round(entry, 6),
            "stop_loss":     round(stop_loss, 6),
            "trail_price":   round(trail_price, 6),   # trailing stop distance
            "orb_high":      round(orb_high, 6),
            "orb_low":       round(orb_low, 6),
            "orb_range":     round(orb_range, 6),
            "rr":            "Trailing (1% trail)",
            "confidence":    confidence,
            "strategy":      "ORB",
            "reason":        f"{'Bullish' if bull_break else 'Bearish'} breakout of {self.orb_minutes}-min opening range",
            "timestamp":     __import__("datetime").datetime.now().isoformat(),
        }
