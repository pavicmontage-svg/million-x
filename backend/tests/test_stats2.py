"""Research Engine 2.0 statistics."""
import numpy as np

from millionx.backtest import stats2 as S


def test_purged_kfold_never_overlaps_test():
    folds = S.purged_kfold_ranges(0, 1_000_000, 5, purge_ms=10_000, embargo_ms=20_000)
    for f in folds:
        a, b = f["test"]
        for x, y in f["train"]:
            assert y <= a - 10_000 or x >= b + 20_000


def test_deflated_sharpe_penalises_many_trials():
    r = np.random.default_rng(3).normal(0.05, 1.0, 400)
    one = S.deflated_sharpe(r, 1)["dsr"]
    many = S.deflated_sharpe(r, 500)["dsr"]
    assert many < one


def test_pbo_high_for_noise_low_for_real_edge():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 1, (400, 20))
    assert S.pbo_cscv(noise)["pbo"] > 0.3
    edge = rng.normal(0, 1, (400, 20))
    edge[:, 7] += 0.8  # one configuration is genuinely better everywhere
    assert S.pbo_cscv(edge)["pbo"] < 0.1


def test_bootstrap_detects_negative_expectancy():
    r = np.random.default_rng(1).normal(-0.2, 1.0, 300)
    b = S.block_bootstrap(r, runs=500)
    assert b["exp_r_ci95"][1] < 0.1 and b["p_exp_le_0"] > 0.9
