"""
Alpaca Client
Handles market data and order execution for both crypto and stocks.
Uses Alpaca paper trading API.
"""

import aiohttp
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_BASE  = "https://paper-api.alpaca.markets/v2"
DATA_BASE   = "https://data.alpaca.markets"
ET          = ZoneInfo("America/New_York")

FIXED_TRADE_SIZE_USD = 1000
MIN_ORDER_USD        = 10

# Crypto symbols use /USD format on Alpaca
CRYPTO_SYMBOLS = {"BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD",
                  "AVAX/USD", "ADA/USD", "LINK/USD", "DOT/USD"}


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
        """Get OHLCV bars for a symbol."""
        try:
            is_crypto = self._is_crypto(symbol)
            now_et    = datetime.now(ET)
            start     = (now_et - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

            if is_crypto:
                alpaca_sym = symbol.replace("/", "")  # BTC/USD -> BTCUSD
                url = f"{DATA_BASE}/v1beta3/crypto/us/bars"
                params = {
                    "symbols":    alpaca_sym,
                    "timeframe":  timeframe,
                    "start":      start,
                    "limit":      limit,
                    "sort":       "asc",
                }
            else:
                url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
                params = {
                    "timeframe": timeframe,
                    "start":     start,
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

            if is_crypto:
                alpaca_sym = symbol.replace("/", "")
                bars_raw   = data.get("bars", {}).get(alpaca_sym, [])
            else:
                bars_raw = data.get("bars", [])

            return [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}
                    for b in bars_raw]

        except Exception as e:
            log.error(f"get_bars error for {symbol}: {e}")
            return []

    async def get_latest_price(self, symbol: str) -> float | None:
        """Get current price for a symbol."""
        try:
            is_crypto  = self._is_crypto(symbol)

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

    async def place_order(self, symbol: str, side: str, trade_size_usd: float, entry_price: float, signal: dict = None) -> dict | None:
        """Place a market order."""
        if not self.enabled:
            return None

        is_crypto  = self._is_crypto(symbol)
        alpaca_sym = symbol.replace("/", "") if is_crypto else symbol

        # Calculate quantity
        qty = round(trade_size_usd / entry_price, 6) if is_crypto else max(1, int(trade_size_usd / entry_price))

        if qty * entry_price < MIN_ORDER_USD:
            log.warning(f"Order too small for {symbol}")
            return None

        stop_loss   = signal.get("stop_loss") if signal else None
        trail_price = signal.get("trail_price") if signal else None

        if not is_crypto and stop_loss and trail_price:
            # Stocks: use bracket with stop loss + trailing stop (no fixed TP)
            payload = {
                "symbol":        alpaca_sym,
                "side":          side,
                "type":          "market",
                "time_in_force": "day",
                "order_class":   "oto",
                "stop_loss":     {"stop_price": str(round(stop_loss, 2))},
            }
        else:
            # Crypto: simple market order (no bracket support)
            payload = {
                "symbol":        alpaca_sym,
                "side":          side,
                "type":          "market",
                "time_in_force": "day",
            }

        # Use notional for stocks, qty for crypto
        if is_crypto:
            payload["qty"] = str(qty)
        else:
            payload["notional"] = str(trade_size_usd)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{PAPER_BASE}/orders", headers=self._headers(), json=payload) as resp:
                    data = await resp.json()
                    if resp.status in (200, 201):
                        log.info(f"Order placed: {alpaca_sym} {side} ${trade_size_usd}")
                        return data
                    log.error(f"Order failed ({resp.status}): {data}")
                    return None
        except Exception as e:
            log.error(f"place_order error for {symbol}: {e}")
            return None

    async def get_open_positions(self) -> list[str]:
        """Returns list of open position symbols."""
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
        """Close an open position."""
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

    async def get_account(self) -> dict | None:
        """Get account info."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{PAPER_BASE}/account", headers=self._headers()) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception as e:
            log.error(f"get_account error: {e}")
            return None
