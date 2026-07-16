"""
Discord Signal Sender for ORB Bot
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

        direction   = signal.get("direction", "BUY")
        symbol      = signal.get("symbol", "???")
        entry       = signal.get("entry", "—")
        sl          = signal.get("stop_loss", "—")
        trail       = signal.get("trail_price", "—")
        orb_high    = signal.get("orb_high", "—")
        orb_low     = signal.get("orb_low", "—")
        orb_range   = signal.get("orb_range", "—")
        confidence  = signal.get("confidence", 0)
        reason      = signal.get("reason", "")
        conf_bar    = "█" * round(confidence / 10) + "░" * (10 - round(confidence / 10))

        embed = {
            "title":       f"{EMOJIS.get(direction, '⚪')} ORB {direction} — {symbol}",
            "description": reason,
            "color":       COLOURS.get(direction, 0x888888),
            "fields": [
                {"name": "Entry",          "value": f"`{entry}`",     "inline": True},
                {"name": "Stop Loss",      "value": f"`{sl}`",        "inline": True},
                {"name": "Trailing Stop",  "value": f"`{trail}`",     "inline": True},
                {"name": "ORB High",       "value": f"`{orb_high}`",  "inline": True},
                {"name": "ORB Low",        "value": f"`{orb_low}`",   "inline": True},
                {"name": "ORB Range",      "value": f"`{orb_range}`", "inline": True},
                {"name": "Confidence",     "value": f"{conf_bar} **{confidence}%**", "inline": False},
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
