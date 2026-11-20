"""Free-first AI provider chain: Groq -> Google Gemini -> OpenRouter (free models).

Each provider is a separate implementation (endpoints, headers, model discovery and
free-model rules differ), with: configuration, model selection from the provider's
own /models list (no hard-coded model names that may be retired), timeout, retry
for transient errors, rate-limit cool-down (Retry-After / 429), quota counters from
response headers, token accounting, error log and a health check.

Failover order is fixed: groq, gemini, openrouter. A provider is skipped while it
is cooling down, unhealthy (bad key) or not configured. The caller passes a
validator; a reply that does not validate is treated like a failure and the next
provider is tried, so the output format is checked for every model. If every
provider fails the chain returns an error and callers fall back to NO TRADE.

Keys are stored encrypted (Vault) and never logged: error texts are scrubbed.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

ORDER = ("groq", "gemini", "openrouter")


@dataclass
class ProviderSpec:
    id: str
    label: str
    base_url: str
    models_url: str
    prefer: tuple[str, ...]          # substrings tried in order when no model is configured
    avoid: tuple[str, ...] = ()
    min_gap_s: float = 0.0
    key_help: str = ""


SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec("groq", "Groq", "https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1/models",
                         prefer=("gpt-oss-120b", "llama-3.3-70b", "gpt-oss-20b", "llama"),
                         avoid=("whisper", "guard", "tts", "orpheus", "playai", "distil"), min_gap_s=2.0,
                         key_help="console.groq.com → API Keys (free tier, rate-limited)"),
    "gemini": ProviderSpec("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                           "https://generativelanguage.googleapis.com/v1beta/openai/models",
                           prefer=("flash",), avoid=("image", "tts", "embedding", "live", "audio", "lite-preview"),
                           min_gap_s=4.0, key_help="aistudio.google.com/apikey (free tier, small daily quota)"),
    "openrouter": ProviderSpec("openrouter", "OpenRouter (free models)", "https://openrouter.ai/api/v1",
                               "https://openrouter.ai/api/v1/models", prefer=(":free",), min_gap_s=3.0,
                               key_help="openrouter.ai/keys — only models whose id ends with ':free' are used"),
}

_KEY_RX = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_\-]{10,}|Bearer\s+\S+)")


def scrub(text: str) -> str:
    return _KEY_RX.sub("***", text or "")[:300]


@dataclass
class ChainResult:
    ok: bool
    text: str = ""
    data: Any = None
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    attempts: list[dict[str, Any]] = field(default_factory=list)


class Provider:
    def __init__(self, spec: ProviderSpec, http=None):
        self.spec = spec
        self.http = http or requests
        self.key = ""
        self.enabled = True
        self.model = ""                 # configured model ("" = auto)
        self.timeout = 45.0
        self.max_retries = 1
        self.cooldown_until = 0.0
        self.unhealthy_reason = ""
        self.last_error = ""
        self.last_ok = 0.0
        self.models: list[str] = []     # discovered at the last health check
        self.quota: dict[str, str] = {}
        self._last_call = 0.0
        self._lock = threading.Lock()

    # ---- state -----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.key) and self.enabled

    def available(self, now: Optional[float] = None) -> bool:
        return self.configured and not self.unhealthy_reason and (now or time.time()) >= self.cooldown_until

    def headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.key}", "content-type": "application/json"}
        if self.spec.id == "openrouter":
            h["X-Title"] = "MILLION-X"
        return h

    # ---- models ------------------------------------------------------------
    def choose_model(self) -> str:
        ids = self.models
        if self.model and (not ids or self.model in ids):
            return self.model
        if self.spec.id == "openrouter":
            ids = [m for m in ids if m.endswith(":free")]
        ids = [m for m in ids if not any(a in m.lower() for a in self.spec.avoid)]
        for p in self.spec.prefer:
            hit = [m for m in ids if p in m.lower()]
            if hit:
                return sorted(hit, key=len)[0]
        return ids[0] if ids else self.model

    def health_check(self) -> dict[str, Any]:
        """Lists the provider's models with the key: proves key + connectivity."""
        t0 = time.time()
        try:
            r = self.http.get(self.spec.models_url, headers=self.headers(), timeout=min(self.timeout, 20))
        except requests.RequestException as e:
            self.last_error = scrub(f"network: {e}")
            return {"ok": False, "error": self.last_error}
        lat = int((time.time() - t0) * 1000)
        if r.status_code in (401, 403):
            self.unhealthy_reason = f"key rejected (HTTP {r.status_code})"
            return {"ok": False, "error": self.unhealthy_reason}
        if r.status_code != 200:
            self.last_error = scrub(f"HTTP {r.status_code}: {r.text}")
            return {"ok": False, "error": self.last_error}
        try:
            d = r.json()
            rows = d.get("data", d.get("models", [])) if isinstance(d, dict) else d
            ids = []
            for m in rows:
                mid = str(m.get("id") or m.get("name") or "")
                if mid.startswith("models/"):
                    mid = mid[7:]
                if self.spec.id == "openrouter":
                    pr = m.get("pricing") or {}
                    free = mid.endswith(":free") or (str(pr.get("prompt", "1")) in ("0", "0.0") and
                                                     str(pr.get("completion", "1")) in ("0", "0.0"))
                    if not free:
                        continue
                if mid:
                    ids.append(mid)
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "error": "unexpected /models response"}
        self.models = ids
        self.unhealthy_reason = ""
        self.last_ok = time.time()
        return {"ok": True, "models": len(ids), "model": self.choose_model(), "latency_ms": lat}

    # ---- call ----------------------------------------------------------------
    def _space(self) -> None:
        with self._lock:
            wait = self._last_call + self.spec.min_gap_s - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def chat(self, system: str, user: str, max_tokens: int, temperature: float) -> dict[str, Any]:
        model = self.choose_model()
        if not model:
            return {"ok": False, "error": "no model available (run the health check)", "fatal": True}
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        last: dict[str, Any] = {}
        for attempt in range(self.max_retries + 1):
            self._space()
            t0 = time.time()
            try:
                r = self.http.post(self.spec.base_url + "/chat/completions", headers=self.headers(),
                                   json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = {"ok": False, "error": scrub(f"timeout/network: {e}"), "transient": True}
                time.sleep(min(2.0 * (attempt + 1), 5.0))
                continue
            lat = int((time.time() - t0) * 1000)
            self.quota = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()} if r.headers else {}
            if r.status_code == 429:
                ra = r.headers.get("retry-after") if r.headers else None
                try:
                    wait = float(ra) if ra else 60.0
                except ValueError:
                    wait = 60.0
                self.cooldown_until = time.time() + min(max(wait, 5.0), 3600.0)
                return {"ok": False, "error": f"rate limit / quota reached (cool-down {int(wait)} s)", "model": model}
            if r.status_code in (401, 403):
                self.unhealthy_reason = f"key rejected (HTTP {r.status_code})"
                return {"ok": False, "error": self.unhealthy_reason, "model": model}
            if r.status_code >= 500:
                last = {"ok": False, "error": f"HTTP {r.status_code}", "transient": True, "model": model}
                time.sleep(min(2.0 * (attempt + 1), 5.0))
                continue
            if r.status_code != 200:
                return {"ok": False, "error": scrub(f"HTTP {r.status_code}: {r.text}"), "model": model}
            try:
                d = r.json()
                text = d["choices"][0]["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError):
                return {"ok": False, "error": "unexpected response shape", "model": model}
            u = d.get("usage") or {}
            return {"ok": True, "text": text, "model": d.get("model") or model, "latency_ms": lat,
                    "input_tokens": int(u.get("prompt_tokens", 0) or 0),
                    "output_tokens": int(u.get("completion_tokens", 0) or 0)}
        return last or {"ok": False, "error": "failed"}

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {"id": self.spec.id, "label": self.spec.label, "configured": bool(self.key), "enabled": self.enabled,
                "model": self.choose_model() if self.key else None, "configured_model": self.model or "auto",
                "models_known": len(self.models), "available": self.available(now),
                "cooldown_s": max(0, int(self.cooldown_until - now)), "unhealthy": self.unhealthy_reason,
                "last_error": self.last_error, "last_ok": int(self.last_ok * 1000) if self.last_ok else None,
                "quota_headers": self.quota, "timeout_s": self.timeout, "key_help": self.spec.key_help}


class ProviderChain:
    """Groq -> Gemini -> OpenRouter with validation-based failover and usage records."""

    def __init__(self, store=None, vault=None, http=None):
        self.store = store
        self.vault = vault
        self.providers: dict[str, Provider] = {k: Provider(SPECS[k], http) for k in ORDER}
        self.paused = False
        self.load()

    # ---- persistence (keys encrypted) --------------------------------------
    def load(self) -> None:
        if not self.store:
            return
        cfg = self.store.kv_get("ai_providers", {}) or {}
        for pid, c in cfg.items():
            p = self.providers.get(pid)
            if not p:
                continue
            p.enabled = bool(c.get("enabled", True))
            p.model = c.get("model", "") or ""
            p.timeout = float(c.get("timeout_s", 45.0))
            if c.get("key_enc") and self.vault:
                try:
                    p.key = self.vault.dec(base64.b64decode(c["key_enc"]))
                except Exception:  # wrong master key: treat as not configured
                    p.key = ""
        self.paused = bool(self.store.kv_get("ai_paused", False))

    def save(self) -> None:
        if not self.store:
            return
        cfg = {}
        for pid, p in self.providers.items():
            c = {"enabled": p.enabled, "model": p.model, "timeout_s": p.timeout}
            if p.key and self.vault:
                c["key_enc"] = base64.b64encode(self.vault.enc(p.key)).decode()
            cfg[pid] = c
        self.store.kv_set("ai_providers", cfg)
        self.store.kv_set("ai_paused", self.paused)

    def configure(self, pid: str, key: Optional[str] = None, model: Optional[str] = None,
                  enabled: Optional[bool] = None, timeout_s: Optional[float] = None) -> Provider:
        p = self.providers[pid]
        if key is not None:
            p.key = key.strip()
            p.unhealthy_reason = ""
            p.cooldown_until = 0.0
        if model is not None:
            p.model = model.strip()
        if enabled is not None:
            p.enabled = bool(enabled)
        if timeout_s is not None:
            p.timeout = max(5.0, min(120.0, float(timeout_s)))
        self.save()
        return p

    # ---- usage ---------------------------------------------------------------
    def _record(self, pid: str, model: str, ok: bool, tin: int, tout: int, err: str = "", purpose: str = "") -> None:
        if not self.store:
            return
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"ai_usage:{day}"
        u = self.store.kv_get(key, {}) or {}
        r = u.setdefault(pid, {"requests": 0, "ok": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
                                "models": {}})
        r["requests"] += 1
        r["ok" if ok else "errors"] += 1
        r["input_tokens"] += tin
        r["output_tokens"] += tout
        if model:
            r["models"][model] = r["models"].get(model, 0) + 1
        self.store.kv_set(key, u)
        if not ok:
            self.store.x("INSERT INTO ai_errors(ts,provider,model,purpose,error) VALUES(?,?,?,?,?)",
                         (int(time.time() * 1000), pid, model, purpose, scrub(err)))

    def usage_today(self) -> dict[str, Any]:
        if not self.store:
            return {}
        return self.store.kv_get("ai_usage:" + datetime.now(timezone.utc).strftime("%Y-%m-%d"), {}) or {}

    @property
    def any_configured(self) -> bool:
        return any(p.configured for p in self.providers.values())

    # ---- main entry ------------------------------------------------------------
    def complete(self, system: str, user: str, validate: Optional[Callable[[str], Any]] = None,
                 max_tokens: int = 1500, temperature: float = 0.2, purpose: str = "") -> ChainResult:
        res = ChainResult(ok=False)
        if self.paused:
            res.error = "AI paused by owner"
            return res
        for pid in ORDER:
            p = self.providers[pid]
            if not p.configured:
                continue
            if not p.available():
                res.attempts.append({"provider": pid, "skipped": p.unhealthy_reason or "cooling down"})
                continue
            if not p.models and not p.model:
                p.health_check()
                if not p.available():
                    res.attempts.append({"provider": pid, "error": p.unhealthy_reason or p.last_error})
                    continue
            r = p.chat(system, user, max_tokens, temperature)
            if not r.get("ok"):
                p.last_error = r.get("error", "")
                self._record(pid, r.get("model", ""), False, 0, 0, p.last_error, purpose)
                res.attempts.append({"provider": pid, "model": r.get("model"), "error": p.last_error})
                continue
            data = None
            if validate is not None:
                try:
                    data = validate(r["text"])
                except Exception as e:  # incompatible output format -> next provider
                    err = f"invalid output: {e}"
                    self._record(pid, r["model"], False, r["input_tokens"], r["output_tokens"], err, purpose)
                    res.attempts.append({"provider": pid, "model": r["model"], "error": err})
                    continue
            p.last_ok = time.time()
            p.last_error = ""
            self._record(pid, r["model"], True, r["input_tokens"], r["output_tokens"], "", purpose)
            res.attempts.append({"provider": pid, "model": r["model"], "ok": True})
            res.ok, res.text, res.data = True, r["text"], data
            res.provider, res.model = pid, r["model"]
            res.input_tokens, res.output_tokens, res.latency_ms = r["input_tokens"], r["output_tokens"], r["latency_ms"]
            return res
        res.error = "all AI providers unavailable" if res.attempts else "no AI provider configured"
        return res

    def status(self) -> dict[str, Any]:
        return {"order": list(ORDER), "paused": self.paused, "any_configured": self.any_configured,
                "providers": [self.providers[k].status() for k in ORDER], "usage_today": self.usage_today()}


def parse_json_obj(text: str) -> dict[str, Any]:
    """Strict-enough JSON extraction used as the output-format validator."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    d = json.loads(t[a:b + 1])
    if not isinstance(d, dict):
        raise ValueError("reply is not a JSON object")
    return d


class ChainLLM:
    """Adapter so the six agents and five executives use the free provider chain
    (with failover) when it is configured; otherwise the legacy single client."""

    def __init__(self, chain: ProviderChain, legacy=None):
        self.chain = chain
        self.legacy = legacy

    @property
    def available(self) -> bool:
        return (self.chain.any_configured and not self.chain.paused) or bool(self.legacy and self.legacy.available)

    @property
    def max_packet_chars(self) -> int:
        return 12_000 if self.chain.any_configured else getattr(self.legacy, "max_packet_chars", 150_000)

    @property
    def model_override(self) -> str:
        if self.chain.any_configured:
            p = next((self.chain.providers[k] for k in ORDER if self.chain.providers[k].available()), None)
            return f"{p.spec.id}:{p.choose_model()}" if p else "chain (no provider available)"
        return getattr(self.legacy, "model_override", "")

    def describe(self) -> dict[str, Any]:
        if self.chain.any_configured:
            return {"connected": True, "provider": "chain", "model": self.model_override,
                    "base_url": None, "min_gap_s": 0}
        return self.legacy.describe() if self.legacy else {"connected": False}

    def configure(self, *a, **k):  # legacy single-provider settings screen
        return self.legacy.configure(*a, **k)

    def disconnect(self) -> None:
        if self.legacy:
            self.legacy.disconnect()

    def complete(self, system: str, user: str, model: str = "", max_tokens: int = 1500, temperature: float = 0.2):
        from ..org.llm import LLMResult
        if self.chain.any_configured:
            r = self.chain.complete(system, user, max_tokens=max_tokens, temperature=temperature, purpose="agents")
            return LLMResult(r.text, r.input_tokens, r.output_tokens, 0.0 if r.ok else None, r.latency_ms,
                             f"{r.provider}:{r.model}" if r.ok else "chain", None if r.ok else r.error)
        return self.legacy.complete(system, user, model, max_tokens, temperature)
