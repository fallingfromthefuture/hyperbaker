# hyperbaker
deepseek baker test


Multi-DEX Trading Bot: DeepSeek + Grok Tandem with WS1-min timeframe bot supporting Hyperliquid, dYdX (easy add others). WS for live prices (low-latency decisions); modular for DEX toggles.FeaturesWS Live Prices: Subscribe to tickers; update price real-time (thread-safe).
Exchanges: Hyperliquid (default), dYdX; abstract for more (e.g., GMX via subclass).
Tandem AI: DeepSeek (signals) + Grok (risk/profit).
Config: .env toggles (ACTIVE_EXCHANGE=hyperliquid; *_ENABLED=True).
Rules: 2% pos, 1.5% SL, 3% TP.
Dry-run, persistence, logging.

Setup on MacBook ProPython 3.12+ (brew install python).
git clone ... && cd multi-dex-trading-bot
pip install -r requirements.txt
.env: Keys for AIs/exchanges; set ACTIVE_EXCHANGE.
python main.py
Test: DRY_RUN=True; switch exchanges via .env.

Adding ExchangesSubclass BaseExchange in exchanges/new_dex.py.
Implement WS/REST methods.
Add .env vars (e.g., NEWDEX_API_KEY).
Set ACTIVE_EXCHANGE=new_dex.

RisksTestnet first. API costs/limits apply.
Not advice.

LicenseMIT.

