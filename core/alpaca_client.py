"""
Alpaca Client - Fixed fractional order issue
Uses qty (whole shares) for stocks with stop loss orders
"""

import aiohttp
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_BASE = "https://paper-api.alpaca.markets/v2"
DATA_BASE  = "https://data.alpaca.markets"
ET         = ZoneInfo("America/New_York")

MIN_ORDER_USD = 10
CRYPTO_SYMBOLS = {"BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD"}

TF_MAP = {
    "2Min": "2Min", "5Min": "5Min", "10Min": "10Min",
    "15Min": "15Min", "30Min": "30Min", "1Min": "1Min",
}


class AlpacaClient:
    def __init__(self):
        self.api_key    = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.enabled    = bool(self.api_key and self.secret_key)
        if not self.enabled:
            log.warning("Alpaca not configured - no API keys found")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type":        "application/json",
        }

    def _is_crypto(self, symbol: str) -> bool:
        return symbol in CRYPTO_SYMBOLS or "/" in symbol

    async def get_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 30) -> list[dict]:
        try:
            is_crypto = self._is_crypto(symbol)
            now_et    = datetime.now(ET)
            start     = now_et.replace(hour=9, minute=25, second=0, microsecond=0)
            start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")

            if is_crypto:
                url    = f"{DATA_BASE}/v1beta3/crypto/us/bars"
                params = {
                    "symbols":   symbol,
                    "timeframe": timeframe,
                    "start":     start_str,
                    "limit":     limit,
                    "sort":      "asc",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=self._headers(), params=params) as resp:
                        if resp.status != 200:
                            log.warning(f"Bars fetch failed for {symbol}: HTTP {resp.status}")
                            return []
                        data = await resp.json()
                bars_raw = data.get("bars", {}).get(symbol, data.get("bars", {}).get(symbol.replace("/", ""), []))
            else:
                url    = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
                params = {
                    "timeframe": timeframe,
                    "start":     start_str,
                    "limit":     limit,
                    "feed":      "iex",
                    "sort":      "asc",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=self._headers(), params=params) as resp:
                        if resp.status != 200:
                            log.warning(f"Bars fetch failed for {symbol}: HTTP {resp.status}")
                            return []
                        data = await resp.json()
                bars_raw = data.get("bars", [])

            return [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}
                    for b in bars_raw]

        except Exception as e:
            log.error(f"get_bars error for {symbol}: {e}")
            return []

    async def get_latest_price(self, symbol: str) -> float | None:
        try:
            is_crypto = self._is_crypto(symbol)
            if is_crypto:
                alpaca_sym = symbol.replace("/", "")
                url    = f"{DATA_BASE}/v1beta3/crypto/us/latest/trades"
                params = {"symbols": alpaca_sym}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=self._headers(), params=params) as resp:
                        if resp.status != 200:
                            return None
                        data   = await resp.json()
                        trades = data.get("trades", {})
                        trade  = trades.get(alpaca_sym)
                        return float(trade["p"]) if trade else None
            else:
                url = f"{DATA_BASE}/v2/stocks/{symbol}/trades/latest"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=self._headers(), params={"feed": "iex"}) as resp:
                        if resp.status != 200:
                            return None
                        data  = await resp.json()
                        trade = data.get("trade")
                        return float(trade["p"]) if trade else None
        except Exception as e:
            log.error(f"get_latest_price error for {symbol}: {e}")
            return None

    async def place_order(self, symbol: str, side: str, trade_size_usd: float,
                          entry_price: float, signal: dict = None) -> dict | None:
        if not self.enabled:
            return None

        is_crypto  = self._is_crypto(symbol)
        alpaca_sym = symbol.replace("/", "") if is_crypto else symbol
        stop_loss  = signal.get("stop_loss") if signal else None

        if is_crypto:
            # Crypto: fractional qty, simple market order
            qty = round(trade_size_usd / entry_price, 6)
            if qty * entry_price < MIN_ORDER_USD:
                log.warning(f"Order too small for {symbol}")
                return None
            payload = {
                "symbol":        alpaca_sym,
                "side":          side,
                "type":          "market",
                "time_in_force": "gtc",
                "qty":           str(qty),
            }
        else:
            # Stocks: whole shares only with oto stop loss
            qty = max(1, int(trade_size_usd / entry_price))
            if qty * entry_price < MIN_ORDER_USD:
                log.warning(f"Order too small for {symbol}")
                return None

            if stop_loss:
                payload = {
                    "symbol":        alpaca_sym,
                    "side":          side,
                    "type":          "market",
                    "time_in_force": "day",
                    "order_class":   "oto",
                    "qty":           str(qty),  # whole shares — fixes fractional error
                    "stop_loss":     {"stop_price": str(round(stop_loss, 2))},
                }
            else:
                payload = {
                    "symbol":        alpaca_sym,
                    "side":          side,
                    "type":          "market",
                    "time_in_force": "day",
                    "qty":           str(qty),
                }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{PAPER_BASE}/orders",
                    headers=self._headers(),
                    json=payload
                ) as resp:
                    data = await resp.json()
                    if resp.status in (200, 201):
                        log.info(f"Order placed: {alpaca_sym} {side} {qty} shares @ ~${entry_price:.2f}")
                        return data
                    log.error(f"Order failed ({resp.status}): {data}")
                    return None
        except Exception as e:
            log.error(f"place_order error for {symbol}: {e}")
            return None

    async def get_open_positions(self) -> list[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{PAPER_BASE}/positions", headers=self._headers()) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return [pos["symbol"] for pos in data]
        except Exception as e:
            log.error(f"get_open_positions error: {e}")
            return []

    async def close_position(self, symbol: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{PAPER_BASE}/positions/{symbol}",
                    headers=self._headers()
                ) as resp:
                    if resp.status in (200, 204, 207):
                        log.info(f"Position closed: {symbol}")
                        return True
                    text = await resp.text()
                    log.error(f"Close failed for {symbol}: {resp.status} {text[:100]}")
                    return False
        except Exception as e:
            log.error(f"close_position error for {symbol}: {e}")
            return False
