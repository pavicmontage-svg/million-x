"""Quantitative trader scoring (0-100) with explanations.

Weights (sum 100): drawdown 25, risk-adjusted return 20, profit factor 15, consistency 15,
history 10, execution quality 10, transparency 5.

A component whose inputs are UNKNOWN is *excluded* (never guessed). The score is renormalised
over the covered weight and reported with `coverage`. Below 60% coverage the trader is
NOT SCORABLE (score = None). Screening preferences (12 months, <=10% DD, PF >= 1.5) are
eligibility criteria for copying, not guarantees of future profitability.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .behaviour import Finding

ENGINE_VERSION = "1.0.0"
WEIGHTS = {"drawdown": 25, "risk_adjusted_return": 20, "profit_factor": 15, "consistency": 15,
           "history": 10, "execution_quality": 10, "transparency": 5}
MIN_COVERAGE = 0.6
PREF_MIN_MONTHS, PREF_MAX_DD, PREF_MIN_PF = 12, 10.0, 1.5


def _lin(x: float, lo: float, hi: float) -> float:
    """0 at lo, 100 at hi (lo may be greater than hi for 'lower is better')."""
    if hi == lo:
        return 0.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


@dataclass
class ScoreResult:
    score: float | None
    rejected: bool
    eligible: bool
    coverage: float
    components: dict = field(default_factory=dict)
    rejections: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    explanation: str = ""
    version: str = ENGINE_VERSION


def score_trader(m: dict, findings: list[Finding], execution: dict | None = None,
                 data_source: str = "unknown", description: str | None = None) -> ScoreResult:
    comps: dict[str, dict] = {}

    def put(name, value, why):
        comps[name] = {"score": None if value is None else round(value, 1), "weight": WEIGHTS[name], "why": why}

    dd = m.get("max_equity_drawdown_pct")
    dd_kind = "equity"
    if dd is None:
        dd, dd_kind = m.get("max_balance_drawdown_pct"), "balance"
    if dd is None:
        put("drawdown", None, "maximum drawdown UNKNOWN")
    else:
        s = _lin(dd, 25, 0)
        if dd_kind == "balance":
            s *= 0.8  # balance drawdown understates risk: penalise
        put("drawdown", s, f"max {dd_kind} drawdown {dd:.2f}% (0% -> 100 pts, 25% -> 0 pts"
                           f"{'; balance-only data -20%' if dd_kind == 'balance' else ''})")

    sh, so = m.get("sharpe"), m.get("sortino")
    if sh is None and so is None:
        put("risk_adjusted_return", None, "Sharpe/Sortino UNKNOWN (needs >= 20 daily returns)")
    else:
        parts = [x for x in (_lin(sh, 0, 2.5) if sh is not None else None,
                             _lin(so, 0, 3.5) if so is not None else None) if x is not None]
        cal = m.get("calmar")
        if cal is not None:
            parts.append(_lin(cal, 0, 3))
        put("risk_adjusted_return", sum(parts) / len(parts),
            f"Sharpe {_f(sh)}, Sortino {_f(so)}, Calmar {_f(cal)}")

    pf = m.get("profit_factor")
    put("profit_factor", None if pf is None else _lin(pf, 1.0, 2.5),
        "profit factor UNKNOWN" if pf is None else f"profit factor {pf:.2f} (1.0 -> 0, 2.5 -> 100)")

    mc, vol = m.get("monthly_consistency"), m.get("return_volatility_ann")
    if mc is None and vol is None:
        put("consistency", None, "monthly consistency UNKNOWN")
    else:
        parts = []
        if mc is not None:
            parts.append(_lin(mc, 40, 90))
        if vol is not None:
            parts.append(_lin(vol, 40, 5))
        put("consistency", sum(parts) / len(parts),
            f"positive months {_f(mc)}%, annualised volatility {_f(vol)}%")

    months, n = m.get("months_of_history"), m.get("total_trades")
    if months is None:
        put("history", None, "account history length UNKNOWN")
    else:
        s = _lin(months, 0, 24)
        if n is not None and n < 50:
            s *= max(0.3, n / 50)
        put("history", s, f"{months:.1f} months, {n if n is not None else 'UNKNOWN'} closed trades")

    if execution and execution.get("copied_trades", 0) >= 10:
        slip_r = execution.get("avg_slippage_r")   # slippage in R units
        delay = execution.get("median_delay_ms")
        fill = execution.get("fill_rate")
        parts = []
        if slip_r is not None:
            parts.append(_lin(slip_r, 0.2, 0.0))
        if delay is not None:
            parts.append(_lin(delay, 5000, 300))
        if fill is not None:
            parts.append(_lin(fill, 0.8, 1.0))
        put("execution_quality", sum(parts) / len(parts) if parts else None,
            f"from {execution['copied_trades']} of our own copied trades: slippage {_f(slip_r)}R, "
            f"median delay {_f(delay)} ms, fill rate {_f(fill)}")
    else:
        put("execution_quality", None, "no copy-execution history yet (UNKNOWN until >= 10 copied trades)")

    t = 0.0
    why = []
    if data_source in ("myfxbook_api", "darwinex_info_api", "ctrader_open_api", "mt5_bridge", "binance_api"):
        t += 40; why.append(f"official source ({data_source})")
    elif data_source == "manual_import":
        t += 15; why.append("manually imported history")
    if m.get("max_equity_drawdown_pct") is not None:
        t += 25; why.append("equity drawdown published")
    if m.get("total_trades"):
        t += 20; why.append("trade-level history")
    slu = m.get("sl_usage_rate")
    if slu is not None:
        t += 15 * slu / 100; why.append(f"stop-loss on {slu:.0f}% of trades")
    t -= 15 * sum(1 for f in findings if f.severity == "warn")
    put("transparency", max(0.0, min(100.0, t)), ", ".join(why) or "no verifiable data")

    covered = sum(c["weight"] for c in comps.values() if c["score"] is not None)
    coverage = covered / 100
    score = None
    if coverage >= MIN_COVERAGE:
        score = round(sum(c["score"] * c["weight"] for c in comps.values() if c["score"] is not None) / covered, 1)

    rejections = [{"code": f.code, "message": f.message, "evidence": f.evidence} for f in findings if f.severity == "reject"]
    flags = [{"code": f.code, "message": f.message} for f in findings if f.severity == "warn"]
    prefs = []
    if months is None or months < PREF_MIN_MONTHS:
        prefs.append(f"history below {PREF_MIN_MONTHS} months ({_f(months)})")
    if dd is None or dd > PREF_MAX_DD:
        prefs.append(f"max drawdown above {PREF_MAX_DD}% or UNKNOWN ({_f(dd)})")
    if pf is None or pf < PREF_MIN_PF:
        prefs.append(f"profit factor below {PREF_MIN_PF} or UNKNOWN ({_f(pf)})")
    flags += [{"code": "below_preference", "message": p} for p in prefs]

    cats = categorise(m, description)
    rejected = bool(rejections)
    eligible = (not rejected) and score is not None and score >= 60 and not prefs

    lines = []
    if rejected:
        lines.append("REJECTED: " + "; ".join(r["message"] for r in rejections) + ".")
    if score is None:
        lines.append(f"NOT SCORABLE: only {coverage:.0%} of the scoring weight has verified data "
                     f"(minimum {MIN_COVERAGE:.0%}).")
    else:
        lines.append(f"Score {score}/100 over {coverage:.0%} data coverage.")
    best = sorted([(k, c) for k, c in comps.items() if c["score"] is not None], key=lambda kv: -kv[1]["score"])
    if best:
        lines.append(f"Strongest: {best[0][0]} ({best[0][1]['why']}). Weakest: {best[-1][0]} ({best[-1][1]['why']}).")
    unknown = [k for k, c in comps.items() if c["score"] is None]
    if unknown:
        lines.append("UNKNOWN components (excluded, not estimated): " + ", ".join(unknown) + ".")
    if prefs:
        lines.append("Below screening preferences: " + "; ".join(prefs) + ".")
    lines.append("Eligible for copy allocation." if eligible else "Not eligible for automatic copying.")
    lines.append("Past performance does not guarantee future results.")
    return ScoreResult(score, rejected, eligible, round(coverage, 3), comps, rejections, flags, cats, " ".join(lines))


def categorise(m: dict, description: str | None = None) -> list[str]:
    cats = []
    dd = m.get("max_equity_drawdown_pct") or m.get("max_balance_drawdown_pct")
    vol = m.get("return_volatility_ann")
    if dd is not None:
        if dd <= 5 and (vol is None or vol <= 10):
            cats.append("Conservative")
        elif dd <= 12:
            cats.append("Balanced")
        else:
            cats.append("Aggressive")
    dur, tpd = m.get("avg_trade_duration_h"), m.get("trades_per_day")
    desc = (description or "").lower()
    algo_words = ("algo", "ea ", "expert advisor", "robot", "automated", "systematic", "bot")
    if any(w in desc for w in algo_words) or (tpd is not None and tpd >= 15):
        cats.append("Algorithmic")
    if dur is not None:
        if dur < 0.25:
            cats.append("Scalper")
        elif dur <= 24:
            if "Algorithmic" not in cats:
                cats.append("Manual Day Trader")
        else:
            cats.append("Swing Trader")
    return cats


def _f(x):
    return "UNKNOWN" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
