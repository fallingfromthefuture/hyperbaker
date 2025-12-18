
```python
"""
Hyperliquid Exchange Implementation.
"""

import requests
import pandas as pd
from .base import BaseExchange
import json

class HyperliquidExchange(BaseExchange):
    def __init__(self, testnet: bool = True, dry_run: bool = False):
        super().__init__(testnet, dry_run)
        self.headers = {"Content-Type": "application/json"}
    
    def _get_ws_url(self) -> str:
        return "wss://api.hyperliquid-testnet.xyz/ws" if self.testnet else "wss://api.hyperliquid.xyz/ws"
    
    def _get_rest_url(self) -> str:
        return "https://api.hyperliquid-testnet.xyz" if self.testnet else "https://api.hyperliquid.xyz"
    
    async def subscribe_prices(self, coin: str):
        sub_msg = json.dumps({
            "method": "subscribe",
            "subscription": {"type": "ticker", "coin": coin}
        })
        await self.ws.send(sub_msg)
    
    def parse_price_update(self, data: Dict[str, Any]) -> float:
        if "channel" in data and data["channel"] == "ticker" and "data" in data:
            return float(data["data"]["midPx"])  # Or px for last price
        return None
    
    def fetch_candles(self, coin: str, interval: str) -> pd.DataFrame:
        if self.dry_run:
            return pd.DataFrame()  # Handled in main
        end = int(time.time() * 1000)
        start = int((time.time() - 3600) * 1000)
        payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "end": end, "start": start}}
        resp = requests.post(f"{self._get_rest_url()}/info", json=payload, headers=self.headers)
        if resp.status_code == 200:
            data = resp.json().get("response", [])
            if data:
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["close"] = pd.to_numeric(df["close"])
                return df
        raise Exception(f"Fetch error: {resp.text}")
    
    def build_buy_order(self, coin: str, size: float, price: float) -> Dict[str, Any]:
        return {
            "type": "order",
            "orders": [{
                "coin": coin,
                "isBuy": True,
                "sz": size,
                "limitPx": round(price * 1.001, 2),
                "orderType": {"limit": {"tif": "Ioc"}}
            }]
        }
    
    def build_sell_order(self, coin: str, size: float, price: float) -> Dict[str, Any]:
        return {
            "type": "order",
            "orders": [{
                "coin": coin,
                "isBuy": False,
                "sz": size,
                "limitPx": round(price * 0.999, 2),
                "orderType": {"limit": {"tif": "Ioc"}}
            }]
        }
    
    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if self.dry_run:
            return {"status": "simulated"}
        resp = requests.post(f"{self._get_rest_url()}/exchange", json={"action": order}, headers=self.headers)
        result = resp.json()
        if "err" in result:
            raise Exception(result["err"])
        return result
