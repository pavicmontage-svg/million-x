"""Evidence-based expected-edge estimates.

The platform never assumes a setup is profitable. Each (engine, setup, direction) key accumulates
*net* R-multiples (after fees, slippage and funding) from out-of-sample backtests, paper trading and
live trading. The expected edge is the posterior mean under a normal prior centred on
``prior_mean`` (default 0R) with strength ``prior_strength`` pseudo-trades:

    E[R] = (n · mean + k · prior_mean) / (n + k)

so a handful of lucky trades cannot produce a large estimate. A one-sided lower bound
(mean − 1.645·sd/√n, also shrunk) is reported for the Quant Researcher and the dashboard.
In-sample backtest results are deliberately not accepted as evidence.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

ACCEPTED_SOURCES = ("backtest_oos", "paper", "testnet", "live")


@dataclass
class EdgeEstimate:
    key: str
    n: int
    mean_r: float
    shrunk_r: float
    lower_r: float
    win_rate: float
    by_source: dict[str, int]

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class EdgeBook:
    def __init__(self, prior_strength: float = 30.0, prior_mean: float = 0.0, max_per_key: int = 5000) -> None:
        self.prior_strength = prior_strength
        self.prior_mean = prior_mean
        self.max_per_key = max_per_key
        self._r: dict[str, list[tuple[float, str]]] = defaultdict(list)

    @staticmethod
    def key(engine: str, setup: str, direction: str) -> str:
        return f"{engine}|{setup}|{direction}"

    def record(self, engine: str, setup: str, direction: str, r_net: float, source: str) -> None:
        if source not in ACCEPTED_SOURCES:
            raise ValueError(f"source {source!r} is not accepted as evidence (allowed: {ACCEPTED_SOURCES})")
        if not math.isfinite(r_net):
            return
        lst = self._r[self.key(engine, setup, direction)]
        lst.append((float(r_net), source))
        if len(lst) > self.max_per_key:
            del lst[: len(lst) - self.max_per_key]

    def estimate(self, engine: str, setup: str, direction: str) -> EdgeEstimate:
        k = self.key(engine, setup, direction)
        rows = self._r.get(k, [])
        n = len(rows)
        by_src: dict[str, int] = defaultdict(int)
        for _, s in rows:
            by_src[s] += 1
        if n == 0:
            return EdgeEstimate(k, 0, 0.0, self.prior_mean, -math.inf, 0.0, {})
        vals = [r for r, _ in rows]
        mean = sum(vals) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 1.0
        w = self.prior_strength
        shrunk = (n * mean + w * self.prior_mean) / (n + w)
        lower = (n * (mean - 1.645 * sd / math.sqrt(n)) + w * self.prior_mean) / (n + w)
        win = sum(1 for v in vals if v > 0) / n
        return EdgeEstimate(k, n, mean, shrunk, lower, win, dict(by_src))

    def all_estimates(self) -> list[EdgeEstimate]:
        out = []
        for k in self._r:
            e, s, d = k.split("|")
            out.append(self.estimate(e, s, d))
        return sorted(out, key=lambda x: -x.shrunk_r)

    def dump(self) -> dict:
        return {k: v for k, v in self._r.items()}

    def load(self, data: dict) -> None:
        for k, rows in data.items():
            self._r[k] = [(float(r), str(s)) for r, s in rows]
