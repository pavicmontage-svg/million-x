# QUANT TITAN AI

Modular, research-driven multi-strategy trading platform for **Binance USDT-M Futures**, delivered as an
**Android app** (the whole platform runs on the phone) and optionally as a server (Docker).

> **Paper trading is the default. Live trading is disabled** unless it is enabled at the deployment level
> (`QT_ALLOW_LIVE=1`), API keys pass a no-withdrawal check, and you type a confirmation in the app.
> Nothing in this repository claims profitability. See [Research results](#research-results-honest).

## Get the APK (no installation on a computer)

1. Every push that touches `quant-titan/` runs the workflow **quant-titan** (tests on two Python stacks → APK build).
2. On the phone open **github.com/pavicmontage-svg/million-x → Releases → "Quant Titan (latest build …)"**,
   download `QuantTitan-<commit>-arm64.apk`, allow installation from the browser, install.
3. Open the app. The first start unpacks Python, numpy, pandas, scipy and statsmodels (up to a minute).
   A persistent notification shows the engine status; it keeps running with the screen off.

Details, battery settings and signing: [docs/ANDROID.md](docs/ANDROID.md).

## What is inside

| Area | Module | Notes |
|---|---|---|
| 6 independent engines | `quanttitan/engines/` | VWAP mean reversion, trend & momentum, volatility breakout, order flow & liquidity, statistical arbitrage, market regime |
| Central orchestrator | `quanttitan/orchestrator/` | regime-gated decision framework (+ majority-vote and confidence-average baselines for comparison), cost model, evidence-based edge book |
| 6 analytical roles | `quanttitan/agents/`, `quanttitan/risk/manager.py` | quant researcher, market analyst, execution specialist, portfolio manager, **risk manager (veto, signed approvals)**, performance auditor |
| Risk | `quanttitan/risk/` | risk-based sizing, open-risk / exposure / leverage / correlation / liquidation checks, circuit breakers |
| Execution | `quanttitan/execution/` | Binance signed client (algo-service conditional orders), paper exchange, idempotent order manager, protected positions, reconciliation |
| Research lab | `quanttitan/backtest/`, `quanttitan/research/` | portfolio backtester sharing the live decision path, walk-forward, Monte Carlo, sensitivity, bootstrap, deflated Sharpe |
| Runtime & API | `quanttitan/runtime/`, `quanttitan/api/` | live/paper loop, Starlette REST + WebSocket, auth, audit |
| Dashboard | `quanttitan/web/` | mobile-first control center (15 pages + engine detail pages), emergency stop |
| Android | `android/` | Chaquopy-embedded Python, WebView, foreground service, alert notifications |

Documentation: [Architecture](docs/ARCHITECTURE.md) · [Engines](docs/ENGINES.md) · [Risk](docs/RISK.md) ·
[Execution](docs/EXECUTION.md) · [Backtesting](docs/BACKTESTING.md) · [Security](docs/SECURITY.md) ·
[Operations & recovery](docs/OPERATIONS.md) · [Deployment](docs/DEPLOYMENT.md) · [Android](docs/ANDROID.md) ·
[Limitations](docs/LIMITATIONS.md)

## Run on a computer

```bash
cd quant-titan
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -o addopts="" -q                 # 115+ tests
python -m quanttitan download BTCUSDT,ETHUSDT 2026-07-01 2026-10-31
python -m quanttitan backtest BTCUSDT,ETHUSDT
python -m quanttitan serve --port 8765            # dashboard on http://127.0.0.1:8765
python -m quanttitan serve --replay BTCUSDT,ETHUSDT  # replay history through the live pipeline
```

## Execution timeframe: 3m (default)

The execution timeframe is **3 minutes** by default (Settings → system → execution_timeframe: 1m / 3m / 5m / 15m).
Chart settings (EMA, ADX, ATR, Bollinger, VWAP bands) stay the same when switching; durations (time stops,
cooldowns, statistics windows, minimum VWAP session length) are rescaled to keep the same wall-clock meaning.

**3m vs 5m, same 6 perpetuals, 8 Jul – 31 Oct 2026, same costs** (`python scripts/timeframe_study.py`):

| Variant | 5m: trades / return / PF / E[R] | 3m: trades / return / PF / E[R] |
|---|---|---|
| Full system | 72 / −1.02% / 0.92 / +0.03 | 47 / −0.50% / 0.92 / −0.05 |
| VWAP only | 3 / +0.11% / 1.22 / +0.10 | 2 / −0.07% / 0.86 / −0.08 |
| VWAP only, cost gate relaxed (research) | 12 / +2.23% / 2.09 / +0.46 | 29 / −2.09% / 0.71 / −0.19 |

On 3m the VWAP engine signals ~4× more often (215 vs 54 signals), but the stops are tighter, so fees and
slippage take a larger share of each trade (fees 304 USDT on 29 trades). In this period 3m did **not** improve
VWAP results; every 95% confidence interval includes zero, so none of these differences is statistically
reliable. Paper trading on 3m will add evidence over time.

## Research results (honest)

Baseline study: 6 perpetuals (BTC, ETH, SOL, BNB, XRP, DOGE), 5-minute bars, **1 Jul – 31 Oct 2026**, Binance
archive data, taker fees 0.05%, modelled slippage and spread, historical funding. Reproduce with
`python scripts/research_study.py --walk-forward`.

| Experiment | Trades | Net return | Expectancy | Profit factor | Max DD |
|---|---|---|---|---|---|
| Default configuration | 74 | −1.12% | +0.02 R | 0.91 | 3.6% |
| Cost filter relaxed (research only) | 56 | −9.11% | −0.57 R | 0.37 | 9.7% |
| Walk-forward, 5 folds, out-of-sample | 171 | −10.0%* | −0.07 R (95% CI −0.30…+0.15) | 0.72 | 14.4% |

\* stopped by the monthly-drawdown circuit breaker. Deflated Sharpe ratio of the walk-forward: **0.002** —
there is **no statistically demonstrated edge** in this period. Fees (230 USDT) and slippage (196 USDT) exceeded
the gross edge of the default run; relaxing the cost filter made results much worse, which validates the filter.
The platform therefore mostly **waits** — trade frequency is a result of measured edge, not a target.
Use the research lab and paper trading to look for edge before risking real money.
