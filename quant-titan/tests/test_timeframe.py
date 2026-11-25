import numpy as np
import pytest

from quanttitan.config import TIME_PARAMS, AppConfig, rescale_timeframe
from quanttitan.core.types import Direction
from quanttitan.data.candles import align_htf, bars_in, resample, DAY_MS
from quanttitan.data.synthetic import generate
from quanttitan.engines import build_engines
from quanttitan.engines.base import MarketContext


def test_default_is_3m_and_durations_mean_the_same_time():
    c3 = AppConfig()
    assert c3.system.execution_timeframe == "3m"
    c5 = rescale_timeframe(c3, "5m")
    # one VWAP rolling window = 1 day; regime volatility window = 7 days; on both timeframes
    assert c3.engines.vwap.rolling_bars * 3 == c5.engines.vwap.rolling_bars * 5 == 1440
    assert c3.engines.regime.vol_lookback * 3 == c5.engines.regime.vol_lookback * 5 == 7 * 1440
    # chart settings are not touched
    assert c3.engines.trend.ema_slow == c5.engines.trend.ema_slow == 200
    assert c3.engines.vwap.band_mults == c5.engines.vwap.band_mults
    back = rescale_timeframe(c5, "3m")
    for eng, fields in TIME_PARAMS.items():
        for f in fields:
            assert getattr(getattr(back.engines, eng), f) == getattr(getattr(c3.engines, eng), f)


def test_bars_in():
    assert bars_in("3m", DAY_MS) == 480 and bars_in("5m", DAY_MS) == 288 and bars_in("3m", 3_600_000) == 20


@pytest.fixture(scope="module")
def synth3():
    return generate(9000, seed=31, timeframe="3m", base_vol=0.0012)


def test_3m_resampling_has_no_lookahead(synth3):
    c, _ = synth3
    for tf, n in (("15m", 5), ("1h", 20)):
        h = resample(c, tf)
        first = int(np.searchsorted(c.ts, h.ts[0]))
        assert h.volume[0] == pytest.approx(c.volume[first:first + n].sum())
        al = align_htf(c, h, np.arange(len(h), dtype=float))
        ok = ~np.isnan(al)
        assert np.all(h.close_ts[al[ok].astype(int)] <= c.close_ts[ok])


def test_all_engines_run_on_3m(synth3):
    c, _ = synth3
    engines = build_engines(AppConfig())
    ctx = MarketContext(c.symbol, c)
    for name in ("vwap", "trend", "breakout", "order_flow", "regime"):
        e = engines[name]
        f = e.features(ctx)
        sigs = [e.evaluate(ctx, f, i) for i in range(e.min_bars(), len(c), 7)]
        assert sigs and all(s.ts > 0 for s in sigs)
        if name != "regime":
            d = [s for s in sigs if s.direction is not Direction.FLAT]
            for s in d:
                assert (s.entry - s.stop) * s.direction.sign > 0
    labels = engines["regime"].classify_series(ctx)
    assert len(set(labels[600:])) >= 3  # the regime engine distinguishes states on 3m bars


def test_backtester_aligns_config_to_data_timeframe(synth3):
    from quanttitan.backtest.engine import Backtester
    c5, _ = generate(600, seed=1, timeframe="5m")
    bt = Backtester(AppConfig(), {"X": c5})
    assert bt.cfg.system.execution_timeframe == "5m" and bt.cfg.engines.regime.vol_lookback == 2016
    bt3 = Backtester(AppConfig(), {"X": synth3[0]})
    assert bt3.cfg.engines.regime.vol_lookback == 3360


def test_runtime_timeframe_change_rescales_and_versions(tmp_path):
    from cryptography.fernet import Fernet
    from quanttitan.runtime.trader import Trader
    from quanttitan.security.secrets import SecretStore
    from quanttitan.storage.db import Database
    db = Database(f"sqlite:///{tmp_path}/t.db")
    tr = Trader(db, SecretStore(db, Fernet.generate_key()))
    assert tr.cfg.system.execution_timeframe == "3m"
    new = tr.update_config({"system": {"execution_timeframe": "5m"}}, "test")
    assert new.engines.regime.vol_lookback == 2016 and new.engines.vwap.min_session_bars == 24
    assert "timeframe change" in db.config_versions(1)[0]["note"]
    back = tr.update_config({"system": {"execution_timeframe": "3m"}}, "test")
    assert back.engines.regime.vol_lookback == 3360
