"""2.4 forward test: frozen file integrity, judge rules (Bonferroni, instruments, months, drawdown)."""
import copy

import numpy as np
import pytest

from millionx.mine import forward as FW


def _cf(n=2):
    c = [{"rule": ["a>=p90", "b<=p10"], "horizon_min": 60, "direction": "LONG", "cost_scenario": "TAKER", "cost_bps": 14.0}
         for _ in range(n)]
    return {"candidates": c, "sha256": FW._hash(c), "n": n}


def _inp(sym, means, seed, n=60):
    rng = np.random.default_rng(seed)
    res = []
    for m in means:
        t = [1_793_000_000_000 + k * 86_400_000 * 2 for k in range(n)]   # ~4 months of trades
        res.append({"t": t, "net_bps": (rng.normal(m, 20, n)).tolist()})
    return {"symbol": sym, "start": "2026-07-01", "end": "2026-11-01", "sha256": None, "results": res}


def test_judge_passes_strong_and_rejects_noise():
    cf = _cf(2)
    ins = []
    for k in range(10):
        x = _inp(f"S{k}", [15.0, 0.0], k)
        x["sha256"] = cf["sha256"]
        ins.append(x)
    j = FW.judge(cf, ins)
    passed = [r for r in j["ranking"] if r["PASS"]]
    assert len(passed) == 1 and passed[0]["forward_net_bps"] > 10
    assert "PASSED: 1" in FW.to_markdown(j)


def test_tampered_candidates_detected():
    cf = _cf(1)
    bad = copy.deepcopy(cf)
    bad["candidates"][0]["horizon_min"] = 5
    with pytest.raises(AssertionError):
        FW.evaluate(bad, "X", None, None, "/tmp")
    x = _inp("S0", [15.0], 0)
    x["sha256"] = "other"
    with pytest.raises(AssertionError):
        FW.judge(cf, [x])
