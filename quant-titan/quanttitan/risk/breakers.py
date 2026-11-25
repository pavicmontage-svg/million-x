"""Circuit breakers.

When a breaker trips: new trades are blocked, the event is recorded and notified, open-position
management continues (stops/targets stay active), and the configured reset condition must be met.

Breaker            trip condition                          reset
DAILY_LOSS         daily loss ≥ daily_loss_limit_pct        next UTC day (auto) or manual
WEEKLY_LOSS        weekly loss ≥ weekly_loss_limit_pct      next ISO week (auto) or manual
MONTHLY_DRAWDOWN   drawdown from month peak ≥ limit         manual only
CONSECUTIVE_LOSSES ≥ max_consecutive_losses losing trades   after cooldown minutes, and the counter resets
EMERGENCY_STOP     user/system emergency stop               manual only, with typed confirmation
SAFE_STATE         reconciliation / data / exchange failure cleared by the runtime when healthy, or manual
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

from ..config import RiskConfig
from ..core.types import now_ms
from .portfolio import PortfolioState, period_keys

MANUAL_ONLY = {"MONTHLY_DRAWDOWN", "EMERGENCY_STOP"}


@dataclass
class Breaker:
    name: str
    reason: str
    tripped_ms: int
    period_key: str = ""
    until_ms: int = 0
    severity: str = "critical"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CircuitBreakers:
    cfg: RiskConfig
    active: dict[str, Breaker] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    on_event: Callable[[str, Breaker], None] | None = None

    def _trip(self, name: str, reason: str, now: int, **kw) -> None:
        if name in self.active:
            return
        b = Breaker(name, reason, now, **kw)
        self.active[name] = b
        self.history.append({"event": "trip", **b.to_dict()})
        if self.on_event:
            self.on_event("trip", b)

    def _clear(self, name: str, now: int, how: str) -> None:
        b = self.active.pop(name, None)
        if b is None:
            return
        self.history.append({"event": "reset", "name": name, "how": how, "ts": now})
        if self.on_event:
            self.on_event("reset", b)

    def evaluate(self, pf: PortfolioState, now: int | None = None) -> list[str]:
        now = now or now_ms()
        day, week, _ = period_keys(now)
        cfg = self.cfg
        # automatic resets first
        if cfg.breaker_reset == "auto_next_period":
            if "DAILY_LOSS" in self.active and self.active["DAILY_LOSS"].period_key != day:
                self._clear("DAILY_LOSS", now, "new UTC day")
            if "WEEKLY_LOSS" in self.active and self.active["WEEKLY_LOSS"].period_key != week:
                self._clear("WEEKLY_LOSS", now, "new ISO week")
        cl = self.active.get("CONSECUTIVE_LOSSES")
        if cl and now >= cl.until_ms:
            pf.consecutive_losses = 0
            self._clear("CONSECUTIVE_LOSSES", now, "cooldown elapsed")
        # trips
        if pf.daily_loss_pct() >= cfg.daily_loss_limit_pct:
            self._trip("DAILY_LOSS", f"daily loss {pf.daily_loss_pct():.2f}% ≥ {cfg.daily_loss_limit_pct}%", now, period_key=day)
        if pf.weekly_loss_pct() >= cfg.weekly_loss_limit_pct:
            self._trip("WEEKLY_LOSS", f"weekly loss {pf.weekly_loss_pct():.2f}% ≥ {cfg.weekly_loss_limit_pct}%", now, period_key=week)
        if pf.monthly_drawdown_pct() >= cfg.monthly_drawdown_limit_pct:
            self._trip("MONTHLY_DRAWDOWN", f"monthly drawdown {pf.monthly_drawdown_pct():.2f}% ≥ {cfg.monthly_drawdown_limit_pct}%", now)
        if pf.consecutive_losses >= cfg.max_consecutive_losses and "CONSECUTIVE_LOSSES" not in self.active:
            self._trip("CONSECUTIVE_LOSSES", f"{pf.consecutive_losses} consecutive losses", now,
                       until_ms=now + cfg.consecutive_loss_cooldown_min * 60_000, severity="warning")
        if pf.equity < cfg.min_equity_usdt:
            self._trip("SAFE_STATE", f"equity {pf.equity:.2f} below minimum {cfg.min_equity_usdt}", now)
        return list(self.active)

    def emergency_stop(self, reason: str = "manual emergency stop") -> None:
        self._trip("EMERGENCY_STOP", reason, now_ms())

    def safe_state(self, reason: str) -> None:
        self._trip("SAFE_STATE", reason, now_ms())

    def clear_safe_state(self, how: str = "system healthy") -> None:
        self._clear("SAFE_STATE", now_ms(), how)

    def manual_reset(self, name: str, *, confirm: bool = False) -> bool:
        if name not in self.active:
            return False
        if name in MANUAL_ONLY and not confirm:
            raise PermissionError(f"{name} requires explicit confirmation to reset")
        self._clear(name, now_ms(), "manual reset")
        return True

    @property
    def blocked(self) -> bool:
        return bool(self.active)

    def to_dict(self) -> dict:
        return {"active": {k: v.to_dict() for k, v in self.active.items()}, "history": self.history[-100:]}
