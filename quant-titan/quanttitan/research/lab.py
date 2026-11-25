"""Research lab — isolated experiments.

Every experiment deep-copies the configuration and uses its own edge book and risk state, so nothing
here can change live parameters or limits. Results are *proposals*; promoting a configuration to
live use requires a human decision in the dashboard.

Experiments
  train_test         chronological split; reports in-sample vs out-of-sample and the overfit ratio
  walk_forward       rolling train → test folds; parameters chosen on train only; stitched OOS
                     trades are the only backtest results admitted to the edge book (source backtest_oos)
  monte_carlo        trade-order shuffling and bootstrap resampling → drawdown / return distributions,
                     probability of loss and of a ≥ruin_dd drawdown
  sensitivity        one-at-a-time parameter perturbation → is performance a broad plateau or a spike?
  policy_comparison  regime_gated vs majority_vote vs confidence_average on identical data
"""
from __future__ import annotations

import copy
import itertools
import math
from typing import Any

import numpy as np

from ..backtest.engine import Backtester, BacktestResult
from ..backtest.metrics import compute_metrics, daily_returns, max_drawdown
from ..config import AppConfig, dump, merge_patch
from ..core.types import TradeRecord
from ..data.candles import Candles
from ..orchestrator.edge import EdgeBook
from .stats import bootstrap_ci, deflated_sharpe, overfit_ratio

DAY = 86_400_000


def _patch(path: str, value: Any) -> dict:
    out: dict = {}
    cur = out
    parts = path.split(".")
    for p in parts[:-1]:
        cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    return out


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _span(data: dict[str, Candles]) -> tuple[int, int]:
    return min(int(c.close_ts[0]) for c in data.values()), max(int(c.close_ts[-1]) for c in data.values())


def _score(m: dict, min_trades: int) -> float:
    if m.get("trades", 0) < min_trades:
        return -math.inf
    return float(m.get("expectancy_r", 0.0))


class ResearchLab:
    def __init__(self, cfg: AppConfig, data: dict[str, Candles], funding: dict | None = None, specs: dict | None = None,
                 warmup_days: float = 7.0) -> None:
        self.base = copy.deepcopy(cfg)
        self.data = data
        self.funding = funding or {}
        self.specs = specs or {}
        self.warmup_ms = int(warmup_days * DAY)
        self.trials = 0

    def backtest(self, cfg: AppConfig, start: int | None = None, end: int | None = None, label: str = "") -> BacktestResult:
        self.trials += 1
        return Backtester(cfg, self.data, funding=self.funding, specs=self.specs, label=label).run(start, end)

    # ------------------------------------------------------------------ split
    def train_test(self, train_frac: float = 0.6, cfg: AppConfig | None = None) -> dict:
        cfg = cfg or self.base
        s, e = _span(self.data)
        s += self.warmup_ms
        cut = int(s + (e - s) * train_frac)
        tr = self.backtest(cfg, s, cut, "train")
        te = self.backtest(cfg, cut + 1, e, "test")
        return {"experiment": "train_test", "split_ts": cut, "train": tr.summary(), "test": te.summary(),
                "overfit_ratio_expectancy": overfit_ratio(tr.metrics.get("expectancy_r", 0), te.metrics.get("expectancy_r", 0))}

    # ------------------------------------------------------------------ walk-forward
    def walk_forward(self, grid: dict[str, list], train_days: float = 30, test_days: float = 10, min_trades: int = 10,
                     edge_book: EdgeBook | None = None, max_folds: int = 12) -> dict:
        s, e = _span(self.data)
        s += self.warmup_ms
        folds = []
        oos: list[TradeRecord] = []
        combos = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())] if grid else [{}]
        base_json = dump(self.base)
        t = s
        while t + (train_days + test_days) * DAY <= e + 1 and len(folds) < max_folds:
            tr_s, tr_e = t, int(t + train_days * DAY)
            te_s, te_e = tr_e + 1, int(tr_e + test_days * DAY)
            best, best_score, table = None, -math.inf, []
            for combo in combos:
                patch: dict = {}
                for path, v in combo.items():
                    patch = _deep_merge(patch, _patch(path, v))
                cfg = merge_patch(self.base, patch) if patch else copy.deepcopy(self.base)
                r = self.backtest(cfg, tr_s, tr_e, f"wf-train {combo}")
                sc = _score(r.metrics, min_trades)
                table.append({"params": combo, "train_trades": r.metrics.get("trades", 0), "train_expectancy_r": r.metrics.get("expectancy_r", 0)})
                if sc > best_score:
                    best, best_score = (combo, cfg), sc
            if best is None:  # no combination met min_trades: use base config, flagged
                best = ({}, copy.deepcopy(self.base))
            combo, cfg = best
            test = self.backtest(cfg, te_s, te_e, f"wf-test {combo}")
            oos.extend(test.trades)
            folds.append({"train": [tr_s, tr_e], "test": [te_s, te_e], "chosen": combo,
                          "train_score_r": best_score if math.isfinite(best_score) else None,
                          "test": test.summary(), "candidates": table})
            t = int(t + test_days * DAY)
        oos.sort(key=lambda x: x.closed_ts)
        if edge_book is not None:
            for tr in oos:
                edge_book.record(tr.engine, tr.setup, tr.direction, tr.r_multiple, "backtest_oos")
        m = compute_metrics(oos, self.base.system.paper_starting_equity)
        rets = daily_returns(oos, self.base.system.paper_starting_equity)
        return {"experiment": "walk_forward", "grid": grid, "folds": folds, "oos_metrics": m,
                "deflated_sharpe": deflated_sharpe(rets, max(1, len(combos) * max(len(folds), 1))),
                "expectancy_ci95": bootstrap_ci(np.array([t.r_multiple for t in oos])) if oos else None,
                "trials": len(combos) * len(folds), "base_config_hash": hash(str(base_json)) & 0xFFFFFFFF}

    # ------------------------------------------------------------------ Monte Carlo
    @staticmethod
    def monte_carlo(trades: list[TradeRecord], equity0: float, n: int = 2000, seed: int = 0, ruin_dd: float = 30.0) -> dict:
        if len(trades) < 5:
            return {"experiment": "monte_carlo", "error": f"need ≥5 trades, have {len(trades)}"}
        rng = np.random.default_rng(seed)
        # per-trade return as a fraction of equity at the time of the trade (compounding-consistent)
        eq = equity0
        fr = []
        for t in sorted(trades, key=lambda x: x.closed_ts):
            fr.append(t.net_pnl / eq if eq > 0 else 0.0)
            eq += t.net_pnl
        fr = np.array(fr)

        def path_stats(seq: np.ndarray) -> tuple[float, float]:
            curve = equity0 * np.cumprod(1 + seq)
            dd, _ = max_drawdown(curve, equity0)
            return float(curve[-1] / equity0 - 1) * 100, dd

        shuf = np.array([path_stats(rng.permutation(fr)) for _ in range(n)])
        boot = np.array([path_stats(fr[rng.integers(0, len(fr), len(fr))]) for _ in range(n)])

        def q(a):
            return {f"p{p}": round(float(np.percentile(a, p)), 3) for p in (5, 25, 50, 75, 95)}

        return {"experiment": "monte_carlo", "trades": len(fr), "simulations": n,
                "shuffle_max_dd_pct": q(shuf[:, 1]), "bootstrap_return_pct": q(boot[:, 0]), "bootstrap_max_dd_pct": q(boot[:, 1]),
                "prob_loss": round(float((boot[:, 0] < 0).mean()), 4),
                f"prob_dd_ge_{ruin_dd:.0f}pct": round(float((boot[:, 1] >= ruin_dd).mean()), 4)}

    # ------------------------------------------------------------------ sensitivity
    def sensitivity(self, params: dict[str, list], start: int | None = None, end: int | None = None, min_trades: int = 10) -> dict:
        base_r = self.backtest(self.base, start, end, "base")
        rows = []
        for path, values in params.items():
            for v in values:
                cfg = merge_patch(self.base, _patch(path, v))
                r = self.backtest(cfg, start, end, f"{path}={v}")
                rows.append({"param": path, "value": v, **r.summary()})
        exps = [r["expectancy_r"] for r in rows if (r["trades"] or 0) >= min_trades]
        plateau = (sum(1 for x in exps if x > 0) / len(exps)) if exps else None
        return {"experiment": "sensitivity", "base": base_r.summary(), "variants": rows,
                "positive_fraction": plateau,
                "interpretation": None if plateau is None else
                ("broad plateau" if plateau >= 0.7 else "fragile: performance depends on exact parameters" if plateau < 0.4 else "mixed")}

    # ------------------------------------------------------------------ policy comparison
    def policy_comparison(self, start: int | None = None, end: int | None = None) -> dict:
        out = {}
        for pol in ("regime_gated", "majority_vote", "confidence_average"):
            cfg = merge_patch(self.base, {"orchestrator": {"policy": pol}})
            out[pol] = self.backtest(cfg, start, end, pol).summary()
        return {"experiment": "policy_comparison", "results": out}
