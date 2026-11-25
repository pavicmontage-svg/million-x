"""The live/paper runtime.

Wires market data → engines → DecisionPipeline → (user approval in SEMI_AUTOMATIC) → order/position
managers → exchange, plus reconciliation, circuit breakers, notifications, persistence, the agents'
reports and the stat-arb pair book. Trading modes:

  PAPER           decisions executed automatically on the paper exchange (live data, simulated fills)
  SEMI_AUTOMATIC  approved decisions wait for the user (TTL); risk is re-checked at approval time
  AUTOMATIC       approved decisions executed automatically on the configured venue

The venue (``execution.exchange_env``) is PAPER unless the user enables TESTNET or LIVE. LIVE also
requires the deployment flag ``QT_ALLOW_LIVE=1``, stored API keys that pass the permission check
and a typed confirmation in the dashboard.
"""
from __future__ import annotations

import asyncio
import copy
import os
import time
from collections import deque

import numpy as np

from ..agents import ExecutionSpecialist, MarketAnalyst, PerformanceAuditor, PortfolioManager, QuantResearcher
from ..agents.portfolio_manager import correlation_matrix
from ..config import AppConfig, ExchangeEnv, TradingMode, dump, load, merge_patch
from ..core.logging import get_logger
from ..core.types import ContractSpec, Decision, Direction, Outcome, TradeRecord, new_id, now_ms
from ..data.binance_public import BinancePublic
from ..data.candles import Candles
from ..data.feeds import LiveFeed, ReplayFeed
from ..data.micro import micro_stats
from ..data.universe import select_universe
from ..engines import build_engines
from ..engines.base import MarketContext, fval
from ..engines.stat_arb import analyze_pair
from ..execution.exchange import Exchange, ExchangeError
from ..execution.order_manager import OrderManager
from ..execution.paper import PaperExchange
from ..execution.position_manager import PositionManager
from ..execution.reconciler import Reconciler
from ..notifications.notifier import Notifier
from ..orchestrator.decision import Orchestrator, SymbolView
from ..orchestrator.edge import EdgeBook
from ..orchestrator.pipeline import DecisionPipeline
from ..risk.manager import MarketCheck, RiskManager
from ..risk.portfolio import PortfolioState
from ..security.secrets import SecretStore
from ..storage.db import Database

log = get_logger("trader")


class Trader:
    def __init__(self, db: Database, secrets: SecretStore, *, replay_data: dict[str, Candles] | None = None,
                 replay_funding: dict | None = None) -> None:
        self.db = db
        self.secrets = secrets
        stored = db.kv_get("config")
        self.cfg: AppConfig = load(stored) if stored else AppConfig()
        if not stored:
            db.kv_set("config", dump(self.cfg))
            db.save_config_version(dump(self.cfg), "system", "initial defaults")
        self.replay_data = replay_data
        self.replay_funding = replay_funding
        self.edge = EdgeBook(self.cfg.orchestrator.edge_prior_strength, self.cfg.orchestrator.edge_prior_mean_r)
        self.edge.load(db.kv_get("edge_book", {}))
        self.engines = build_engines(self.cfg)
        self.risk = RiskManager(self.cfg)
        self.risk.breakers.on_event = self._on_breaker
        self.pm_agent = PortfolioManager(self.cfg)
        self.ex_agent = ExecutionSpecialist(self.cfg)
        self.analyst = MarketAnalyst()
        self.auditor = PerformanceAuditor()
        self.researcher = QuantResearcher(self.edge)
        self.orch = Orchestrator(self.cfg, self.edge)
        self.pipeline = DecisionPipeline(self.cfg, self.engines, self.orch, self.risk, self.pm_agent, self.ex_agent, self.analyst)
        self.notifier = Notifier(self.cfg.notifications, db, secrets.get)
        self.specs: dict[str, ContractSpec] = {}
        self.universe: list[dict] = []
        self.symbols: list[str] = list(self.cfg.universe.fixed_symbols)
        self.exchange: Exchange | None = None
        self.om: OrderManager | None = None
        self.pos: PositionManager | None = None
        self.reconciler: Reconciler | None = None
        self.feed: LiveFeed | ReplayFeed | None = None
        st = db.kv_get("portfolio_anchor") or {}
        self.pf = PortfolioState(float(st.get("balance", self.cfg.system.paper_starting_equity)))
        for k in ("day_key", "week_key", "month_key", "day_start_equity", "week_start_equity", "month_start_equity",
                  "month_peak_equity", "peak_equity", "consecutive_losses"):
            if k in st:
                setattr(self.pf, k, st[k])
        self.pending_user: dict[str, Decision] = {}
        self.last_views: dict[str, dict] = {}
        self.last_decisions: deque = deque(maxlen=300)
        self.pair_stats: list[dict] = []
        self.pairs_open: dict[str, dict] = {}
        self.listeners: list = []
        self.started_ms = now_ms()
        self.running = False
        self.health: dict = {"feed": "idle", "exchange": "idle", "last_bar_ms": 0}
        self._tasks: list[asyncio.Task] = []
        self._batch: dict[int, set] = {}
        self._batch_lock = asyncio.Lock()
        self.notifier.listeners.append(lambda row: self._broadcast({"type": "alert", "data": row}))

    # ------------------------------------------------------------------ helpers
    def _broadcast(self, msg: dict) -> None:
        for q in list(self.listeners):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    def _alert(self, kind: str, severity: str, message: str) -> None:
        self.notifier.notify(kind, severity, message)

    def _on_breaker(self, event: str, b) -> None:
        self.db.execute("INSERT INTO breaker_events (ts, event, name, reason) VALUES (?,?,?,?)", (now_ms(), event, b.name, b.reason))
        kind = {"DAILY_LOSS": "daily_loss_limit", "WEEKLY_LOSS": "weekly_loss_limit", "EMERGENCY_STOP": "emergency_stop"}.get(b.name, "circuit_breaker")
        self._alert(kind, "critical" if event == "trip" else "info", f"circuit breaker {b.name} {event}: {b.reason}")

    @property
    def venue(self) -> str:
        if self.cfg.system.trading_mode is TradingMode.PAPER:
            return "PAPER"
        return self.cfg.execution.exchange_env.value

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        await self._build_venue()
        await self._init_market()
        self._tasks = [asyncio.create_task(self._guard(self.feed.run(), "feed")),
                       asyncio.create_task(self._guard(self._housekeeping(), "housekeeping"))]
        if not isinstance(self.feed, ReplayFeed):
            self._tasks.append(asyncio.create_task(self._guard(self._universe_loop(), "universe")))
        self._alert("system", "info", f"runtime started: mode {self.cfg.system.trading_mode.value}, venue {self.venue}")

    async def stop(self) -> None:
        self.running = False
        if self.feed:
            await self.feed.stop()
        for t in self._tasks:
            t.cancel()
        self._persist()

    async def _guard(self, coro, name: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("task %s crashed", name)
            self._alert("exchange_failure" if name != "feed" else "websocket_failure", "critical", f"{name} task crashed: {e}")
            self.risk.breakers.safe_state(f"{name} task crashed")

    async def _build_venue(self) -> None:
        venue = self.venue
        if venue == "PAPER":
            self.exchange = PaperExchange(self.pf.balance, self.cfg.costs, self.specs)
        else:
            from ..execution.binance_futures import BinanceFutures
            if venue == "LIVE" and os.environ.get("QT_ALLOW_LIVE") != "1":
                raise PermissionError("LIVE trading is disabled at the deployment level (QT_ALLOW_LIVE != 1)")
            key, sec = self.secrets.get(f"binance_{venue.lower()}_key"), self.secrets.get(f"binance_{venue.lower()}_secret")
            if not key or not sec:
                raise PermissionError(f"no {venue} API key stored")
            self.exchange = BinanceFutures(key, sec, venue, conditional_endpoint=self.cfg.execution.conditional_endpoint,
                                           timeout=self.cfg.execution.request_timeout_s)
            await self.exchange.sync_time()
            await self.exchange.check_account_setup()
            bal = await self.exchange.balance()
            self.pf.balance = bal.wallet
        self.om = OrderManager(self.exchange, self.risk.signer, max_retries=self.cfg.execution.max_retries,
                               on_event=lambda row: self.db.execute(
                                   "INSERT INTO order_events (ts, client_id, event, data_json) VALUES (?,?,?,?)",
                                   (now_ms(), row.get("client_id", ""), row["event"], str(row))))
        self.pos = PositionManager(self.cfg, self.exchange, self.om, self.specs, on_closed=self._on_trade_closed,
                                   on_alert=self._alert, mode=venue.lower())
        self.reconciler = Reconciler(self.cfg, self.pos, self.om, self.risk.breakers, on_alert=self._alert)
        self.health["exchange"] = venue

    async def _init_market(self) -> None:
        if self.replay_data is not None or self.cfg.system.market_data_source == "replay":
            data = self.replay_data or {}
            self.symbols = sorted(data)
            for s, c in data.items():
                px = float(np.median(c.close[-50:]))
                step = 10 ** np.floor(np.log10(max(1e-8, 10 / px)))
                self.specs[s] = ContractSpec(s, tick_size=float(10 ** np.floor(np.log10(px * 1e-5))), step_size=float(step),
                                             min_qty=float(step), min_notional=5.0)
            self.feed = ReplayFeed(data, self._on_bar_close, funding=self.replay_funding, speed_s=self.cfg.system.replay_speed)
            return
        src = self.cfg.system.market_data_source
        client = BinancePublic(src)
        try:
            self.specs.update(await client.exchange_info())
            await self._refresh_universe(client)
        finally:
            await client.close()
        self.feed = LiveFeed(src, self.symbols, self._on_bar_close, tf=self.cfg.system.execution_timeframe)

    async def _refresh_universe(self, client: BinancePublic) -> None:
        rows = await select_universe(client, self.cfg.universe, self.specs)
        self.universe = [r.to_dict() for r in rows]
        chosen = [r.symbol for r in rows if r.eligible][: self.cfg.universe.size]
        held = {p.symbol for p in self.pos.positions} if self.pos else set()
        if chosen:
            self.symbols = sorted(set(chosen) | held)  # keep managing symbols we hold even if they drop out
        if self.feed and isinstance(self.feed, LiveFeed):
            self.feed.set_symbols(self.symbols)

    async def _universe_loop(self) -> None:
        while self.running:
            await asyncio.sleep(self.cfg.universe.refresh_minutes * 60)
            client = BinancePublic(self.cfg.system.market_data_source)
            try:
                await self._refresh_universe(client)
            except Exception as e:
                log.warning("universe refresh failed: %s", e)
            finally:
                await client.close()

    # ------------------------------------------------------------------ market events
    def _ctx(self, s: str) -> MarketContext | None:
        st = self.feed.state
        c = st.candles.get(s)
        if c is None or len(c) == 0:
            return None
        micro = micro_stats(st.book.get(s), st.recent_trades(s), levels=self.cfg.engines.order_flow.book_levels,
                            quantile=self.cfg.engines.order_flow.large_trade_quantile, ts=now_ms())
        age = 0.0 if isinstance(self.feed, ReplayFeed) else st.data_age_s(s)
        return MarketContext(s, c, spec=self.specs.get(s) or ContractSpec(s), micro=micro, funding_rate=st.funding.get(s, 0.0),
                             mark_price=st.mark.get(s, float(c.close[-1])), data_age_s=age)

    def _push_paper_market(self, s: str, ctx: MarketContext) -> None:
        if isinstance(self.exchange, PaperExchange):
            c = ctx.candles
            st = self.feed.state
            self.exchange.update_market(s, last=float(c.close[-1]), mark=st.mark.get(s), book=st.book.get(s),
                                        ts=int(c.close_ts[-1]), funding_rate=st.funding.get(s),
                                        next_funding_ms=st.next_funding.get(s), atr=None,
                                        high=float(c.high[-1]), low=float(c.low[-1]))

    async def _on_bar_close(self, symbol: str, close_ts: int) -> None:
        async with self._batch_lock:
            batch = self._batch.setdefault(close_ts, set())
            batch.add(symbol)
            ready = set(self.symbols) <= batch or isinstance(self.feed, ReplayFeed) and set(self.feed.symbols) <= batch
        self.health["last_bar_ms"] = close_ts
        ctx = self._ctx(symbol)
        if ctx is not None:
            self._push_paper_market(symbol, ctx)
            await self._manage_positions(symbol, ctx)
        stale = not isinstance(self.feed, ReplayFeed) and now_ms() - close_ts > 2 * (ctx.candles.tf_ms if ctx else 300_000)
        if ready and stale:  # backfilled bar after a reconnect: manage positions, but never open trades on old data
            self._batch.pop(close_ts, None)
            return
        if ready:
            self._batch.pop(close_ts, None)
            for old in [k for k in self._batch if k < close_ts - 3_600_000]:
                self._batch.pop(old, None)
            await self._decide(close_ts)
        elif not isinstance(self.feed, ReplayFeed):
            # do not wait forever for a lagging symbol
            asyncio.get_running_loop().call_later(20, lambda: asyncio.ensure_future(self._flush(close_ts)))

    async def _flush(self, close_ts: int) -> None:
        if close_ts in self._batch:
            self._batch.pop(close_ts, None)
            await self._decide(close_ts)

    async def _manage_positions(self, symbol: str, ctx: MarketContext) -> None:
        if not self.pos:
            return
        try:
            await self.pos.sync()  # closed trades are booked through _on_trade_closed
            c = ctx.candles
            feats = self.engines["trend"].features(ctx)
            atr = fval(feats, "atr", len(c) - 1, 0.0)
            await self.pos.on_bar(symbol, float(c.high[-1]), float(c.low[-1]), float(c.close[-1]), atr)
            for p in [p for p in self.pos.positions if p.symbol == symbol]:
                exit_reason = None
                if p.engine == "trend":
                    exit_reason = self.engines["trend"].exit_signal(feats, len(c) - 1, p.direction, float(c.close[-1]))
                rf = self.engines["regime"].features(ctx)
                regime = self.engines["regime"].classify(ctx, rf, len(c) - 1)
                v = SymbolView(symbol, int(c.close_ts[-1]), float(c.close[-1]), {}, regime)
                d = self.orch.review_position(p, v, exit_reason)
                if d is not None:
                    self._record_decision(d)
                    await self.pos.close(p.id, "signal_exit" if exit_reason else "regime_exit")
            # pair trades
            await self._manage_pairs()
        except ExchangeError as e:
            self._alert("exchange_failure", "warning", f"position management {symbol}: {e}")

    def _on_trade_closed(self, tr: TradeRecord) -> None:
        if isinstance(self.exchange, PaperExchange):
            self.pf.balance = self.exchange.wallet
        else:
            self.pf.balance += tr.net_pnl
        self.pf.on_trade_closed(tr)
        self.db.save_trade(tr)
        src = {"paper": "paper", "testnet": "testnet", "live": "live"}.get(tr.mode)
        if src and tr.setup and not tr.group_id:
            self.edge.record(tr.engine, tr.setup, tr.direction, tr.r_multiple, src)
        kind = "stop_loss" if "stop" in tr.exit_reason else "take_profit" if "take_profit" in tr.exit_reason else "trade_closed"
        self._alert(kind, "info", f"{tr.symbol} {tr.direction} {tr.engine} closed ({tr.exit_reason}): {tr.net_pnl:+.2f} USDT, {tr.r_multiple:+.2f}R")
        self._broadcast({"type": "trade", "data": tr.to_dict()})

    # ------------------------------------------------------------------ decisions
    def _record_decision(self, d: Decision) -> None:
        self.db.save_decision(d)
        self.last_decisions.appendleft({k: v for k, v in d.to_dict().items() if k != "approval"})
        self._broadcast({"type": "decision", "data": self.last_decisions[0]})

    async def _decide(self, close_ts: int) -> None:
        self.pf.roll(close_ts if isinstance(self.feed, ReplayFeed) else now_ms())
        if isinstance(self.exchange, PaperExchange):
            self.pf.balance = self.exchange.wallet
        self.pf.positions = self.pos.positions if self.pos else []
        st = self.feed.state
        for s in self.symbols:
            if s in st.last:
                self.pf.prices[s] = st.last[s]
        views, books = [], {}
        for s in self.symbols:
            ctx = self._ctx(s)
            if ctx is None or len(ctx.candles) < 300:
                continue
            feats = self.pipeline.features(ctx)
            v = self.pipeline.symbol_view(ctx, feats, len(ctx.candles) - 1)
            self.last_views[s] = {"regime": v.regime.to_dict(), "signals": {n: sig.to_dict() for n, sig in v.signals.items()},
                                  "price": v.price, "ts": v.ts, "spread_bps": v.spread_bps, "funding": v.funding_rate,
                                  "micro": ctx.micro.__dict__ if ctx.micro else None}
            if any(p.symbol == s for p in self.pf.positions):
                continue
            views.append(v)
            books[s] = st.book.get(s)
        regimes = {s: self.engines["regime"].last_states[s] for s in self.symbols if s in self.engines["regime"].last_states}
        if len(self.symbols) > 1 and int(close_ts / 300_000) % 12 == 0:
            self.pf.correlations = correlation_matrix({s: st.candles[s] for s in self.symbols if s in st.candles})
        avg_corr = float(np.mean(list(self.pf.correlations.values()))) if self.pf.correlations else None
        market = self.analyst.analyze(regimes, avg_corr)
        dislocated = [s for s, r in regimes.items() if r.regime.value == "MARKET_DISLOCATION"]
        for s in dislocated:
            self._alert("market_dislocation", "warning", f"{s}: market dislocation — {'; '.join(regimes[s].reasons)}")
        if not views:
            return
        decisions = self.pipeline.decide(views, self.pf, self.specs, books=books, trading_paused=self.cfg.system.trading_paused,
                                         market_block=market.get("block_all", False))
        for d in decisions:
            if d.outcome is not Outcome.WAIT or len(self.last_decisions) < 50:
                self._record_decision(d)
            if d.outcome in (Outcome.APPROVE_LONG, Outcome.APPROVE_SHORT):
                await self._handle_approved(d)
            elif d.risk_verdict == "VETO":
                self._alert("risk_violation", "info", f"{d.symbol}: risk veto — {'; '.join(d.risk_reasons[:2])}")
        await self._scan_pairs(close_ts)

    async def _handle_approved(self, d: Decision) -> None:
        mode = self.cfg.system.trading_mode
        if mode is TradingMode.SEMI_AUTOMATIC:
            d.status = "pending_user"
            self.pending_user[d.id] = d
            self._record_decision(d)
            self._alert("decision_pending", "warning", f"{d.symbol} {d.direction.value} via {d.engine} awaits your approval "
                        f"({self.cfg.orchestrator.semi_auto_ttl_s}s)")
            return
        await self._execute(d)

    async def _execute(self, d: Decision) -> bool:
        if not self.pos:
            return False
        ref = self.feed.state.last.get(d.symbol, d.entry)
        try:
            p = await self.pos.open(d, ref_price=ref, order_type=d.costs.get("order_type", "MARKET"), leverage=d.costs.get("leverage", 1.0))
        except Exception as e:
            d.status = "failed"
            self._record_decision(d)
            self._alert("exchange_failure", "warning", f"{d.symbol}: entry failed — {e}")
            return False
        d.status = "executed" if p else "not_filled"
        self._record_decision(d)
        if p:
            self.pf.positions = self.pos.positions
            self._alert("trade_opened", "info", f"{d.symbol} {d.direction.value} {p.qty:g} @ {p.entry_price:.6g} via {d.engine} "
                        f"(stop {p.stop:.6g}, risk {d.risk_usdt:.2f} USDT)")
        return bool(p)

    async def approve_pending(self, decision_id: str) -> dict:
        d = self.pending_user.pop(decision_id, None)
        if d is None:
            raise KeyError("no such pending decision")
        if now_ms() - d.ts > self.cfg.orchestrator.semi_auto_ttl_s * 1000 and not isinstance(self.feed, ReplayFeed):
            d.status = "expired"
            self._record_decision(d)
            return {"ok": False, "reason": "expired"}
        # risk re-check with current state (the earlier approval is replaced)
        ctx = self._ctx(d.symbol)
        spec = self.specs.get(d.symbol) or ContractSpec(d.symbol)
        mkt = MarketCheck(spread_bps=ctx.micro.spread_bps if ctx and ctx.micro else None, funding_rate=ctx.funding_rate if ctx else 0.0,
                          data_age_s=ctx.data_age_s if ctx else 1e9, atr=d.signal.atr if d.signal else 0.0,
                          bar_quote_volume=float(ctx.candles.quote_volume[-1]) if ctx else 0.0,
                          regime_multiplier=self.engines["regime"].last_states.get(d.symbol).risk_multiplier
                          if d.symbol in self.engines["regime"].last_states else 0.0)
        self.pf.positions = self.pos.positions
        d.id = new_id("dec_")  # fresh id → fresh single-use approval
        v = self.risk.review(d, self.pf, spec, mkt, trading_paused=self.cfg.system.trading_paused)
        if not v.approved:
            d.status, d.risk_verdict, d.risk_reasons = "rejected_at_approval", "VETO", v.reasons
            self._record_decision(d)
            return {"ok": False, "reason": "risk veto at approval time", "details": v.reasons}
        d.qty, d.approval = v.qty, v.approval
        ok = await self._execute(d)
        return {"ok": ok}

    def reject_pending(self, decision_id: str) -> None:
        d = self.pending_user.pop(decision_id, None)
        if d:
            d.status = "rejected_by_user"
            self._record_decision(d)

    # ------------------------------------------------------------------ statistical arbitrage
    async def _scan_pairs(self, close_ts: int) -> None:
        cfg = self.cfg.engines.stat_arb
        if not cfg.enabled or int(close_ts / 300_000) % 12 != 0:
            return
        eng = self.engines["stat_arb"]
        data = {s: self.feed.state.candles[s] for s in self.symbols if s in self.feed.state.candles}
        stats = eng.scan(data)
        self.pair_stats = [p.to_dict() for p in stats[:20]]
        if len(self.pairs_open) >= cfg.max_pairs:
            return
        for st in stats:
            if not st.tradeable or f"{st.y}/{st.x}" in self.pairs_open:
                continue
            sig = eng.finalize(MarketContext(f"{st.y}/{st.x}", Candles(st.y, "5m")), eng.evaluate_pair(st, close_ts))
            if sig.direction is Direction.FLAT:
                continue
            regs = self.engines["regime"].last_states
            if any("stat_arb" not in (regs[s].permitted_engines if s in regs else []) for s in (st.y, st.x)):
                continue
            await self._open_pair(sig)
            if len(self.pairs_open) >= cfg.max_pairs:
                break

    async def _open_pair(self, sig) -> None:
        cfg = self.cfg.engines.stat_arb
        pr = sig.pair
        budget = self.pf.equity * cfg.max_pair_risk_pct / 100
        z_risk = (pr["stop_z"] - abs(pr["z"])) * pr["std"]  # log-spread move to the stop
        if z_risk <= 0:
            return
        notional_y = budget / z_risk
        gid = new_id("pair_")
        legs = []
        for leg in pr["legs"]:
            s = leg["symbol"]
            px = self.feed.state.last.get(s)
            spec = self.specs.get(s) or ContractSpec(s)
            if not px:
                return
            d = Direction(leg["direction"])
            notional = notional_y * leg["weight"]
            stop = px * (1 - d.sign * self.cfg.risk.max_stop_distance_pct / 100)  # catastrophic exchange stop per leg
            dec = Decision(new_id("dec_"), now_ms(), s, Outcome.APPROVE_LONG if d is Direction.LONG else Outcome.APPROVE_SHORT,
                           engine="stat_arb", setup="PAIR_REVERSION", direction=d, confidence=sig.confidence, entry=px, stop=stop)
            mkt = MarketCheck(spread_bps=None, funding_rate=self.feed.state.funding.get(s, 0.0), atr=0.0,
                              regime_multiplier=1.0)
            leg_budget = notional * self.cfg.risk.max_stop_distance_pct / 100
            v = self.risk.review(dec, self.pf, spec, mkt, trading_paused=self.cfg.system.trading_paused, pair_trade=True,
                                 budget_override=leg_budget)
            if not v.approved:
                self._alert("risk_violation", "info", f"pair {sig.symbol} leg {s} vetoed: {'; '.join(v.reasons[:2])}")
                return
            dec.qty, dec.approval, dec.costs = v.qty, v.approval, {"leverage": v.leverage}
            legs.append(dec)
        opened = []
        for dec in legs:
            p = await self.pos.open(dec, ref_price=self.feed.state.last[dec.symbol], leverage=dec.costs["leverage"], group_id=gid)
            if p is None:
                for q in opened:  # never leave a naked leg
                    await self.pos.close(q.id, "pair_leg_failed")
                self._alert("exchange_failure", "warning", f"pair {sig.symbol}: leg failed, pair unwound")
                return
            opened.append(p)
        self.pairs_open[sig.symbol] = {"group": gid, "pair": pr, "opened_ms": now_ms(), "bars": 0, "positions": [p.id for p in opened]}
        self._alert("trade_opened", "info", f"pair {sig.symbol} {sig.direction.value} spread at z={pr['z']:+.2f}")

    async def _manage_pairs(self) -> None:
        for key, info in list(self.pairs_open.items()):
            pr = info["pair"]
            y, x = self.feed.state.candles.get(pr["y"]), self.feed.state.candles.get(pr["x"])
            if y is None or x is None:
                continue
            s = np.log(y.close[-1]) - (pr["alpha"] + pr["beta"] * np.log(x.close[-1]))
            z = (s - pr["mean"]) / pr["std"]
            info["bars"] += 1
            reason = None
            if abs(z) <= pr["exit_z"]:
                reason = "pair_mean_reversion"
            elif abs(z) >= pr["stop_z"]:
                reason = "pair_stop_z"
            elif info["bars"] >= max(1, pr["time_stop_bars"]) * 3:  # pair stats are on 15m bars; runtime ticks 5m
                reason = "pair_time_stop"
            alive = [pid for pid in info["positions"] if pid in self.pos.managed]
            if reason or len(alive) < len(info["positions"]):
                for pid in alive:
                    await self.pos.close(pid, reason or "pair_leg_closed")
                self.pairs_open.pop(key, None)

    # ------------------------------------------------------------------ control actions
    async def emergency_stop(self, flatten: bool, actor: str) -> dict:
        self.risk.breakers.emergency_stop(f"emergency stop by {actor}")
        self.cfg.system.trading_paused = True
        self.pending_user.clear()
        closed = []
        if flatten and self.pos:
            closed = [t.to_dict() for t in await self.pos.close_all("emergency_stop")]
        self._persist()
        return {"ok": True, "flattened": len(closed)}

    async def close_position(self, pid: str) -> dict:
        tr = await self.pos.close(pid, "manual_close")
        return {"ok": tr is not None}

    async def close_all(self) -> dict:
        trs = await self.pos.close_all("manual_close_all")
        return {"ok": True, "closed": len(trs)}

    def update_config(self, patch: dict, actor: str, note: str = "") -> AppConfig:
        new = merge_patch(self.cfg, patch)
        if new.execution.exchange_env is ExchangeEnv.LIVE and self.cfg.execution.exchange_env is not ExchangeEnv.LIVE:
            raise PermissionError("switching to LIVE requires the dedicated /api/live/enable flow")
        venue_changed = (new.system.trading_mode, new.execution.exchange_env) != (self.cfg.system.trading_mode, self.cfg.execution.exchange_env)
        if venue_changed and self.pos and self.pos.positions:
            raise PermissionError("close all positions before changing trading mode or venue")
        self._apply(new)
        self.db.kv_set("config", dump(new))
        self.db.save_config_version(dump(new), actor, note)
        return new

    def _apply(self, new: AppConfig) -> None:
        self.cfg = new
        self.pipeline.update_config(new)
        self.notifier.cfg = new.notifications
        if self.pos:
            self.pos.cfg = new
        if self.reconciler:
            self.reconciler.cfg = new

    async def restart_venue(self) -> None:
        """Rebuild the venue after a mode change (no open positions allowed)."""
        was = self.running
        await self.stop()
        self.running = False
        if was:
            await self.start()

    # ------------------------------------------------------------------ housekeeping
    async def _housekeeping(self) -> None:
        tick = 0
        while self.running:
            await asyncio.sleep(5)
            tick += 1
            try:
                # data freshness → safe state
                if not isinstance(self.feed, ReplayFeed):
                    ages = [self.feed.state.data_age_s(s) for s in self.symbols]
                    stale = [s for s, a in zip(self.symbols, ages) if a > self.cfg.risk.max_data_staleness_s]
                    if stale and len(stale) == len(self.symbols):
                        if "SAFE_STATE" not in self.risk.breakers.active:
                            self.risk.breakers.safe_state("market data stale for all symbols")
                            self._alert("websocket_failure", "critical", f"market data stale: {', '.join(stale[:5])}")
                    elif "SAFE_STATE" in self.risk.breakers.active and self.risk.breakers.active["SAFE_STATE"].reason.startswith("market data"):
                        self.risk.breakers.clear_safe_state("market data fresh again")
                self.health["feed"] = self.feed.state.status if self.feed else "idle"
                # expire pending user decisions
                for did, d in list(self.pending_user.items()):
                    if now_ms() - d.ts > self.cfg.orchestrator.semi_auto_ttl_s * 1000 and not isinstance(self.feed, ReplayFeed):
                        d.status = "expired"
                        self.pending_user.pop(did, None)
                        self._record_decision(d)
                if self.reconciler and tick % max(1, self.cfg.execution.reconcile_interval_s // 5) == 0:
                    rep = await self.reconciler.run()
                    self.health["reconcile"] = rep.to_dict()
                if tick % 12 == 0:
                    eq = self.pf.equity
                    self.db.save_equity(now_ms(), eq, self.pf.balance, self.pf.unrealized(), self.pf.open_risk())
                    self._persist()
                    self._broadcast({"type": "portfolio", "data": self.pf.snapshot()})
            except Exception as e:
                log.exception("housekeeping")
                self._alert("exchange_failure", "warning", f"housekeeping error: {e}")

    def _persist(self) -> None:
        pf = self.pf
        self.db.kv_set("portfolio_anchor", {k: getattr(pf, k) for k in (
            "balance", "day_key", "week_key", "month_key", "day_start_equity", "week_start_equity", "month_start_equity",
            "month_peak_equity", "peak_equity", "consecutive_losses")})
        self.db.kv_set("edge_book", self.edge.dump())

    # ------------------------------------------------------------------ reporting
    def status(self) -> dict:
        pf = self.pf
        trades = self.db.trades(2000)
        now = now_ms()
        def pnl_since(ms):
            return round(sum(t["net_pnl"] for t in trades if t["closed_ts"] >= ms), 4)
        day0 = (now // 86_400_000) * 86_400_000
        regimes = self.engines["regime"].last_states
        return {
            "ts": now, "running": self.running, "mode": self.cfg.system.trading_mode.value, "venue": self.venue,
            "paused": self.cfg.system.trading_paused, "portfolio": pf.snapshot(),
            "pnl": {"realized_total": pnl_since(0), "daily": pnl_since(day0), "weekly": pnl_since(now - 7 * 86_400_000),
                    "monthly": pnl_since(now - 30 * 86_400_000), "unrealized": round(pf.unrealized(), 4)},
            "positions": [p.to_dict() | {"mark": pf.prices.get(p.symbol), "unrealized": p.unrealized(pf.prices.get(p.symbol, p.entry_price))}
                          for p in (self.pos.positions if self.pos else [])],
            "breakers": self.risk.breakers.to_dict()["active"], "symbols": self.symbols,
            "regimes": {s: r.regime.value for s, r in regimes.items()},
            "market": self.analyst.last.get("summary", ""), "health": self.health,
            "feed": {"mode": self.feed.state.mode if self.feed else "", "status": self.feed.state.status if self.feed else "idle",
                     "errors": self.feed.state.errors if self.feed else 0},
            "pending_decisions": [d.to_dict() | {"approval": None} for d in self.pending_user.values()],
            "trade_count": len(trades), "active_engines": [n for n, e in self.engines.items() if e.enabled],
            "pairs_open": list(self.pairs_open),
        }

    def agents_report(self) -> dict:
        trades = [TradeRecord(**{k: v for k, v in t.items()}) for t in self.db.trades(5000)]
        return {
            "quant_researcher": self.researcher.review(self.cfg.orchestrator.min_validated_trades),
            "market_analyst": self.analyst.last or {"summary": "no data yet"},
            "execution_specialist": self.ex_agent.report(),
            "portfolio_manager": self.pm_agent.report(self.pf),
            "risk_manager": self.risk.report(self.pf),
            "performance_auditor": self.auditor.audit(trades, self.cfg.system.paper_starting_equity),
        }
