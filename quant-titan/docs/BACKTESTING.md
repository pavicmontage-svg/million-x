# Backtesting & research lab

Data: Binance public archive (`data.binance.vision`) — monthly/daily USDT-M kline and funding files, verified for
gaps/duplicates/OHLC consistency, cached as `.npz`. The archive is reachable from locations where the REST API is not.

## Bias controls
| Risk | Control |
|---|---|
| Look-ahead | causal indicators (truncation tests), decisions at bar close → fill at next open, HTF values only from closed HTF bars, future-perturbation test |
| Unrealistic fills | slippage = base + vol × ATR% + √participation impact, half spread, taker fees, gap-through stops, stop-first intrabar, entry cancelled if it gaps beyond the approved price band |
| Liquidity | order notional ≤ `max_bar_participation` × bar quote volume |
| Funding | historical 8h funding applied to open notional (default rate if missing) |
| Leverage | the same risk manager (leverage cap, liquidation distance) |
| Overfitting | walk-forward with parameter choice on train only; only OOS trades become evidence; deflated Sharpe accounts for the number of trials; sensitivity analysis; bootstrap CIs |
| Survivorship | the universe is passed in explicitly — pick it *as of* the test start, not from today's top list |

## Experiments (`research/lab.py`, also runnable from the app)
`backtest` (+ Monte Carlo), `train_test`, `walk_forward`, `monte_carlo` (shuffle + bootstrap: drawdown and return
distributions, probability of loss / of a large drawdown), `sensitivity` (plateau vs spike), `policy_comparison`
(regime-gated vs majority vote vs confidence average).

Metrics: net/gross PnL, return, win rate, profit factor, expectancy (USDT and R), average win/loss, max drawdown
(closed-trade and mark-to-market), Sharpe, Sortino, Calmar, trade count, holding time, fees, funding, slippage,
long vs short, and breakdowns by regime, instrument, engine, exit reason and hour.

## Baseline study (1 Jul – 31 Oct 2026, 6 perpetuals, 5m)
See the table in the README. Summary: no statistically demonstrated edge after costs (walk-forward deflated
Sharpe 0.002); costs dominate tight 5-minute stops; the monthly drawdown breaker capped losses at the configured
10%. Policy comparison on the relaxed-cost configuration: regime-gated −9.1% (56 trades), majority vote −8.5%
(67), confidence average −8.5% (67) — none acceptable. This is exactly why the default configuration only trades
when costs are ≤ 0.25 R and why live trading requires validated evidence.
