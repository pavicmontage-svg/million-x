"""2.0 research prototypes and Research Engine 2.0."""
import numpy as np

from millionx.backtest.data import synthetic_bars
from millionx.models import Side, Trade
from millionx.strategies import RESEARCH_CLASSES, MarketData, build_bots

PROTO_IDS = [c.bot_id for c in RESEARCH_CLASSES if c.bot_id.startswith("proto")]


def _sigs(bars, upto):
    md = MarketData("TESTUSDT", bars.slice(0, upto))
    out = {}
    for c in RESEARCH_CLASSES:
        s = c()
        s.prepare(md)
        out[s.bot_id] = {int(i): s.signal_at(int(i)) for i in s.close_idx}
    return out


def test_prototypes_registered_and_not_in_live_set():
    assert len(PROTO_IDS) == 4
    live_ids = {b.bot_id for b in build_bots()}
    assert not live_ids & set(PROTO_IDS)
    assert {b.bot_id for b in build_bots(include=PROTO_IDS[:1])} >= {PROTO_IDS[0]}


def test_prototypes_no_lookahead_and_valid_geometry():
    bars = synthetic_bars(30_000, seed=5)
    full = _sigs(bars, len(bars))
    n_sig = 0
    for cut in (18_000, 24_000):
        part = _sigs(bars, cut)
        for bot, sigs in part.items():
            for i, s in sigs.items():
                f = full[bot].get(i)
                assert (s is None) == (f is None), f"{bot} at {i}: future data changes the signal"
                if s is not None:
                    assert (s.side, round(s.stop, 9), round(s.tp2, 9)) == (f.side, round(f.stop, 9), round(f.tp2, 9))
    for bot, sigs in full.items():
        for s in sigs.values():
            if s is None:
                continue
            n_sig += 1
            if s.side == Side.LONG:
                assert s.stop < s.entry < s.tp1 <= s.tp2
            else:
                assert s.stop > s.entry > s.tp1 >= s.tp2
    assert n_sig > 0


def _t(sym, opened, closed, r, side=Side.LONG):
    return Trade(id="x", bot_id="b", symbol=sym, side=str(side), qty=1.0, entry_price=1.0, exit_price=1.0,
                 opened_at=opened, closed_at=closed, gross_pnl=r, fees=0.0, funding=0.0, net_pnl=r, r_multiple=r,
                 risk_amount=1.0, exit_reason="t")


def test_nested_wf_only_uses_outer_test_trades_and_purges():
    from millionx import research2 as R2
    DAY = R2.DAY
    t0, t1 = 0, 200 * DAY
    rng = np.random.default_rng(1)
    # config 0 is good only in training ranges, config 1 random; OOS must come from test windows only
    by_cfg = []
    for c in range(2):
        ts = []
        for d in range(200):
            for k in range(6):
                o = d * DAY + k * 3_600_000
                ts.append(_t("BTCUSDT", o, o + 600_000, float(rng.normal(0.3 if c == 0 else 0.0, 1))))
        by_cfg.append(ts)
    grid = [{"x": 1}, {"x": 2}]
    wf = R2.nested_wf(by_cfg, grid, t0, t1)
    assert wf["windows"], "at least one outer window"
    for w in wf["windows"]:
        assert w["test"][0] == w["train"][1]
    test_ranges = [tuple(w["test"]) for w in wf["windows"]]
    for t in wf["oos"]:
        assert any(a <= t.opened_at < b for a, b in test_ranges)
    first_test = wf["windows"][0]["test"][0]
    assert all(t.opened_at >= first_test for t in wf["oos"])


def test_evaluate_end_to_end_small(tmp_path):
    from millionx import research2 as R2
    bars = synthetic_bars(60 * 24 * 150, seed=9)
    data = {"BTCUSDT": bars, "DOGEUSDT": synthetic_bars(60 * 24 * 150, seed=10)}
    grid = R2.grid_from({"lookback": [6, 12]}, {"timeframe": 15})
    t0, t1 = int(bars.open_time[0]), int(bars.open_time[-1])
    rep = R2.evaluate(data, "protoD_cost_momentum", grid, (t0, t1), n_trials_total=2, log=lambda *_: None)
    assert rep["verdict"] in ("RESEARCH", "CANDIDATE_FOR_PAPER")
    assert set(rep["cost_scenarios"]) == {"BASE", "STRESSED", "EXTREME"}
    assert "deflated_sharpe" in rep["checks"] and "pbo" in rep["checks"]
    # a synthetic random walk must not be declared tradeable
    assert rep["verdict"] == "RESEARCH"


def test_v2_strategies_registered_versioned_and_gated():
    from millionx.strategies import v2 as V2
    ids = [c.bot_id for c in RESEARCH_CLASSES if c.bot_id.endswith("_v2")]
    assert len(ids) == 9 and "bot3_orderflow_v2" in ids
    assert "CVD-only" in V2.OrderFlowV2.name
    assert not {b.bot_id for b in build_bots()} & set(ids), "v2 never in the live set"
    bars = synthetic_bars(40_000, seed=12)
    md = MarketData("TESTUSDT", bars)
    n = 0
    for c in RESEARCH_CLASSES:
        if not c.bot_id.endswith("_v2"):
            continue
        s = c()
        s.prepare(md)
        assert s.version == "2.0.0"
        for i in s.close_idx:
            sig = s.signal_at(int(i))
            if sig is None:
                continue
            n += 1
            j = int(s.j_of_i[int(i)])
            if s.kind == "trend":
                assert s.f["t60"][j] == int(sig.side)
            else:
                assert s.f["adx15"][j] < s.params["adx_max"] and s.f["t60"][j] != -int(sig.side)
    assert n > 0


def test_v2_orderflow_rejects_missing_live_book():
    from millionx.strategies.v2 import OrderFlowV2
    bars = synthetic_bars(40_000, seed=13)
    md = MarketData("TESTUSDT", bars)
    s = OrderFlowV2({"min_cost_mult": 4.0})
    s.prepare(md)
    hist = [i for i in s.close_idx if s.signal_at(int(i))]
    assert hist, "fixture must produce at least one historical (CVD-only) signal"
    md.live = True  # live with no book = missing data
    assert all(s.signal_at(int(i)) is None for i in hist)


def test_research2_import_and_api(tmp_path):
    import json
    from starlette.testclient import TestClient
    from millionx import research2_store
    from millionx.storage import Store
    st = Store(str(tmp_path / "r.db"))
    agg = {"run": "ci-1", "reports": [
        {"bot": "bot2_ema_v2", "timeframe": 15, "verdict": "RESEARCH", "oos": {"trades": 10}, "checks": {}},
        {"bot": "protoD_cost_momentum", "timeframe": 15, "verdict": "CANDIDATE_FOR_PAPER", "oos": {"trades": 300},
         "checks": {}}],
        "selection": {"cells": {"BTCUSDT|SIDEWAYS": {"symbol": "BTCUSDT", "regime": "SIDEWAYS", "selected": "NO_TRADE",
                                                     "why": "none", "candidates": []}},
                      "chosen_strategies": [], "no_trade_cells": 1}}
    out = research2_store.import_results(st, agg, "test")
    assert out["reports"] == 2
    assert st.kv_get("research2:protoD_cost_momentum")["verdict"] == "CANDIDATE_FOR_PAPER"
    ov = research2_store.overview(st, None, None)
    assert ov["available"] and ov["report"]["cells"][0]["selected"] == "NO_TRADE"
    assert any(c["id"] == "bot3_orderflow_v2" and c["research_only"] for c in ov["catalog"])
    assert ov["param_history"][0]["action"] == "research2_imported"
    with __import__("pytest").raises(ValueError):
        research2_store.summarise({"nope": 1})
