"""2.1 archive alignment: availability lag, staleness, timestamp parsing, holdout refusal."""
from datetime import date

import numpy as np
import pytest

from millionx.micro import archive as A


def test_ffill_at_uses_only_available_values_and_expires():
    decision = np.array([100, 200, 300, 400, 1000])
    t_avail = np.array([150, 250])
    v, age = A._ffill_at(decision, t_avail, np.array([1.0, 2.0]), max_age_ms=300)
    assert np.isnan(v[0]), "nothing known before the first availability time"
    assert v[1] == 1.0 and v[2] == 2.0 and v[3] == 2.0
    assert np.isnan(v[4]), "older than max_age -> missing, not carried forever"
    assert age[2] == 50


def test_ms_parsing_strings_and_microseconds():
    s = A._ms(["2026-11-15 00:00:01", "2026-11-15 00:05:00"])
    assert s[1] - s[0] == 299_000 and s[0] == 1_794_700_801_000
    assert list(A._ms(np.array([1_794_700_800_006_000, 1_794_700_800_006]))) == [1_794_700_800_006, 1_794_700_800_006]


def test_holdout_refused():
    with pytest.raises(ValueError):
        A.build_symbol("BTCUSDT", date(2026, 6, 1), date(2026, 7, 2), "/tmp/none")
