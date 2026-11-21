"""AI agent framework.

Every agent = deterministic analysis (always runs) + optional Claude review (when an Anthropic key is
configured). Each run is persisted to `agent_runs` with prompt version, input hash, structured
output, latency and token usage. Prompts are versioned files; their SHA-256 is registered in
`agent_prompts` so the history of every prompt change is kept.

Guard rails: an LLM can only *add* tightening proposals (block trader/symbol/currency, risk
multiplier <= 1). Those proposals are validated in code before being applied. No agent can place,
modify or close orders, and no LLM output can loosen a limit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .._util import stable_hash
from ..config import get_settings
from ..models import AgentPrompt, AgentRun

log = logging.getLogger("titan.agents")
PROMPTS = Path(__file__).parent / "prompts"

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "assessment": {"type": "string", "enum": ["low_risk", "moderate_risk", "high_risk", "unknown"]},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "tighten_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["block_trader", "block_symbol", "block_currency", "risk_multiplier"]},
                    "key": {"type": "string"},
                    "value": {"type": "number"},
                    "minutes": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "key", "value", "minutes", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "assessment", "concerns", "recommendations", "tighten_actions"],
    "additionalProperties": False,
}


class Agent:
    name: str = ""
    version: str = "1.0.0"
    title: str = ""

    @property
    def prompt(self) -> str:
        return (PROMPTS / f"{self.name}.md").read_text()

    def analyze(self, s: Session, **kw) -> dict:  # deterministic core
        raise NotImplementedError

    def subject(self, **kw) -> str | None:
        return None

    def run(self, s: Session, use_llm: bool = True, **kw) -> dict:
        register_prompt(s, self)
        t0 = time.time()
        out: dict[str, Any] = {}
        err = None
        llm_used, model, tin, tout = False, None, 0, 0
        try:
            out = self.analyze(s, **kw)
        except Exception as e:
            log.exception("agent %s failed", self.name)
            err = f"{type(e).__name__}: {e}"
            out = {"error": err}
        if use_llm and err is None and llm_available():
            try:
                review, model, tin, tout = llm_review(self.prompt, out)
                out["llm_review"] = review
                out["applied_tighten_actions"] = []
                llm_used = True
            except Exception as e:
                out["llm_review"] = {"error": f"{type(e).__name__}: {e}"}
        elif use_llm and not llm_available():
            out["llm_review"] = {"status": "LLM offline (no ANTHROPIC_API_KEY): deterministic analysis only"}
        run = AgentRun(agent=self.name, version=self.version, subject=self.subject(**kw),
                       input_hash=stable_hash({k: str(v) for k, v in kw.items()}),
                       output=json.loads(json.dumps(out, default=str)), llm_used=llm_used, llm_model=model,
                       duration_ms=(time.time() - t0) * 1000, input_tokens=tin, output_tokens=tout, error=err)
        s.add(run)
        s.flush()
        out["_run_id"] = run.id
        return out


def register_prompt(s: Session, agent: Agent) -> None:
    text = agent.prompt
    h = hashlib.sha256(text.encode()).hexdigest()
    row = s.scalar(select(AgentPrompt).where(AgentPrompt.agent == agent.name, AgentPrompt.version == agent.version))
    if row is None:
        s.add(AgentPrompt(agent=agent.name, version=agent.version, sha256=h, prompt=text))
    elif row.sha256 != h:
        # prompt text changed without a version bump: keep history under a derived version id
        alt = f"{agent.version}+{h[:8]}"
        if not s.scalar(select(AgentPrompt).where(AgentPrompt.agent == agent.name, AgentPrompt.version == alt)):
            s.add(AgentPrompt(agent=agent.name, version=alt, sha256=h, prompt=text))
    s.flush()


def llm_available() -> bool:
    st = get_settings()
    return bool(st.llm_enabled and st.anthropic_api_key)


def llm_review(system_prompt: str, analysis: dict) -> tuple[dict, str, int, int]:
    """Claude review of a deterministic analysis -> validated structured output."""
    import anthropic

    st = get_settings()
    client = anthropic.Anthropic(api_key=st.anthropic_api_key, max_retries=2, timeout=120)
    content = ("Deterministic analysis (JSON; null means UNKNOWN - do not estimate it):\n"
               + json.dumps(analysis, default=str, indent=1)[:150_000]
               + "\n\nReturn your review. tighten_actions may only block or reduce risk (risk_multiplier value "
                 "between 0 and 1); leave it empty if none are warranted.")
    resp = client.beta.messages.create(
        model=st.llm_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": st.llm_effort, "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined to review this analysis")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("review truncated (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    review = json.loads(text)
    review["tighten_actions"] = [a for a in review.get("tighten_actions", []) if valid_tighten(a)]
    return review, resp.model, resp.usage.input_tokens, resp.usage.output_tokens


def valid_tighten(a: dict) -> bool:
    if a.get("kind") not in ("block_trader", "block_symbol", "block_currency", "risk_multiplier"):
        return False
    if a["kind"] == "risk_multiplier" and not (0 <= float(a.get("value", 2)) <= 1):
        return False
    return 0 < float(a.get("minutes", 0)) <= 7 * 24 * 60
