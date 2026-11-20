"""Minimal LLM client with token/cost accounting.

Providers:
  * "anthropic" — Claude Messages API (paid).
  * "openai"    — any OpenAI-compatible /chat/completions endpoint, e.g. Groq
    (https://api.groq.com/openai/v1, free tier) or Google Gemini's compatible
    layer (https://generativelanguage.googleapis.com/v1beta/openai). Free tiers
    are rate-limited, so calls are spaced by ``min_gap_s``.

Prices are NOT hard-coded: set MX_LLM_PRICES as JSON, e.g.
  {"claude-haiku-5-5": [in_usd_per_mtok, out_usd_per_mtok]}
from the current Anthropic price list. Without a price the cost is reported
as unknown (tokens are always recorded).
"""
from __future__ import annotations

import json
import os
import threading
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


PRESETS = {
    # provider id: (api style, base url, default model, min seconds between calls)
    "groq": ("openai", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", 20.0),
    "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "", 15.0),
    "openai_compatible": ("openai", "", "", 5.0),
    "anthropic": ("anthropic", API_URL, DEFAULT_MODEL, 0.0),
}


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0):
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.provider = "anthropic"
        self.style = "anthropic"
        self.base_url = API_URL
        self.model_override = ""     # used for non-Anthropic providers (agent configs hold Claude model ids)
        self.min_gap_s = 0.0
        self.max_packet_chars = 150_000
        self.timeout = timeout
        self._gap_lock = threading.Lock()
        self._last_call = 0.0
        try:
            self.prices = json.loads(os.environ.get("MX_LLM_PRICES", "{}"))
        except ValueError:
            self.prices = {}

    def configure(self, provider: str, key: str, base_url: str = "", model: str = "",
                  min_gap_s: Optional[float] = None) -> None:
        if provider not in PRESETS:
            raise ValueError(f"unknown provider {provider}")
        style, url, dmodel, gap = PRESETS[provider]
        base_url = (base_url or url).rstrip("/")
        model = model or dmodel
        if style == "openai" and (not base_url or not model):
            raise ValueError("base URL and model are required for this provider")
        self.provider, self.style, self.key = provider, style, key.strip()
        self.base_url = base_url
        self.model_override = model if style == "openai" else ""
        self.min_gap_s = gap if min_gap_s is None else float(min_gap_s)
        # free tiers have small per-minute token budgets: keep evidence packets short
        self.max_packet_chars = 150_000 if style == "anthropic" else 12_000

    def disconnect(self) -> None:
        self.key = ""

    def describe(self) -> dict:
        return {"connected": self.available, "provider": self.provider if self.available else None,
                "base_url": self.base_url if self.available else None,
                "model": (self.model_override or DEFAULT_MODEL) if self.available else None,
                "min_gap_s": self.min_gap_s}

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _space_calls(self) -> None:
        if self.min_gap_s <= 0:
            return
        with self._gap_lock:
            wait = self._last_call + self.min_gap_s - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def complete(self, system: str, user: str, model: str = DEFAULT_MODEL, max_tokens: int = 1500,
                 temperature: float = 0.2) -> LLMResult:
        if not self.key:
            return LLMResult("", 0, 0, None, 0, model, "LLM offline: no AI model connected")
        if self.style == "openai":
            return self._complete_openai(system, user, max_tokens, temperature)
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

    def _complete_openai(self, system: str, user: str, max_tokens: int, temperature: float) -> LLMResult:
        model = self.model_override
        self._space_calls()
        t0 = time.time()
        try:
            r = requests.post(self.base_url + "/chat/completions", timeout=self.timeout, headers={
                "Authorization": f"Bearer {self.key}", "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "temperature": temperature,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        except requests.RequestException as e:
            return LLMResult("", 0, 0, None, int((time.time() - t0) * 1000), model, f"network: {e}")
        lat = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            hint = " (free-tier rate limit reached; retries next cycle)" if r.status_code == 429 else ""
            return LLMResult("", 0, 0, None, lat, model, f"HTTP {r.status_code}{hint}: {r.text[:200]}")
        try:
            d = r.json()
            text = d["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError):
            return LLMResult("", 0, 0, None, lat, model, f"unexpected response: {r.text[:200]}")
        u = d.get("usage") or {}
        it, ot = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        price = self.prices.get(model)
        cost = (it * price[0] + ot * price[1]) / 1e6 if price else (0.0 if self.provider in ("groq", "gemini") else None)
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
