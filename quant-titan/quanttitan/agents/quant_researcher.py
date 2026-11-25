"""AGENT 1 — Quant Researcher.

Owns the evidence base. Reviews the edge book, quantifies statistical significance and overfitting
(deflated Sharpe, bootstrap confidence intervals, in-sample vs out-of-sample degradation), tests
order-flow feature predictiveness, and proposes experiments that run only in the isolated research
lab. Its output is advisory: it cannot change live parameters or risk limits.
"""
from __future__ import annotations

import numpy as np

from ..orchestrator.edge import EdgeBook
from ..research.stats import bootstrap_ci


class QuantResearcher:
    name = "quant_researcher"
    title = "Quant Researcher"
    responsibilities = ["Develop mathematical strategy models", "Analyse statistical edge", "Evaluate features",
                        "Design experiments", "Detect overfitting", "Validate robustness"]

    def __init__(self, edge: EdgeBook) -> None:
        self.edge = edge
        self.last: dict = {}
        self.lab_results: list[dict] = []

    def review(self, min_validated: int = 30) -> dict:
        rows = []
        proposals = []
        for est in self.edge.all_estimates():
            e, s, d = est.key.split("|")
            vals = np.array([r for r, _ in self.edge._r.get(est.key, [])])
            ci = bootstrap_ci(vals) if len(vals) >= 5 else None
            verdict = "UNVALIDATED"
            if est.n >= min_validated:
                if ci and ci["lo"] > 0:
                    verdict = "POSITIVE_EDGE (95% CI excludes 0)"
                elif ci and ci["hi"] < 0:
                    verdict = "NEGATIVE_EDGE"
                else:
                    verdict = "INCONCLUSIVE"
            rows.append({**est.to_dict(), "ci95": ci, "verdict": verdict})
            if verdict == "NEGATIVE_EDGE":
                proposals.append({"experiment": f"diagnose {e}/{s}/{d}", "why": "negative validated expectancy",
                                  "plan": "walk-forward parameter sensitivity + regime breakdown in the research lab"})
            elif verdict == "UNVALIDATED":
                proposals.append({"experiment": f"collect OOS evidence for {e}/{s}/{d}", "why": f"only {est.n} trades",
                                  "plan": "walk-forward backtest on more instruments/months; paper trading"})
        self.last = {"agent": self.name, "edges": rows, "proposed_experiments": proposals[:20],
                     "recent_lab_results": self.lab_results[-10:],
                     "policy": "experiments run in the isolated lab; promotion to live needs human approval"}
        return self.last

    def add_lab_result(self, result: dict) -> None:
        self.lab_results.append(result)
        self.lab_results = self.lab_results[-100:]
