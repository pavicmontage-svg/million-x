"""Portfolio state with UTC day/week/month accounting used by the risk manager."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from ..core.types import Direction, Position, TradeRecord


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)


def period_keys(ms: int) -> tuple[str, str, str]:
    d = _utc(ms)
    iso = d.isocalendar()
    return d.strftime("%Y-%m-%d"), f"{iso[0]}-W{iso[1]:02d}", d.strftime("%Y-%m")


@dataclass
class PortfolioState:
    balance: float  # wallet balance incl. realised PnL, excl. unrealised
    positions: list[Position] = field(default_factory=list)
    prices: dict[str, float] = field(default_factory=dict)
    day_key: str = ""
    week_key: str = ""
    month_key: str = ""
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    month_start_equity: float = 0.0
    month_peak_equity: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    last_loss_ms: int = 0
    engine_day_r: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    correlations: dict[tuple[str, str], float] = field(default_factory=dict)
    suspended_engines: set[str] = field(default_factory=set)
    realized_today: float = 0.0
    trades_today: int = 0

    def __post_init__(self) -> None:
        e = self.balance
        self.day_start_equity = self.day_start_equity or e
        self.week_start_equity = self.week_start_equity or e
        self.month_start_equity = self.month_start_equity or e
        self.month_peak_equity = self.month_peak_equity or e
        self.peak_equity = self.peak_equity or e

    # ---------------------------------------------------------------- valuation
    def unrealized(self) -> float:
        return sum(p.unrealized(self.prices.get(p.symbol, p.entry_price)) for p in self.positions)

    @property
    def equity(self) -> float:
        return self.balance + self.unrealized()

    def open_risk(self) -> float:
        return sum(p.risk_usdt for p in self.positions)

    def gross_notional(self) -> float:
        return sum(p.notional(self.prices.get(p.symbol, p.entry_price)) for p in self.positions)

    def symbol_notional(self, symbol: str) -> float:
        return sum(p.notional(self.prices.get(p.symbol, p.entry_price)) for p in self.positions if p.symbol == symbol)

    def corr(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        return self.correlations.get((a, b), self.correlations.get((b, a), 0.0))

    def correlated_risk(self, symbol: str, direction: Direction, threshold: float) -> float:
        """Risk of open positions that move together with a new ``direction`` position on ``symbol``.
        A position counts if its signed correlation (corr × same/opposite direction) ≥ threshold."""
        tot = 0.0
        for p in self.positions:
            c = self.corr(symbol, p.symbol)
            signed = c * (1 if p.direction is direction else -1)
            if signed >= threshold:
                tot += p.risk_usdt
        return tot

    # ---------------------------------------------------------------- periods
    def roll(self, now_ms: int) -> list[str]:
        """Update period anchors. Returns which periods rolled over."""
        day, week, month = period_keys(now_ms)
        eq = self.equity
        rolled = []
        if day != self.day_key:
            if self.day_key:
                rolled.append("day")
            self.day_key, self.day_start_equity = day, eq
            self.engine_day_r = defaultdict(float)
            self.realized_today, self.trades_today = 0.0, 0
        if week != self.week_key:
            if self.week_key:
                rolled.append("week")
            self.week_key, self.week_start_equity = week, eq
        if month != self.month_key:
            if self.month_key:
                rolled.append("month")
            self.month_key, self.month_start_equity, self.month_peak_equity = month, eq, eq
        self.peak_equity = max(self.peak_equity, eq)
        self.month_peak_equity = max(self.month_peak_equity, eq)
        return rolled

    def daily_loss_pct(self) -> float:
        return max(0.0, (self.day_start_equity - self.equity) / self.day_start_equity * 100) if self.day_start_equity > 0 else 0.0

    def weekly_loss_pct(self) -> float:
        return max(0.0, (self.week_start_equity - self.equity) / self.week_start_equity * 100) if self.week_start_equity > 0 else 0.0

    def monthly_drawdown_pct(self) -> float:
        return max(0.0, (self.month_peak_equity - self.equity) / self.month_peak_equity * 100) if self.month_peak_equity > 0 else 0.0

    def drawdown_pct(self) -> float:
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100) if self.peak_equity > 0 else 0.0

    # ---------------------------------------------------------------- events
    def on_trade_closed(self, t: TradeRecord) -> None:
        if t.net_pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_ms = t.closed_ts
        elif t.net_pnl > 0:
            self.consecutive_losses = 0
        self.engine_day_r[t.engine] += t.r_multiple
        self.realized_today += t.net_pnl
        self.trades_today += 1

    def snapshot(self) -> dict:
        return {
            "balance": round(self.balance, 4), "equity": round(self.equity, 4), "unrealized": round(self.unrealized(), 4),
            "open_risk": round(self.open_risk(), 4), "gross_notional": round(self.gross_notional(), 4),
            "positions": len(self.positions), "daily_loss_pct": round(self.daily_loss_pct(), 3),
            "weekly_loss_pct": round(self.weekly_loss_pct(), 3), "monthly_drawdown_pct": round(self.monthly_drawdown_pct(), 3),
            "drawdown_pct": round(self.drawdown_pct(), 3), "consecutive_losses": self.consecutive_losses,
            "day_start_equity": round(self.day_start_equity, 4), "week_start_equity": round(self.week_start_equity, 4),
            "month_start_equity": round(self.month_start_equity, 4), "peak_equity": round(self.peak_equity, 4),
            "realized_today": round(self.realized_today, 4), "trades_today": self.trades_today,
            "engine_day_r": dict(self.engine_day_r), "suspended_engines": sorted(self.suspended_engines),
        }
