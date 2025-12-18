
## File: main.py
```python
#!/usr/bin/env python3
"""
Multi-DEX Trading Bot: DeepSeek + Grok Tandem with WS.
Supports Hyperliquid, dYdX; WS for live prices.
Config: ACTIVE_EXCHANGE in .env.
"""

import asyncio
import json
import os
import logging
import re
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from queue import Queue
import pandas as pd
import pandas_ta as ta
from openai import OpenAI
from dotenv import load_dotenv
import requests
import websockets

# Load .env
load_dotenv()

# Config
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
CAPITAL = float(os.getenv("CAPITAL", 10000))
COIN = os.getenv("COIN", "BTC")
ACTIVE_EXCHANGE = os.getenv("ACTIVE_EXCHANGE", "hyperliquid")
MAX_POSITION_PCT = 0.02
STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.03
DRAWDOWN_PCT = 0.02

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# AI Clients
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY required")
deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
DEEPSEEK_MODEL = "deepseek-r1-distill-qwen-32b"

if not GROK_API_KEY:
    raise ValueError("GROK_API_KEY required")
grok_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
GROK_MODEL = "grok-4-latest"

# Shared State
current_price = 60000.0  # Default
price_queue = Queue()  # For live updates
DECISION_CACHE = {}

# Exchange Import
from exchanges.base import BaseExchange
if ACTIVE_EXCHANGE == "hyperliquid":
    from exchanges.hyperliquid import HyperliquidExchange
    exchange = HyperliquidExchange(testnet=os.getenv("HYPERLIQUID_TESTNET", "True").lower() == "true", dry_run=DRY_RUN)
elif ACTIVE_EXCHANGE == "dydx":
    if not all([os.getenv("DYDX_API_KEY"), os.getenv("DYDX_API_SECRET"), os.getenv("DYDX_STARK_PRIVATE_KEY")]):
        raise ValueError("dYdX creds required in .env")
    from exchanges.dydx import DydxExchange
    exchange = DydxExchange(
        api_key=os.getenv("DYDX_API_KEY"),
        api_secret=os.getenv("DYDX_API_SECRET"),
        stark_private_key=os.getenv("DYDX_STARK_PRIVATE_KEY"),
        testnet=os.getenv("DYDX_TESTNET", "True").lower() == "true",
        dry_run=DRY_RUN
    )
else:
    raise ValueError(f"Unsupported exchange: {ACTIVE_EXCHANGE}")

POSITION_FILE = "position.json"

def load_position() -> Tuple[float, Optional[float]]:
    try:
        if os.path.exists(POSITION_FILE):
            with open(POSITION_FILE, "r") as f:
                data = json.load(f)
                return data.get("position", 0.0), data.get("entry_price")
    except Exception as e:
        logger.error(f"Load pos error: {e}")
    return 0.0, None

def save_position(position: float, entry_price: Optional[float]):
    try:
        data = {"position": position, "entry_price": entry_price, "timestamp": datetime.now().isoformat()}
        with open(POSITION_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved pos: {position} at {entry_price}")
    except Exception as e:
        logger.error(f"Save pos error: {e}")

async def ws_price_handler():
    """Async WS loop; updates global price."""
    global current_price
    try:
        await exchange.connect_ws()
        await exchange.subscribe_prices(COIN)
        async for msg in exchange.ws:
            data = json.loads(msg)
            price = exchange.parse_price_update(data)
            if price:
                current_price = price
                price_queue.put(price)
                logger.debug(f"Live price: ${price}")
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        await exchange.disconnect_ws()

def start_ws_thread():
    """Start WS in thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ws_price_handler())

def fetch_candles() -> pd.DataFrame:
    """Poll candles every min; use live price for interim."""
    global current_price
    if DRY_RUN:
        # Mock
        end = int(time.time() * 1000)
        timestamps = pd.date_range(end=end, periods=60, freq="1T").astype(int) // 10**6 * 1000
        mock_data = {
            "timestamp": timestamps.tolist(),
            "open": [current_price + i * 10 for i in range(60)],
            "high": [current_price + 10 + i * 10 for i in range(60)],
            "low": [current_price - 10 + i * 10 for i in range(60)],
            "close": [current_price + i * 10 + (i % 2 * 20 - 10) for i in range(60)],
            "volume": [100 + i for i in range(60)]
        }
        df = pd.DataFrame(mock_data)
        df["close"] = pd.to_numeric(df["close"])
        return df
    return exchange.fetch_candles(COIN, "1m")

def compute_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 20:
        raise ValueError("Insufficient data")
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["sma_20"] = ta.sma(df["close"], length=20)
    latest = df.iloc[-1].to_dict()
    latest["price_change_pct"] = ((latest["close"] - df.iloc[-2]["close"]) / df.iloc[-2]["close"]) * 100 if len(df) > 1 else 0
    latest["live_price"] = current_price  # Inject live
    return latest

def query_deepseek_decision(current_data: Dict[str, Any], current_position: float, entry_price: Optional[float]) -> str:
    cache_key = f"ds_{int(time.time() // 60)}"
    if cache_key in DECISION_CACHE:
        return DECISION_CACHE[cache_key]
    
    prompt = f"""
Current {COIN} (candles + live ${current_data['live_price']:.2f}):
- Close: ${current_data['close']:.2f}, RSI: {current_data['rsi']:.1f}, SMA20: ${current_data['sma_20']:.2f}
- Change: {current_data['price_change_pct']:.2f}%

State: Capital ${CAPITAL:.0f}, Pos {current_position:.4f} (val ${current_position * current_data['live_price']:.0f}), Entry ${entry_price:.2f}

Rules: Max {MAX_POSITION_PCT*100}%, SL {STOP_LOSS_PCT*100}%, TP {TAKE_PROFIT_PCT*100}%, Drawdown <{DRAWDOWN_PCT*100}%

<think>1. Action: Trend/RSI sig? 2. Live price impact? 3. Rule check. 4. Propose.</think>

Final: Action: [BUY/SELL/HOLD/CLOSE]\nSize: X\nReason: ...
"""
    try:
        if DRY_RUN:
            return "Action: BUY\nSize: 0.0004\nReason: RSI oversold + live uptick"
        response = deepseek_client.chat.completions.create(
            model=DEEPSEEK_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.6, max_tokens=512, top_p=0.95, timeout=30
        )
        decision = response.choices[0].message.content.strip()
        DECISION_CACHE[cache_key] = decision
        logger.info(f"DeepSeek: {decision}")
        return decision
    except Exception as e:
        logger.error(f"DeepSeek err: {e}")
        return "Action: HOLD\nReason: Error"

def query_grok_review(deepseek_decision: str, current_data: Dict[str, Any], current_position: float, entry_price: Optional[float]) -> str:
    cache_key = f"grok_{int(time.time() // 60)}"
    if cache_key in DECISION_CACHE:
        return DECISION_CACHE[cache_key]
    
    pnl_pct = ((current_data['live_price'] - entry_price) / entry_price * 100) if entry_price and current_position > 0 else 0
    prompt = f"""
DeepSeek: {deepseek_decision}

{COIN}: Close ${current_data['close']:.2f}, Live ${current_data['live_price']:.2f}, RSI {current_data['rsi']:.1f}
State: Cap ${CAPITAL:.0f}, Pos {current_position:.4f} (val ${current_position * current_data['live_price']:.0f}), PnL {pnl_pct:.1f}%

Rules: Enforce max 2%, SL 1.5%, TP 3%, drawdown <2%. Opt for perf/profit.

<think>1. Risk: Safe? 2. Live price vol? 3. Prob >60%? 4. Align/veto.</think>

Review: [APPROVE/MODIFY/VETO]\nAction: ...\nSize: X\nProb Profit: Y%\nReason: ...
"""
    try:
        if DRY_RUN:
            return "APPROVE\nAction: BUY\nSize: 0.0004\nProb Profit: 65%\nReason: Live alignment"
        response = grok_client.chat.completions.create(
            model=GROK_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=512, top_p=0.9, timeout=30
        )
        review = response.choices[0].message.content.strip()
        DECISION_CACHE[cache_key] = review
        logger.info(f"Grok: {review}")
        return review
    except Exception as e:
        logger.error(f"Grok err: {e}")
        return "VETO\nAction: HOLD\nReason: Error"

def parse_decision(text: str) -> Tuple[str, Optional[float], str]:
    action_match = re.search(r"Action:\s*(\w+)", text, re.IGNORECASE)
    size_match = re.search(r"Size:\s*([\d.]+)", text, re.IGNORECASE)
    reason_match = re.search(r"Reason:\s*(.+)", text, re.IGNORECASE)
    return (
        action_match.group(1).upper() if action_match else "HOLD",
        float(size_match.group(1)) if size_match else None,
        reason_match.group(1).strip() if reason_match else ""
    )

def calculate_position_size(price: float) -> float:
    return (CAPITAL * MAX_POSITION_PCT) / price

def tandem_execute(deepseek_text: str, grok_text: str, price: float, pos: float, entry: Optional[float]) -> Tuple[float, Optional[float]]:
    ds_action, ds_size, ds_reason = parse_decision(deepseek_text)
    gr_action, gr_size, gr_reason = parse_decision(grok_text)
    gr_approve = "APPROVE" in grok_text.upper()
    prob_match = re.search(r"Prob Profit:\s*(\d+)%", grok_text)
    prob = float(prob_match.group(1)) if prob_match else 50
    
    logger.info(f"Tandem: DS={ds_action} | Grok={gr_action} (prob={prob}%)")
    
    if ds_action == gr_action and gr_approve and prob > 55:
        action = ds_action
        size = ds_size or calculate_position_size(price)
        reason = f"Consensus: {ds_reason} + {gr_reason}"
    else:
        action = "HOLD"
        reason = f"Mismatch/low prob - hold"
        logger.warning(reason)
    
    if action == "BUY" and pos == 0:
        order = exchange.build_buy_order(COIN, size, price)
        try:
            result = exchange.place_order(order)
            logger.info(f"BUY {size:.4f} at ${price} | {reason}")
            save_position(size, price)
            return size, price
        except Exception as e:
            logger.error(f"BUY fail: {e}")
    
    elif (action in ("SELL", "CLOSE")) and pos > 0:
        pnl = (price - entry) / entry if entry else 0
        if pnl < -STOP_LOSS_PCT or pnl > TAKE_PROFIT_PCT or action == "CLOSE":
            order = exchange.build_sell_order(COIN, pos, price)
            try:
                result = exchange.place_order(order)
                logger.info(f"SELL {pos:.4f} at ${price} | PnL {pnl*100:.1f}% | {reason}")
                save_position(0, None)
                return 0, None
            except Exception as e:
                logger.error(f"SELL fail: {e}")
    
    logger.info(f"{action}: {reason}")
    return pos, entry

def main():
    logger.info(f"Starting {ACTIVE_EXCHANGE.upper()} Bot with WS...")
    pos, entry = load_position()
    logger.info(f"Resumed: Pos {pos}, Entry ${entry}")
    
    # Start WS thread
    ws_thread = threading.Thread(target=start_ws_thread, daemon=True)
    ws_thread.start()
    time.sleep(2)  # Wait for connect
    
    last_candle_time = 0
    while True:
        try:
            # Poll candles every min
            now = time.time()
            if now - last_candle_time >= 60:
                df = fetch_candles()
                latest = compute_indicators(df)
                last_candle_time = now
            else:
                latest = {"close": current_price, "live_price": current_price, "rsi": 50, "sma_20": current_price, "price_change_pct": 0}  # Stub
            
            logger.info(f"Live: ${current_price}, RSI {latest.get('rsi', 'N/A')}")
            
            ds_dec = query_deepseek_decision(latest, pos, entry)
            gr_rev = query_grok_review(ds_dec, latest, pos, entry)
            
            pos, entry = tandem_execute(ds_dec, gr_rev, current_price, pos, entry)
            
            time.sleep(30)  # Check every 30s
            
        except KeyboardInterrupt:
            logger.info("Stopped.")
            save_position(pos, entry)
            break
        except Exception as e:
            logger.error(f"Loop err: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
