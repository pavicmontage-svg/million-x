# The six engines

Every engine has its own configuration section (`engines.<name>`), enable switch, minimum confidence,
cooldown, risk multiplier (≤ 1), max open positions, daily loss limit in R, statistics and logger. Engines never
read each other's output. Confidence is a **transparent weighted component score** (each component is shown in
the dashboard); it is *not* a probability. The probability-like quantity the system relies on is the
**evidence-based expectancy** in the edge book (see `orchestrator/edge.py`), and the Performance Auditor checks
whether confidence is actually related to outcomes (Spearman correlation).

Full methodology lives in each module's docstring (also shown on the engine detail page in the app).

## 1. VWAP mean reversion — `engines/vwap.py`

**Band calculation** (typical price `p = (H+L+C)/3`, sums over the session = UTC day, or a rolling window):

```
VWAP  = Σ v·p / Σ v
σ     = sqrt( Σ v·p² / Σ v − VWAP² )            volume-weighted standard deviation
band_k = VWAP ± k·σ     (k = band_mults, default 1, 2, 3)
z      = (price − VWAP) / σ
```

LONG (SHORT mirrors): excursion low reached `z ≤ −entry_band` in the last `touch_lookback` bars; excursion is ≥
`min_deviation_atr` ATR (large enough to pay costs); session has ≥ `min_session_bars`; **not** band-riding
(≥ `ride_bars` of `ride_window` closes beyond the band = trend continuation); 1h ADX/DI and the regime engine do not
show a strong opposing trend; signal bar closes back inside the band, bullish, with a rejection wick or a close
above the prior high; relative volume and taker-buy ratio confirm; TP1 at VWAP ≥ `min_rr_tp1` R; TP2 at VWAP + 1σ;
stop beyond the excursion extreme − `stop_buffer_atr` ATR.
States: NORMAL_DEVIATION, OVEREXTENDED, STRONG_TREND_CONTINUATION, POTENTIAL_REVERSAL, FALSE_REJECTION,
VWAP_RECLAIM, VWAP_BREAKDOWN, HIGH_VOLATILITY.

## 2. Trend & momentum — `engines/trend.py`
EMA 20/50/200 stack + 1h EMA 50/200, ADX/DI, 12-bar ROC and its acceleration, relative volume.
Setups: **pullback** into the EMA20–50 zone with a resumption bar, **continuation** on a break of a 20-bar
structure with rising ADX. Late-entry guard (max extension in ATR). Structural stop clipped to [1, stop_atr] ATR,
TP1 at `tp1_r`, runner with a chandelier trailing stop, time stop, deterioration exit (close through EMA50 with
opposite DI dominance). States: STRONG/WEAK BULL/BEAR, TREND_REVERSAL, TREND_EXHAUSTION, MOMENTUM_ACCELERATION.

## 3. Volatility breakout — `engines/breakout.py`
Compression = Bollinger width percentile ≤ `compression_pct` on ≥ `min_compression_bars` of the box window (a
count, so one noisy bar does not reset it) and box ≤ `max_box_atr`. Breakout requires relative volume and an
expansion bar (true range ≥ `min_atr_expansion` × ATR(50)); 1h trend must not oppose; no chasing beyond the
measured-move target. Modes: breakout close or retest. States: COMPRESSION, VALID_BREAKOUT, LOW_VOLUME_BREAKOUT,
FALSE_BREAKOUT, BREAKOUT_CONTINUATION, BREAKOUT_RETEST, FAILED_RETEST.

## 4. Order flow & liquidity — `engines/order_flow.py`, `data/micro.py`
Bar tier (backtestable): taker-buy ratio, normalised CVD, volume z-score, average trade size z-score, price
response (corr of returns with signed volume), absorption. Live tier: book imbalance, spread, depth within 0.5%,
depth change, aggressive buy/sell notional, large-print share and bias (prints above the 95th size percentile —
measured, **no claim of identifying institutions or hidden orders**), per-trade price impact.
Default role: **confirmation** (opposition ≥ `oppose_threshold` rejects a trade; support adds a bounded boost).
Standalone only when configured *and* ≥ `min_samples_standalone` validated trades exist.

## 5. Statistical arbitrage — `engines/stat_arb.py` (disabled by default)
Correlation is only a pre-filter. Required: Engle–Granger cointegration p ≤ `max_coint_pvalue` (statsmodels), the
relationship holding in ≥ `min_stable_windows` sub-windows, hedge-ratio drift ≤ `max_beta_drift`, OU half-life in
range, positive β, and expected reversion minus costs (both legs) ≥ `min_edge_z`. Entry |z| ≥ entry_z, exit ≤
exit_z, stop ≥ stop_z, time stop in half-lives. Pairs have their own risk bucket (`max_pair_risk_pct`), each leg
carries a catastrophic exchange stop, and a failed leg unwinds the pair.

## 6. Market regime & volatility — `engines/regime.py`
Ordered, explainable rules on ADX, efficiency ratio, EMA slope, 1h structure, realized-vol ratio, ATR% and
BB-width percentiles, jump size in σ, spread, funding and data age → UNKNOWN, UNSAFE_MARKET, MARKET_DISLOCATION,
VOLATILITY_EXPANSION, HIGH_VOLATILITY, STRONG_TREND, VOLATILITY_COMPRESSION, WEAK_TREND, LOW_VOLATILITY, RANGE,
plus flags. Output: permitted engines (configurable eligibility matrix) and a risk multiplier (≤ 1).
Regime labels are logged per bar in backtests (`metrics.by_regime`). Tested against synthetic ground truth.
