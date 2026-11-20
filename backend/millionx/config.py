"""Central configuration for MILLION-X AI.

Values come from (in order of precedence): environment variables, a YAML file
(MX_CONFIG, default ./config.yaml) and the defaults below. Nothing in here is a
promise of profitability; these are safety limits and simulation assumptions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict, fields
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


@dataclass
class FeeModel:
    # Binance USDⓈ-M VIP0 defaults. The live adapter overwrites these with the
    # real per-symbol commission rate from GET /fapi/v1/commissionRate.
    maker: float = 0.0002
    taker: float = 0.0005
    slippage_bps: float = 2.0          # extra adverse slippage on market fills (bps)
    funding_rate_8h: float = 0.0001    # backtest estimate when no funding data is loaded


@dataclass
class RiskLimits:
    risk_per_trade: float = 0.005          # 0.5 % of equity at the stop (2.50 EUR on 500 EUR)
    max_daily_loss: float = 0.02           # stop opening trades after -2 % on the day (10 EUR on 500 EUR)
    max_daily_loss_eur: float = 10.0       # absolute daily loss limit in EUR (0 = off); the stricter limit wins
    max_total_drawdown: float = 0.20       # hard stop at -20 % from equity peak
    max_consecutive_losses: int = 4        # then cool down
    cooldown_minutes: int = 60
    loss_streak_risk_scale: float = 0.5    # risk multiplier after a loss streak
    drawdown_risk_scale_start: float = 0.08  # start scaling risk down from this DD
    max_open_positions: int = 2
    max_total_exposure: float = 3.0        # sum(notional) / equity
    max_leverage: int = 20                 # hard ceiling, never exchange max
    # leverage tiers by setup quality (5x weak / 10x standard / 15x very good / 20x exceptional)
    leverage_tiers: str = "5,10,15,20"
    min_liq_buffer: float = 3.0            # liquidation distance must be >= this x stop distance
    leverage_vol_target: float = 0.02      # lev <= target / ATR%(15m)
    max_fee_to_risk: float = 0.35          # reject if round-trip cost > 35 % of risk
    min_reward_risk: float = 1.2           # net of fees
    stale_data_seconds: int = 90
    disconnect_flatten: bool = False       # exchange-side stops protect positions anyway
    max_correlated_exposure: float = 1.5   # same-direction notional of correlated symbols / equity
    max_orders_per_minute: int = 3         # real entry orders
    max_orders_per_hour: int = 30
    max_slippage_bps: float = 15.0         # fill vs. price at decision; breach -> incident
    max_price_drift_bps: float = 10.0      # adverse move between signal and execution
    max_spread_bps: float = 3.0            # pre-trade liquidity
    min_depth_usdt: float = 50_000.0       # each side within 10 bps of mid
    max_unlock_hours: int = 12             # live authorisation lifetime


@dataclass
class ActivationCriteria:
    min_oos_trades: int = 100
    min_profit_factor: float = 1.15
    min_expectancy_r: float = 0.05
    max_drawdown: float = 0.20
    mc_dd_p95_max: float = 0.30
    min_positive_wf_windows: float = 0.6
    min_paper_trades: int = 50
    min_paper_pf: float = 1.05


@dataclass
class Settings:
    mode: str = "paper"                 # paper | demo | live
    execution: str = "manual"           # manual | auto (approval of real orders)
    capital_mode: str = "pro"           # experimental (4 EUR) | multi (40 EUR) | pro (500 EUR)
    experimental_capital_eur: float = 4.0
    multi_capital_eur: float = 40.0
    pro_capital_eur: float = 500.0
    eur_usdt: float = 1.08              # display only; overwritten from market if available
    goal_eur: float = 1_048_576.0
    quote_asset: str = "USDT"
    universe_size: int = 10
    universe_refresh_minutes: int = 60
    max_spread_bps: float = 3.0
    min_depth_usdt: float = 50_000.0    # within 10 bps of mid, each side
    excluded_bases: tuple = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "EUR", "USDP")
    timeframes: tuple = (1, 3, 5, 15)
    db_path: str = "data/millionx.db"
    data_dir: str = "data/klines"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    fees: FeeModel = field(default_factory=FeeModel)
    # Stops tighter than fees allow are widened (targets scaled with them) so that
    # round-trip costs are at most this share of the risk. 0 disables.
    target_fee_share: float = 0.25
    # Owner-selected timeframe (minutes) for all base bots; 0 = each bot's own default.
    strategy_timeframe: int = 0
    risk: RiskLimits = field(default_factory=RiskLimits)
    activation: ActivationCriteria = field(default_factory=ActivationCriteria)

    # ---- endpoints -------------------------------------------------------
    @property
    def rest_base(self) -> str:
        return "https://fapi.binance.com" if self.mode == "live" else "https://demo-fapi.binance.com"

    @property
    def ws_base(self) -> str:
        return "wss://fstream.binance.com" if self.mode == "live" else "wss://demo-fstream.binance.com"

    @property
    def market_data_base(self) -> str:
        # Paper mode can read public prices from the demo environment too.
        return os.environ.get("MX_MARKET_DATA_URL", self.rest_base)

    @property
    def starting_capital_eur(self) -> float:
        return {"experimental": self.experimental_capital_eur, "multi": self.multi_capital_eur}.get(
            self.capital_mode, self.pro_capital_eur)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rest_base"] = self.rest_base
        return d


def _merge(dc: Any, data: dict[str, Any]) -> None:
    for f in fields(dc):
        if f.name not in data:
            continue
        cur = getattr(dc, f.name)
        val = data[f.name]
        if hasattr(cur, "__dataclass_fields__") and isinstance(val, dict):
            _merge(cur, val)
        elif isinstance(cur, tuple) and isinstance(val, list):
            setattr(dc, f.name, tuple(val))
        else:
            setattr(dc, f.name, type(cur)(val) if cur is not None and not isinstance(cur, bool) else val)


def load_settings(path: str | None = None) -> Settings:
    s = Settings()
    path = path or os.environ.get("MX_CONFIG", "config.yaml")
    if yaml is not None and path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            _merge(s, yaml.safe_load(fh) or {})
    env_map = {
        "MX_MODE": ("mode", str),
        "MX_EXECUTION": ("execution", str),
        "MX_CAPITAL_MODE": ("capital_mode", str),
        "MX_DB_PATH": ("db_path", str),
        "MX_DATA_DIR": ("data_dir", str),
        "MX_API_PORT": ("api_port", int),
        "MX_API_HOST": ("api_host", str),
    }
    for env, (attr, typ) in env_map.items():
        if env in os.environ:
            setattr(s, attr, typ(os.environ[env]))
    if s.mode not in ("paper", "demo", "live"):
        raise ValueError(f"invalid mode {s.mode}")
    from .governance import validate_limits
    errs = validate_limits(s.risk.__dict__)
    if errs:
        raise ValueError("invalid risk limits in configuration: " + "; ".join(errs))
    return s
