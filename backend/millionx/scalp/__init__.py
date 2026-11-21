"""MillionX 2.2 — Continuous Directional Scalping Engine.

features.py   1-second features (shared by the live scanner and the event-driven backtest)
engines.py    Engine A Momentum, Engine B Pullback, Engine C Order Flow
exits.py      position management (SL, TP, break-even, trailing, exhaustion, order-flow reversal, max hold)
costs.py      fees, spread, slippage, latency, funding; maker only on confirmed fills
opportunity.py calibrated ranking (expected move, holding time, net EV, R/R, data quality)
research.py   event-driven 1 s backtest on aggTrades (CI) and aggregation (walk-forward, portfolio, leverage)
live.py       continuous scanner + virtual PaperBook on live streams (never sends exchange orders)
"""
