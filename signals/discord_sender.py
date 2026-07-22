"""
Discord Signal Sender for Multi-TF ORB Bot
"""

import aiohttp
import logging
from datetime import datetime

log = logging.getLogger(__name__)

COLOURS = {"BUY": 0x00C853, "SELL": 0xFF1744}
EMOJIS  = {"BUY": "🟢", "SELL": "🔴"}


class DiscordSender:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, signal: dict):
        if not self.webhook_url or "PASTE" in self.webhook_url:
            log.warning("Discord webhook not configured")
            return

        direction  = signal.get("direction", "BUY")
        symbol     = signal.get("symbol", "???")
        entry      = signal.get("entry", "—")
        sl         = signal.get("stop_loss", "—")
        tp         = signal.get("take_profit", "—")
        orb_high   = signal.get("orb_high", "—")
        orb_low    = signal.get("orb_low", "—")
        ema_20     = signal.get("ema_20", "—")
        rr         = signal.get("rr", "—")
        confidence = signal.get("confidence", 0)
        reason     = signal.get("reason", "")
        strategy   = signal.get("strategy", "ORB")
        conf_bar   = "█" * round(confidence / 10) + "░" * (10 - round(confidence / 10))

        embed = {
            "title":       f"{EMOJIS.get(direction, '⚪')} {strategy} {direction} — {symbol}",
            "description": reason,
            "color":       COLOURS.get(direction, 0x888888),
            "fields": [
                {"name": "Entry",       "value": f"`{entry}`",    "inline": True},
                {"name": "Stop Loss",   "value": f"`{sl}`",       "inline": True},
                {"name": "Target",      "value": f"`{tp}`",       "inline": True},
                {"name": "ORB High",    "value": f"`{orb_high}`", "inline": True},
                {"name": "ORB Low",     "value": f"`{orb_low}`",  "inline": True},
                {"name": "20 EMA",      "value": f"`{ema_20}`",   "inline": True},
                {"name": "Risk/Reward", "value": f"`{rr}`",       "inline": True},
                {"name": "Confidence",  "value": f"{conf_bar} **{confidence}%**", "inline": False},
            ],
            "footer": {"text": f"Finn's ORB Bot • {datetime.now().strftime('%Y-%m-%d %H:%M')} NZST"},
            "timestamp": datetime.utcnow().isoformat()
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json={"embeds": [embed]}) as resp:
                    if resp.status not in (200, 204):
                        log.error(f"Discord send failed: HTTP {resp.status}")
                    else:
                        log.info(f"Discord signal sent: {symbol} {direction}")
        except Exception as e:
            log.error(f"Discord send error: {e}")

    async def send_alert(self, message: str):
        if not self.webhook_url or "PASTE" in self.webhook_url:
            return
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(self.webhook_url, json={"content": f"ORB Bot: {message}"})
        except Exception as e:
            log.error(f"Discord alert error: {e}")
