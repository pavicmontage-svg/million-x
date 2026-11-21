"""PostgreSQL schema (SQLAlchemy 2.0 declarative).

Conventions
* All timestamps are timezone-aware UTC.
* Money/price/quantity columns are NUMERIC(30,12) in the database and float in Python;
  exchange-facing quantities are re-quantised with Decimal against venue filters.
* Trader metrics are nullable: NULL means UNKNOWN and is never replaced by an estimate.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, JSONType

NUM = Numeric(30, 12, asdecimal=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def TS(**kw):  # noqa: N802
    return mapped_column(DateTime(timezone=True), **kw)


# --------------------------------------------------------------------------- enums
class Market(str, enum.Enum):
    FOREX = "forex"
    CRYPTO = "crypto"


class TradingMode(str, enum.Enum):
    PAPER = "paper"   # internal simulator, clearly labelled SIMULATED
    DEMO = "demo"     # venue demo environment (e.g. Binance Demo Trading)
    LIVE = "live"     # real money, disabled by default


class PortfolioStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"                    # user paused
    HALTED_DAILY = "halted_daily"        # daily loss limit hit (auto-resets next UTC day)
    HALTED_WEEKLY = "halted_weekly"      # weekly loss limit hit (auto-resets next ISO week)
    REVIEW_REQUIRED = "review_required"  # max drawdown hit: manual review needed


class CopyCapability(str, enum.Enum):
    MONITOR_ONLY = "monitor_only"            # public data only, cannot be copied
    AUTHORIZED_SIGNALS = "authorized_signals"  # owner-authorized signal source
    UNAVAILABLE = "unavailable"


class TraderStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    MONITORING = "monitoring"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class SignalKind(str, enum.Enum):
    OPEN = "open"
    CLOSE = "close"
    PARTIAL_CLOSE = "partial_close"
    MODIFY = "modify"


class SignalStatus(str, enum.Enum):
    RECEIVED = "received"
    EXECUTED = "executed"
    PARTIALLY_EXECUTED = "partially_executed"
    REJECTED = "rejected"
    IGNORED = "ignored"
    FAILED = "failed"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"        # persisted, not yet sent
    SUBMITTED = "submitted"    # sent, awaiting ack
    ACKED = "acked"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"        # transport failure: must be reconciled by client id


class PositionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


# --------------------------------------------------------------------------- security
class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = TS(default=utcnow)
    last_seen_at: Mapped[datetime | None] = TS(nullable=True)
    fcm_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = TS()
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Credential(Base):
    """Encrypted API credential (AES-256-GCM, key from secrets manager)."""
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue: Mapped[str] = mapped_column(String(40))          # binance_spot, binance_usdm, ctrader, ...
    environment: Mapped[str] = mapped_column(String(10))    # demo | live
    label: Mapped[str] = mapped_column(String(120))
    ciphertext: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(16))    # first chars of sha256(api key) for display
    permissions: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = TS(default=utcnow)
    verified_at: Mapped[datetime | None] = TS(nullable=True)
    verify_result: Mapped[dict] = mapped_column(JSONType, default=dict)
    __table_args__ = (UniqueConstraint("venue", "environment", "label"),)


# --------------------------------------------------------------------------- traders
class Trader(Base):
    __tablename__ = "traders"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    market: Mapped[Market] = mapped_column(Enum(Market, native_enum=False))
    broker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instruments: Mapped[list] = mapped_column(JSONType, default=list)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TraderStatus] = mapped_column(Enum(TraderStatus, native_enum=False),
                                                 default=TraderStatus.CANDIDATE)
    copy_capability: Mapped[CopyCapability] = mapped_column(
        Enum(CopyCapability, native_enum=False), default=CopyCapability.MONITOR_ONLY)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    data_status: Mapped[str] = mapped_column(String(40), default="unverified")
    data_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Signal source wiring for authorized signals (e.g. webhook secret id, source account id).
    signal_source: Mapped[dict] = mapped_column(JSONType, default=dict)
    verified_at: Mapped[datetime | None] = TS(nullable=True)
    created_at: Mapped[datetime] = TS(default=utcnow)
    updated_at: Mapped[datetime] = TS(default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("platform", "name"),)

    metrics: Mapped[list["TraderMetricSnapshot"]] = relationship(back_populates="trader",
                                                                  cascade="all, delete-orphan")


class TraderMetricSnapshot(Base):
    """Point-in-time metric snapshot. Every metric is nullable (NULL == UNKNOWN)."""
    __tablename__ = "trader_metric_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[datetime] = TS(default=utcnow)
    source: Mapped[str] = mapped_column(String(80))   # e.g. "myfxbook_api", "computed_from_trades"
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    trader: Mapped[Trader] = relationship(back_populates="metrics")


class TraderEquityPoint(Base):
    __tablename__ = "trader_equity_points"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = TS()
    balance: Mapped[float | None] = mapped_column(NUM, nullable=True)
    equity: Mapped[float | None] = mapped_column(NUM, nullable=True)
    growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("trader_id", "ts"),)


class TraderTrade(Base):
    """Historical (closed) trade of a monitored trader, as published by the source."""
    __tablename__ = "trader_trades"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(4))  # buy / sell
    volume: Mapped[float | None] = mapped_column(NUM, nullable=True)
    open_time: Mapped[datetime] = TS()
    close_time: Mapped[datetime | None] = TS(nullable=True)
    open_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    close_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    sl: Mapped[float | None] = mapped_column(NUM, nullable=True)
    tp: Mapped[float | None] = mapped_column(NUM, nullable=True)
    profit: Mapped[float | None] = mapped_column(NUM, nullable=True)
    pips: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(NUM, nullable=True)
    swap: Mapped[float | None] = mapped_column(NUM, nullable=True)
    __table_args__ = (UniqueConstraint("trader_id", "external_id"),
                      Index("ix_trader_trades_trader_time", "trader_id", "open_time"))


class TraderScore(Base):
    __tablename__ = "trader_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[datetime] = TS(default=utcnow)
    engine_version: Mapped[str] = mapped_column(String(20))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL = not scorable (insufficient data)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    rejections: Mapped[list] = mapped_column(JSONType, default=list)
    flags: Mapped[list] = mapped_column(JSONType, default=list)
    categories: Mapped[list] = mapped_column(JSONType, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- portfolios
class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    market: Mapped[Market] = mapped_column(Enum(Market, native_enum=False))
    venue: Mapped[str] = mapped_column(String(40))           # paper_forex, paper_crypto, binance_usdm, ctrader ...
    mode: Mapped[TradingMode] = mapped_column(Enum(TradingMode, native_enum=False), default=TradingMode.PAPER)
    base_currency: Mapped[str] = mapped_column(String(10))
    initial_capital: Mapped[float] = mapped_column(NUM)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    status: Mapped[PortfolioStatus] = mapped_column(Enum(PortfolioStatus, native_enum=False),
                                                    default=PortfolioStatus.ACTIVE)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime | None] = TS(nullable=True)
    high_water_mark: Mapped[float] = mapped_column(NUM)
    risk_limits: Mapped[dict] = mapped_column(JSONType, default=dict)
    copy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = TS(default=utcnow)


class Allocation(Base):
    """A trader followed by a portfolio."""
    __tablename__ = "allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capital_fraction: Mapped[float] = mapped_column(Float, default=1.0)   # share of equity used for sizing
    max_risk_pct: Mapped[float] = mapped_column(Float, default=0.5)       # per-trader open-risk cap
    stopped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = TS(default=utcnow)
    __table_args__ = (UniqueConstraint("portfolio_id", "trader_id"),)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    source: Mapped[str] = mapped_column(String(60))
    trader_id: Mapped[int | None] = mapped_column(ForeignKey("traders.id"), nullable=True, index=True)
    market: Mapped[Market] = mapped_column(Enum(Market, native_enum=False))
    kind: Mapped[SignalKind] = mapped_column(Enum(SignalKind, native_enum=False))
    source_position_id: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str | None] = mapped_column(String(4), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    sl: Mapped[float | None] = mapped_column(NUM, nullable=True)
    tp: Mapped[float | None] = mapped_column(NUM, nullable=True)
    close_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_volume: Mapped[float | None] = mapped_column(NUM, nullable=True)
    source_risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_ts: Mapped[datetime] = TS()
    received_at: Mapped[datetime] = TS(default=utcnow)
    status: Mapped[SignalStatus] = mapped_column(Enum(SignalStatus, native_enum=False),
                                                 default=SignalStatus.RECEIVED)
    outcome: Mapped[list] = mapped_column(JSONType, default=list)   # per-portfolio decisions
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    venue_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(20))   # entry, exit, partial, stop_loss, take_profit
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[float] = mapped_column(NUM)
    price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, native_enum=False), default=OrderStatus.PENDING)
    filled_qty: Mapped[float] = mapped_column(NUM, default=0)
    avg_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    fee: Mapped[float] = mapped_column(NUM, default=0)
    fee_asset: Mapped[str | None] = mapped_column(String(12), nullable=True)
    expected_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    slippage: Mapped[float | None] = mapped_column(NUM, nullable=True)   # adverse price diff (account ccy per unit)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = TS(default=utcnow)
    updated_at: Mapped[datetime] = TS(default=utcnow, onupdate=utcnow)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    trader_id: Mapped[int | None] = mapped_column(ForeignKey("traders.id"), nullable=True)
    source_position_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    venue_position_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[float] = mapped_column(NUM)
    initial_quantity: Mapped[float] = mapped_column(NUM)
    entry_price: Mapped[float] = mapped_column(NUM)
    sl: Mapped[float | None] = mapped_column(NUM, nullable=True)
    tp: Mapped[float | None] = mapped_column(NUM, nullable=True)
    initial_risk: Mapped[float] = mapped_column(NUM, default=0)   # account ccy at entry
    status: Mapped[PositionStatus] = mapped_column(Enum(PositionStatus, native_enum=False),
                                                   default=PositionStatus.OPEN)
    opened_at: Mapped[datetime] = TS(default=utcnow)
    closed_at: Mapped[datetime | None] = TS(nullable=True)
    exit_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(NUM, default=0)
    fees: Mapped[float] = mapped_column(NUM, default=0)
    slippage_cost: Mapped[float] = mapped_column(NUM, default=0)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    copy_delay_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidation_price: Mapped[float | None] = mapped_column(NUM, nullable=True)
    leverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    protection: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_positions_open", "portfolio_id", "status"),)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    ts: Mapped[datetime] = TS(default=utcnow)
    balance: Mapped[float] = mapped_column(NUM)
    equity: Mapped[float] = mapped_column(NUM)
    margin_used: Mapped[float] = mapped_column(NUM, default=0)
    unrealized: Mapped[float] = mapped_column(NUM, default=0)
    open_risk: Mapped[float] = mapped_column(NUM, default=0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"), nullable=True)
    ts: Mapped[datetime] = TS(default=utcnow)
    kind: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)


class SystemState(Base):
    """Key/value store for global switches (kill switch, copy engine status, live approvals)."""
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = TS(default=utcnow, onupdate=utcnow)


class PaperAccount(Base):
    """Persistent state of the internal SIMULATED broker (survives restarts)."""
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue: Mapped[str] = mapped_column(String(40), unique=True)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = TS(default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- AI agents
class AgentPrompt(Base):
    __tablename__ = "agent_prompts"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = TS(default=utcnow)
    __table_args__ = (UniqueConstraint("agent", "version"),)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[str] = mapped_column(String(20))
    ts: Mapped[datetime] = TS(default=utcnow)
    subject: Mapped[str | None] = mapped_column(String(160), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSONType, default=dict)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------- ops
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = TS(default=utcnow)
    kind: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    pushed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper-evident)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = TS(default=utcnow)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = TS(default=utcnow)
    kind: Mapped[str] = mapped_column(String(30))  # backtest | walk_forward | monte_carlo
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
