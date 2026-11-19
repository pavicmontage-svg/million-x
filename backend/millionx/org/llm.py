"""Minimal Claude API client with token/cost accounting.

Prices are NOT hard-coded: set MX_LLM_PRICES as JSON, e.g.
  {"claude-haiku-5-5": [in_usd_per_mtok, out_usd_per_mtok]}
from the current Anthropic price list. Without a price the cost is reported
as unknown (tokens are always recorded).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("MX_LLM_MODEL", "claude-haiku-5-5")


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float]
    latency_ms: int
    model: str
    error: Optional[str] = None


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0):
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        try:
            self.prices = json.loads(os.environ.get("MX_LLM_PRICES", "{}"))
        except ValueError:
            self.prices = {}

    @property
    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, user: str, model: str = DEFAULT_MODEL, max_tokens: int = 1500,
                 temperature: float = 0.2) -> LLMResult:
        if not self.key:
            return LLMResult("", 0, 0, None, 0, model, "LLM offline: ANTHROPIC_API_KEY not set")
        t0 = time.time()
        try:
            r = requests.post(API_URL, timeout=self.timeout, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                      "messages": [{"role": "user", "content": user}]})
        except requests.RequestException as e:
            return LLMResult("", 0, 0, None, int((time.time() - t0) * 1000), model, f"network: {e}")
        lat = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return LLMResult("", 0, 0, None, lat, model, f"HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        text = "".join(c.get("text", "") for c in d.get("content", []) if c.get("type") == "text")
        u = d.get("usage", {})
        it, ot = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        price = self.prices.get(model)
        cost = (it * price[0] + ot * price[1]) / 1e6 if price else None
        return LLMResult(text, it, ot, cost, lat, model)


def parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(text[a:b + 1])
    except ValueError:
        return None
