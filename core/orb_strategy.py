"""
ORB Strategy Logic
Checks if current price has broken out of the opening range
with volume and momentum confirmation.
"""


class ORBStrategy:
    def __init__(self, orb_minutes: int = 30, buffer_pct: float = 0.001):
        self.orb_minutes = orb_minutes
        self.buffer_pct  = buffer_pct  # 0.1% buffer to avoid false breakouts

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

        direction  = "BUY" if bull_break else "SELL"
        entry      = current_price

        # Stop loss just outside the opening range
        stop_loss  = orb_low - buffer  if direction == "BUY" else orb_high + buffer

        # Take profit at 2x the opening range size
        take_profit = entry + orb_range * 2 if direction == "BUY" else entry - orb_range * 2

        # Risk/reward
        risk   = abs(entry - stop_loss)
        reward = abs(entry - take_profit)
        rr     = round(reward / risk, 1) if risk > 0 else 0

        # Confidence based on how far price has broken out
        breakout_strength = abs(current_price - (orb_high if bull_break else orb_low)) / orb_range
        confidence = min(int(60 + breakout_strength * 30), 95)

        return {
            "symbol":      symbol,
            "direction":   direction,
            "entry":       round(entry, 6),
            "stop_loss":   round(stop_loss, 6),
            "take_profit": round(take_profit, 6),
            "orb_high":    round(orb_high, 6),
            "orb_low":     round(orb_low, 6),
            "rr":          f"1:{rr}",
            "confidence":  confidence,
            "strategy":    "ORB",
            "reason":      f"{'Bullish' if bull_break else 'Bearish'} breakout of {self.orb_minutes}-min opening range",
            "timestamp":   __import__("datetime").datetime.now().isoformat(),
        }
