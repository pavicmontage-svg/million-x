"""Data Quality Engine (2.1, §8).

Every value that reaches research or a snapshot carries its timestamp, source, instrument,
quality status, freshness and a missing flag. Missing data is NEVER filled with assumptions:
the analysis that needs it is marked INCOMPLETE instead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

import numpy as np

OK, DEGRADED, MISSING, STALE, INVALID = "OK", "DEGRADED", "MISSING", "STALE", "INVALID"
COMPLETE, INCOMPLETE = "COMPLETE", "INCOMPLETE"

# maximum age before a value is STALE (ms) — live snapshot use
MAX_AGE_MS = {"ohlcv": 90_000, "funding": 30_000, "open_interest": 120_000, "liquidations": None,
              "order_book": 3_000, "cvd": 90_000, "spread": 3_000, "volatility": 90_000, "regime": 15 * 60_000 + 60_000}
CRITICAL = ("ohlcv", "order_book", "cvd")  # a snapshot without these is INCOMPLETE


@dataclass
class Field:
    name: str
    value: Any
    ts: Optional[int]
    source: str
    instrument: str
    quality: str = OK
    missing: bool = False
    note: str = ""
    age_ms: Optional[int] = None
    fresh: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stamp(name: str, value: Any, ts: Optional[int], source: str, instrument: str, now_ms: int,
          quality: str = OK, note: str = "") -> Field:
    """Build a Field with freshness computed against `now_ms`. None value or ts -> MISSING."""
    if value is None or ts is None:
        return Field(name, None, ts, source, instrument, MISSING, True, note or "no data", None, False)
    age = now_ms - int(ts)
    limit = MAX_AGE_MS.get(name)
    fresh = limit is None or age <= limit
    q = quality if fresh else STALE
    if age < -5_000:  # from the future: clock drift or wrong mapping
        q, fresh, note = INVALID, False, f"timestamp {-age} ms in the future (clock drift?)"
    return Field(name, value if fresh and q != INVALID else None, int(ts), source, instrument, q,
                 not fresh or q == INVALID, note, age, fresh)


def snapshot_status(fields: Iterable[Field]) -> dict[str, Any]:
    fs = {f.name: f for f in fields}
    missing = [n for n, f in fs.items() if f.missing]
    crit = [n for n in CRITICAL if n not in fs or fs[n].missing]
    return {"status": INCOMPLETE if crit else COMPLETE, "missing": missing, "missing_critical": crit}


# ---------------------------------------------------------------- series checks (archives / recorded data)
@dataclass
class SeriesReport:
    name: str
    instrument: str
    rows: int
    expected_step_ms: int
    gaps: int = 0
    missing_points: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    invalid_values: int = 0
    abnormal_values: int = 0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    coverage: float = 0.0
    largest_gap_ms: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.rows == 0:
            return MISSING
        if self.invalid_values or self.out_of_order or self.coverage < 0.95:
            return DEGRADED
        return OK

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status
        return d


def check_series(name: str, instrument: str, ts: Sequence[int], values: Optional[np.ndarray],
                 expected_step_ms: int, start_ms: Optional[int] = None, end_ms: Optional[int] = None,
                 positive: bool = True, abnormal_z: float = 12.0) -> SeriesReport:
    """Gaps, duplicates, out-of-order timestamps, invalid (non-finite / non-positive) and abnormal values
    (robust z-score of log changes above `abnormal_z`). Coverage is measured on [start, end)."""
    t = np.asarray(ts, dtype=np.int64)
    rep = SeriesReport(name, instrument, len(t), expected_step_ms)
    if len(t) == 0:
        rep.issues.append("no data")
        return rep
    rep.first_ts, rep.last_ts = int(t.min()), int(t.max())
    d = np.diff(t)
    rep.out_of_order = int((d < 0).sum())
    rep.duplicates = int(len(t) - len(np.unique(t)))
    ts_sorted = np.unique(t)
    dd = np.diff(ts_sorted)
    big = dd > 1.5 * expected_step_ms
    rep.gaps = int(big.sum())
    rep.missing_points = int(np.round(dd[big] / expected_step_ms - 1).sum()) if big.any() else 0
    rep.largest_gap_ms = int(dd.max()) if len(dd) else 0
    a = start_ms if start_ms is not None else int(ts_sorted[0])
    b = end_ms if end_ms is not None else int(ts_sorted[-1]) + expected_step_ms
    expected = max(1, (b - a) // expected_step_ms)
    inside = ((ts_sorted >= a) & (ts_sorted < b)).sum()
    rep.coverage = float(min(1.0, inside / expected))
    if values is not None and len(values):
        v = np.asarray(values, dtype=float)
        bad = ~np.isfinite(v) | ((v <= 0) if positive else np.zeros(len(v), bool))
        rep.invalid_values = int(bad.sum())
        good = v[~bad]
        if positive and len(good) > 50:
            r = np.diff(np.log(good))
            med = np.median(r)
            mad = np.median(np.abs(r - med)) or 1e-12
            rep.abnormal_values = int((np.abs(r - med) / (1.4826 * mad) > abnormal_z).sum())
    if rep.gaps:
        rep.issues.append(f"{rep.gaps} gaps, {rep.missing_points} missing points, largest {rep.largest_gap_ms / 60000:.1f} min")
    if rep.duplicates:
        rep.issues.append(f"{rep.duplicates} duplicate timestamps")
    if rep.out_of_order:
        rep.issues.append(f"{rep.out_of_order} out-of-order events")
    if rep.invalid_values:
        rep.issues.append(f"{rep.invalid_values} invalid values")
    if rep.abnormal_values:
        rep.issues.append(f"{rep.abnormal_values} abnormal jumps (robust z > {abnormal_z})")
    return rep


def check_symbol_mapping(expected: str, seen: Iterable[str]) -> list[str]:
    bad = sorted({s for s in seen if s and s.upper() != expected.upper()})
    return [f"rows for {s} found in {expected} file" for s in bad]


def analysis_status(required: Sequence[str], available: dict[str, SeriesReport], min_coverage: float = 0.9) -> dict[str, Any]:
    """An analysis is COMPLETE only when every required source exists with coverage >= min_coverage."""
    missing = [r for r in required if r not in available or available[r].rows == 0]
    weak = [r for r in required if r in available and available[r].rows and available[r].coverage < min_coverage]
    return {"status": INCOMPLETE if (missing or weak) else COMPLETE, "missing": missing,
            "low_coverage": {r: round(available[r].coverage, 3) for r in weak}}
