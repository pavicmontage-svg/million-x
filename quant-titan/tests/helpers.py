from quanttitan.config import AppConfig
from quanttitan.core.types import ContractSpec, Decision, Direction, EngineSignal, Outcome, Position, Regime, RegimeState, TargetLevel, new_id
from quanttitan.orchestrator.decision import SymbolView

DAY0 = 1_767_225_600_000  # 2026-01-01T00:00Z
SPEC = ContractSpec("BTCUSDT", tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5.0, maint_margin_rate=0.004)


def signal(engine="vwap", direction=Direction.LONG, conf=80.0, entry=100.0, stop=98.0, tp1=104.0, setup="X", symbol="BTCUSDT"):
    return EngineSignal(engine, symbol, direction, conf, setup=setup, state="TEST", entry=entry, stop=stop,
                        targets=[TargetLevel(tp1, 0.5), TargetLevel(entry + (tp1 - entry) * 2, 1.0)], atr=1.0,
                        time_stop_bars=24, reasons=[f"{engine} test reason"], ts=DAY0)


def flat(engine, symbol="BTCUSDT"):
    return EngineSignal(engine, symbol, Direction.FLAT, 0.0, state="NONE", ts=DAY0)


def regime(reg=Regime.RANGE, permitted=None, mult=1.0, symbol="BTCUSDT"):
    cfg = AppConfig()
    return RegimeState(symbol, reg, permitted_engines=permitted if permitted is not None else cfg.engines.regime.eligibility[reg.value],
                       risk_multiplier=mult, reasons=["test"])


def view(signals: dict, reg=None, assess=None, symbol="BTCUSDT", spread=1.0, ts=DAY0):
    base = {n: flat(n, symbol) for n in ("vwap", "trend", "breakout", "order_flow")}
    base.update(signals)
    return SymbolView(symbol, ts, 100.0, base, reg or regime(symbol=symbol), assess, spread, 0.0, 5e7, 300_000)


def decision(symbol="BTCUSDT", direction=Direction.LONG, entry=100.0, stop=98.0, engine="vwap", conf=100.0, ts=DAY0):
    d = Decision(new_id("dec_"), ts, symbol, Outcome.APPROVE_LONG if direction is Direction.LONG else Outcome.APPROVE_SHORT,
                 engine=engine, setup="X", direction=direction, confidence=conf, entry=entry, stop=stop,
                 targets=[TargetLevel(entry + (entry - stop) * 2 * direction.sign, 1.0)])
    return d


def position(symbol="ETHUSDT", direction=Direction.LONG, entry=100.0, stop=98.0, qty=10.0, engine="trend", leverage=1.0):
    return Position(new_id("pos_"), symbol, direction, engine, "X", entry, qty, qty, stop, stop,
                    [TargetLevel(entry + 4 * direction.sign, 1.0)], DAY0, leverage=leverage)
