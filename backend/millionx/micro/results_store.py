"""Packaged 2.1 research outputs for the app (data availability, data quality, research A–F).
Files are produced on CI from real data and committed under millionx/data/; nothing here is computed
on the phone and nothing activates a strategy."""
from __future__ import annotations

import json
import pkgutil
from typing import Any


def _get(name: str) -> Any:
    try:
        raw = pkgutil.get_data("millionx", f"data/{name}")
    except (OSError, FileNotFoundError):
        return None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def overview() -> dict[str, Any]:
    res = _get("micro_research_summary.json")
    qual = _get("micro_quality_summary.json")
    avail = _get("micro_availability_summary.json")
    return {"available": res is not None, "research": res, "quality": qual, "availability": avail,
            "note": "Research on the selection period only (2025-01 … 2026-06); the final holdout is not ingested. "
                    "Liquidation research is INCOMPLETE: Binance publishes no liquidation history for 2025–2026."}
