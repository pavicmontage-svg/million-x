"""Typed, validated, versioned configuration.

Every tunable parameter of the platform lives here. Nothing is hard-coded in the engines, the
risk manager or the execution layer: they all receive a section of this tree. The dashboard edits
the tree through the API; each change is validated by pydantic, stored as a new version in the
database and recorded in the audit log, so every change is reversible.

The defaults are *initial test parameters*, not optimised or recommended values.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Literal

try:  # pydantic 2 (desktop/server) ships the v1 API as pydantic.v1
    from pydantic.v1 import BaseModel, Field, root_validator
except ImportError:  # pydantic 1.10 (Android/Chaquopy: pydantic-core has no Android build)
    from pydantic import BaseModel, Field, root_validator


class _M(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


class TradingMode(str, Enum):
    PAPER = "PAPER"
    SEMI_AUTOMATIC = "SEMI_AUTOMATIC"
    AUTOMATIC = "AUTOMATIC"


class ExchangeEnv(str, Enum):
    """Where orders go. PAPER = simulated fills on live data. TESTNET = Binance futures testnet."""

    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class DirectionMode(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


# --------------------------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------------------------
class RiskConfig(_M):
    risk_per_trade_pct: float = Field(0.5, gt=0, le=2.0, description="% of equity risked to the stop")
    max_open_risk_pct: float = Field(2.0, gt=0, le=10.0, description="sum of risk-to-stop of all open positions")
    daily_loss_limit_pct: float = Field(2.0, gt=0, le=20.0)
    weekly_loss_limit_pct: float = Field(5.0, gt=0, le=40.0)
    monthly_drawdown_limit_pct: float = Field(10.0, gt=0, le=60.0)
    max_consecutive_losses: int = Field(3, ge=1, le=50)
    consecutive_loss_cooldown_min: int = Field(240, ge=0, le=10080)
    max_leverage: float = Field(5.0, ge=1.0, le=50.0, description="max gross notional / equity and per-position leverage")
    max_symbol_exposure_pct: float = Field(150.0, gt=0, le=2000.0, description="max notional on one symbol, % of equity")
    max_concurrent_positions: int = Field(4, ge=1, le=50)
    max_correlated_risk_pct: float = Field(1.0, gt=0, le=10.0, description="max same-direction risk inside one correlation cluster")
    correlation_threshold: float = Field(0.7, ge=0.0, le=1.0)
    max_spread_bps: float = Field(5.0, gt=0, le=200)
    max_slippage_bps: float = Field(10.0, gt=0, le=500)
    max_abs_funding_rate: float = Field(0.0010, gt=0, le=0.05, description="per 8h; block the side that pays more than this")
    min_liquidation_distance_mult: float = Field(2.5, ge=1.0, le=20.0, description="liq distance must be >= mult x stop distance")
    max_stop_distance_pct: float = Field(6.0, gt=0, le=50.0)
    max_data_staleness_s: float = Field(90.0, gt=0, le=3600)
    drawdown_derisk_start_pct: float = Field(4.0, ge=0, le=50, description="from this drawdown, size scales down linearly")
    drawdown_derisk_floor: float = Field(0.5, gt=0, le=1.0, description="minimum size multiplier while de-risking")
    breaker_reset: Literal["auto_next_period", "manual"] = "auto_next_period"
    min_equity_usdt: float = Field(50.0, ge=0)

    @root_validator(skip_on_failure=True)
    def _consistency(cls, v: dict) -> dict:
        if v["max_open_risk_pct"] < v["risk_per_trade_pct"]:
            raise ValueError("max_open_risk_pct must be >= risk_per_trade_pct")
        if v["weekly_loss_limit_pct"] < v["daily_loss_limit_pct"]:
            raise ValueError("weekly_loss_limit_pct must be >= daily_loss_limit_pct")
        return v


# --------------------------------------------------------------------------------------------
# Costs (used by backtest, paper simulation and the pre-trade edge check)
# --------------------------------------------------------------------------------------------
class CostConfig(_M):
    taker_fee: float = Field(0.0005, ge=0, le=0.01)
    maker_fee: float = Field(0.0002, ge=-0.001, le=0.01)
    base_slippage_bps: float = Field(1.0, ge=0, le=100)
    vol_slippage_coef: float = Field(0.05, ge=0, le=5, description="extra slippage as fraction of 5m ATR%")
    impact_coef: float = Field(0.5, ge=0, le=20, description="square-root impact coefficient vs bar volume participation")
    max_bar_participation: float = Field(0.01, gt=0, le=0.2, description="max order size as fraction of bar quote volume")
    default_funding_rate: float = Field(0.0001, ge=-0.01, le=0.01, description="used when no funding history is available")
    latency_ms: int = Field(250, ge=0, le=10000)


# --------------------------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------------------------
class EngineBase(_M):
    enabled: bool = True
    min_confidence: float = Field(60.0, ge=0, le=100)
    cooldown_bars: int = Field(10, ge=0, le=5000, description="bars after a signal on a symbol before the next one (30 min on 3m)")
    risk_multiplier: float = Field(1.0, gt=0, le=1.0, description="engine-level risk scaling, never > 1")
    max_open_positions: int = Field(2, ge=0, le=50)
    daily_loss_limit_r: float = Field(3.0, gt=0, le=50, description="engine suspended for the day after this many R lost")


class VWAPConfig(EngineBase):
    session_anchor: Literal["utc_day", "rolling"] = "utc_day"
    rolling_bars: int = Field(480, ge=20, le=20000, description="rolling VWAP window (480 × 3m = 1 day)")
    band_mults: list[float] = Field(default_factory=lambda: [1.0, 2.0, 3.0])
    entry_band: float = Field(1.0, ge=0.5, le=4.0)
    overextended_band: float = Field(3.0, ge=1.5, le=6.0)
    min_session_bars: int = Field(40, ge=3, le=5000, description="bars since session start before σ is trusted (2h on 3m)")
    min_deviation_atr: float = Field(1.0, ge=0, le=10)
    touch_lookback: int = Field(3, ge=1, le=20)
    min_wick_ratio: float = Field(0.35, ge=0, le=1)
    min_rel_volume: float = Field(0.8, ge=0, le=10)
    min_taker_ratio_confirm: float = Field(0.5, ge=0, le=1)
    max_htf_adx: float = Field(30.0, ge=5, le=80, description="do not fade when 1h ADX is above this and trend opposes")
    ride_bars: int = Field(4, ge=2, le=20, description="closes beyond band within ride_window -> trend continuation")
    ride_window: int = Field(6, ge=2, le=40)
    stop_buffer_atr: float = Field(0.3, ge=0, le=5)
    min_rr_tp1: float = Field(1.0, ge=0.2, le=10)
    tp1_fraction: float = Field(0.5, ge=0.1, le=1.0)
    time_stop_bars: int = Field(60, ge=1, le=5000)


class TrendConfig(EngineBase):
    ema_fast: int = Field(20, ge=2, le=500)
    ema_mid: int = Field(50, ge=3, le=1000)
    ema_slow: int = Field(200, ge=5, le=2000)
    htf_ema_fast: int = Field(50, ge=2, le=500)
    htf_ema_slow: int = Field(200, ge=5, le=2000)
    adx_period: int = Field(14, ge=2, le=100)
    min_adx: float = Field(22.0, ge=5, le=80)
    strong_adx: float = Field(30.0, ge=5, le=90)
    pullback_atr: float = Field(0.6, ge=0, le=5, description="max distance of pullback low from EMA zone")
    max_extension_atr: float = Field(2.5, ge=0.5, le=20, description="no entry when close is this far from fast EMA")
    bos_lookback: int = Field(20, ge=3, le=500)
    min_rel_volume: float = Field(1.0, ge=0, le=10)
    stop_atr: float = Field(1.8, ge=0.3, le=10)
    tp1_r: float = Field(1.5, ge=0.3, le=20)
    tp1_fraction: float = Field(0.5, ge=0.1, le=1.0)
    trail_atr: float = Field(3.0, ge=0.5, le=20)
    time_stop_bars: int = Field(160, ge=1, le=10000)
    allow_continuation: bool = True
    allow_pullback: bool = True


class BreakoutConfig(EngineBase):
    bb_period: int = Field(20, ge=5, le=500)
    bb_mult: float = Field(2.0, ge=0.5, le=5)
    width_pct_lookback: int = Field(830, ge=50, le=20000)
    compression_pct: float = Field(20.0, ge=1, le=60, description="BB width percentile considered compressed")
    min_compression_bars: int = Field(12, ge=2, le=500)
    box_lookback: int = Field(24, ge=5, le=500)
    breakout_buffer_atr: float = Field(0.1, ge=0, le=3)
    min_rel_volume: float = Field(1.5, ge=0, le=20)
    min_atr_expansion: float = Field(1.2, ge=0.5, le=10, description="breakout bar true range / ATR(50)")
    entry_mode: Literal["breakout_close", "retest"] = "breakout_close"
    retest_window: int = Field(6, ge=1, le=100)
    false_breakout_bars: int = Field(3, ge=1, le=50)
    max_box_atr: float = Field(6.0, ge=1, le=50, description="box wider than this is not compression")
    stop_mode: Literal["box_mid", "box_opposite"] = "box_mid"
    stop_buffer_atr: float = Field(0.25, ge=0, le=5)
    target_box_mult: float = Field(1.0, ge=0.25, le=10)
    tp1_fraction: float = Field(0.5, ge=0.1, le=1.0)
    trail_atr: float = Field(2.5, ge=0.5, le=20)
    time_stop_bars: int = Field(80, ge=1, le=10000)


class OrderFlowConfig(EngineBase):
    role: Literal["confirmation", "standalone"] = "confirmation"
    min_samples_standalone: int = Field(100, ge=10, le=100000)
    cvd_window: int = Field(12, ge=2, le=500)
    vol_z_window: int = Field(160, ge=10, le=10000)
    min_taker_ratio: float = Field(0.56, ge=0.5, le=1.0)
    book_levels: int = Field(10, ge=1, le=100)
    min_book_imbalance: float = Field(0.15, ge=0, le=1)
    large_trade_quantile: float = Field(0.95, ge=0.5, le=0.999)
    max_spread_bps: float = Field(5.0, gt=0, le=200)
    oppose_threshold: float = Field(65.0, ge=0, le=100, description="contra-confidence at which order flow vetoes")
    confirm_boost: float = Field(8.0, ge=0, le=30)
    stop_atr: float = Field(1.5, ge=0.3, le=10)
    tp1_r: float = Field(1.2, ge=0.3, le=20)
    tp1_fraction: float = Field(0.5, ge=0.1, le=1.0)
    time_stop_bars: int = Field(20, ge=1, le=5000)


class StatArbConfig(EngineBase):
    enabled: bool = False  # optional by design
    timeframe: Literal["5m", "15m", "1h"] = "15m"
    lookback_bars: int = Field(960, ge=100, le=20000)
    min_correlation: float = Field(0.75, ge=0, le=1)
    max_coint_pvalue: float = Field(0.05, gt=0, le=0.5)
    stability_windows: int = Field(3, ge=1, le=20)
    min_stable_windows: int = Field(2, ge=1, le=20)
    max_beta_drift: float = Field(0.25, ge=0, le=5, description="relative hedge ratio change across windows")
    min_half_life_bars: float = Field(2.0, ge=0.5, le=10000)
    max_half_life_bars: float = Field(96.0, ge=1, le=100000)
    entry_z: float = Field(2.0, ge=0.5, le=10)
    exit_z: float = Field(0.5, ge=0, le=5)
    stop_z: float = Field(3.5, ge=1, le=20)
    time_stop_half_lives: float = Field(3.0, ge=0.5, le=50)
    max_pairs: int = Field(2, ge=0, le=20)
    max_pair_risk_pct: float = Field(0.5, gt=0, le=5, description="separate risk bucket for pair trades")
    min_edge_z: float = Field(0.8, ge=0, le=10, description="(entry_z - exit_z) must exceed cost in z units by this")


class RegimeConfig(EngineBase):
    adx_period: int = Field(14, ge=2, le=100)
    er_period: int = Field(20, ge=2, le=500)
    strong_adx: float = Field(30.0, ge=5, le=90)
    weak_adx: float = Field(20.0, ge=5, le=90)
    strong_er: float = Field(0.35, ge=0, le=1)
    range_er: float = Field(0.25, ge=0, le=1)
    vol_lookback: int = Field(3360, ge=100, le=100000, description="bars for volatility percentiles (7 days of 3m)")
    high_vol_pct: float = Field(85.0, ge=50, le=100)
    low_vol_pct: float = Field(15.0, ge=0, le=50)
    expansion_ratio: float = Field(1.6, ge=1.0, le=10, description="short/long realized vol ratio")
    compression_ratio: float = Field(0.6, ge=0.05, le=1.0)
    dislocation_vol_pct: float = Field(99.0, ge=80, le=100)
    jump_sigma: float = Field(6.0, ge=2, le=50, description="single-bar move in sigmas that flags a jump")
    jump_lookback: int = Field(10, ge=1, le=500)
    max_spread_bps: float = Field(10.0, gt=0, le=500)
    unsafe_funding: float = Field(0.003, gt=0, le=0.1)
    min_bars: int = Field(500, ge=50, le=100000)
    high_vol_risk_mult: float = Field(0.5, gt=0, le=1)
    expansion_risk_mult: float = Field(0.75, gt=0, le=1)
    eligibility: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "STRONG_TREND": ["trend", "breakout", "order_flow", "stat_arb"],
            "WEAK_TREND": ["trend", "vwap", "order_flow", "stat_arb"],
            "RANGE": ["vwap", "order_flow", "stat_arb"],
            "LOW_VOLATILITY": ["vwap", "breakout", "stat_arb"],
            "VOLATILITY_COMPRESSION": ["breakout", "vwap", "stat_arb"],
            "VOLATILITY_EXPANSION": ["trend", "breakout", "order_flow"],
            "HIGH_VOLATILITY": ["trend", "order_flow"],
            "MARKET_DISLOCATION": [],
            "UNSAFE_MARKET": [],
            "UNKNOWN": [],
        }
    )


class EnginesConfig(_M):
    vwap: VWAPConfig = Field(default_factory=VWAPConfig)
    trend: TrendConfig = Field(default_factory=TrendConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    order_flow: OrderFlowConfig = Field(default_factory=OrderFlowConfig)
    stat_arb: StatArbConfig = Field(default_factory=StatArbConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)


# --------------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------------
class OrchestratorConfig(_M):
    policy: Literal["regime_gated", "majority_vote", "confidence_average"] = "regime_gated"
    conflict_margin: float = Field(25.0, ge=0, le=100, description="confidence gap needed to override an opposing primary")
    orderflow_confirmation: Literal["off", "optional", "required"] = "optional"
    min_net_edge_r: float = Field(0.05, ge=-1, le=5, description="min expected R after costs")
    max_cost_r: float = Field(0.25, ge=0, le=2, description="round-trip cost as fraction of risk; above -> reject")
    edge_prior_strength: float = Field(30.0, ge=0, le=10000, description="pseudo-trades shrinking expectancy to prior")
    edge_prior_mean_r: float = Field(0.0, ge=-1, le=1)
    min_validated_trades: int = Field(30, ge=0, le=100000, description="AUTOMATIC/LIVE requires this much evidence")
    paper_exploration: bool = Field(True, description="PAPER may trade unvalidated setups to gather evidence")
    min_rr_after_costs: float = Field(1.0, ge=0, le=20, description="exploration: TP1 reward/risk after costs")
    semi_auto_ttl_s: int = Field(180, ge=10, le=3600)


# --------------------------------------------------------------------------------------------
# Execution & universe
# --------------------------------------------------------------------------------------------
class ExecutionConfig(_M):
    exchange_env: ExchangeEnv = ExchangeEnv.PAPER
    entry_order_type: Literal["MARKET", "LIMIT_IOC"] = "MARKET"
    limit_offset_bps: float = Field(2.0, ge=0, le=100)
    margin_type: Literal["ISOLATED", "CROSSED"] = "ISOLATED"
    conditional_endpoint: Literal["algo", "legacy"] = "algo"
    working_type: Literal["MARK_PRICE", "CONTRACT_PRICE"] = "MARK_PRICE"
    request_timeout_s: float = Field(8.0, gt=0, le=60)
    max_retries: int = Field(3, ge=0, le=10)
    reconcile_interval_s: int = Field(30, ge=5, le=3600)
    on_unexpected_position: Literal["alert_only", "protect", "flatten"] = "protect"
    max_entry_drift_r: float = Field(0.3, ge=0, le=5, description="reject entry if price moved this many R from signal")
    move_stop_to_be_after_tp1: bool = True


class UniverseConfig(_M):
    size: int = Field(10, ge=1, le=50)
    mode: Literal["dynamic", "fixed"] = "dynamic"
    fixed_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    blacklist: list[str] = Field(default_factory=list)
    min_quote_volume_24h: float = Field(100_000_000.0, ge=0)
    max_spread_bps: float = Field(4.0, gt=0, le=200)
    min_depth_usdt: float = Field(250_000.0, ge=0, description="bid+ask depth within 0.5% of mid")
    max_abs_funding: float = Field(0.002, gt=0, le=0.1)
    min_listing_days: int = Field(30, ge=0, le=3650)
    refresh_minutes: int = Field(60, ge=5, le=10080)


class NotificationConfig(_M):
    enabled_events: dict[str, bool] = Field(
        default_factory=lambda: {
            k: True
            for k in [
                "trade_opened", "trade_closed", "stop_loss", "take_profit", "daily_loss_limit",
                "weekly_loss_limit", "circuit_breaker", "exchange_failure", "websocket_failure",
                "unexpected_position", "risk_violation", "strategy_suspended", "market_dislocation",
                "emergency_stop", "reconciliation_failure", "decision_pending",
            ]
        }
    )
    min_severity: Literal["info", "warning", "critical"] = "info"
    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""
    dedup_window_s: int = Field(300, ge=0, le=86400)


class SystemConfig(_M):
    trading_mode: TradingMode = TradingMode.PAPER
    direction: DirectionMode = DirectionMode.BOTH
    execution_timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h"] = "3m"
    analysis_timeframes: list[str] = Field(default_factory=lambda: ["1m", "3m", "15m", "1h", "4h"])
    paper_starting_equity: float = Field(10_000.0, gt=0)
    trading_paused: bool = False
    market_data_source: Literal["binance_live", "binance_testnet", "replay"] = "binance_live"
    replay_speed: float = Field(0.0, ge=0, le=1e6, description="seconds of sleep per replayed bar; 0 = as fast as possible")


class AppConfig(_M):
    system: SystemConfig = Field(default_factory=SystemConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    engines: EnginesConfig = Field(default_factory=EnginesConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)


# Statistics windows that express wall-clock time (a VWAP day, a 7-day volatility history, a 2-hour
# minimum VWAP session, ...). When the execution timeframe changes they are rescaled so they keep the
# same meaning. Indicator lengths (EMA, ADX, ATR, Bollinger, structure/box lookbacks) and trade
# management (time stops, cooldowns) are chart settings counted in bars of the chart — exactly as when
# a strategy is moved to another chart in TradingView — and are NOT rescaled.
TIME_PARAMS: dict[str, tuple[str, ...]] = {
    "vwap": ("rolling_bars", "min_session_bars"),
    "breakout": ("width_pct_lookback",),
    "order_flow": ("vol_z_window",),
    "regime": ("vol_lookback", "jump_lookback", "min_bars"),
}
_TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def rescale_timeframe(cfg: "AppConfig", new_tf: str) -> "AppConfig":
    """Return a copy of ``cfg`` on ``new_tf`` with duration parameters rescaled to the same wall-clock time."""
    old_tf = cfg.system.execution_timeframe
    d = dump(cfg)
    d["system"]["execution_timeframe"] = new_tf
    if old_tf != new_tf:
        f = _TF_MS[old_tf] / _TF_MS[new_tf]
        models = {n: type(getattr(cfg.engines, n)) for n in TIME_PARAMS}
        for eng, fields in TIME_PARAMS.items():
            for k in fields:
                info = models[eng].__fields__[k].field_info
                lo = info.ge if info.ge is not None else 0
                hi = info.le if info.le is not None else 10 ** 9
                d["engines"][eng][k] = int(min(hi, max(lo, round(d["engines"][eng][k] * f))))
    return AppConfig.parse_obj(d)


def merge_patch(cfg: AppConfig, patch: dict) -> AppConfig:
    """Return a new validated config with ``patch`` deep-merged in. Raises on invalid values."""

    def deep(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "eligibility":
                out[k] = deep(out[k], v)
            else:
                out[k] = v
        return out

    return AppConfig.parse_obj(deep(dump(cfg), patch))


def dump(model: BaseModel) -> dict:
    """JSON-safe dict of a config model (enums as values)."""
    return json.loads(model.json())


def load(data: dict) -> AppConfig:
    return AppConfig.parse_obj(data)
