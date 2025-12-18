
```python
"""
Abstract BaseExchange for DEX integration.
Subclass for specific exchanges.
"""

import abc
import json
from typing import Dict, Any, Optional
import websockets
import asyncio

class BaseExchange(abc.ABC):
    def __init__(self, testnet: bool = True, dry_run: bool = False):
        self.testnet = testnet
        self.dry_run = dry_run
        self.ws = None
        self.ws_url = self._get_ws_url()
        self.rest_url = self._get_rest_url()
    
    @abc.abstractmethod
    def _get_ws_url(self) -> str:
        pass
    
    @abc.abstractmethod
    def _get_rest_url(self) -> str:
        pass
    
    async def connect_ws(self):
        self.ws = await websockets.connect(self.ws_url)
    
    async def disconnect_ws(self):
        if self.ws:
            await self.ws.close()
    
    @abc.abstractmethod
    async def subscribe_prices(self, coin: str):
        pass
    
    @abc.abstractmethod
    def parse_price_update(self, data: Dict[str, Any]) -> Optional[float]:
        pass
    
    @abc.abstractmethod
    def fetch_candles(self, coin: str, interval: str) -> 'pd.DataFrame':
        pass
    
    @abc.abstractmethod
    def build_buy_order(self, coin: str, size: float, price: float) -> Dict[str, Any]:
        pass
    
    @abc.abstractmethod
    def build_sell_order(self, coin: str, size: float, price: float) -> Dict[str, Any]:
        pass
    
    @abc.abstractmethod
    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        pass
