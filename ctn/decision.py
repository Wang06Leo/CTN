"""Agent decision contract. All model-like responses are LOCAL fixtures.

No real LLM prompts or external network requests in this module.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
import re
from typing import Protocol
from .models import AgentAction, MockPost
from .selection import select_longest_ascii_question

class DecisionParseError(ValueError):
    pass

@dataclass(frozen=True)
class Decision:
    action: AgentAction
    reason_code: str
    confidence: float
    source: str

class DecisionAgent(Protocol):
    def decide(self, posts: list[MockPost]) -> Decision: ...

class OfflineStructuredAgent:
    """Neutral, deterministic rules: useful for verifying the orchestration only."""
    def decide(self, posts: list[MockPost]) -> Decision:
        post = select_longest_ascii_question(posts)
        if post is not None:
            return Decision(
                AgentAction("reply", post.id,
                    "Offline test response: thanks for asking. This is simulated content."),
                reason_code="longest_ascii_question", confidence=0.55, source="offline_rules"
            )
        return Decision(AgentAction("noop"), "no_ascii_question", 1.0, "offline_rules")

class DecisionTextProvider(Protocol):
    def generate(self, posts: list[MockPost]) -> str: ...

@dataclass(frozen=True)
class FixtureDecisionProvider:
    """Simulates the untrusted TEXT that a model might return; never calls a model."""
    response_text: str
    def generate(self, posts: list[MockPost]) -> str:
        return self.response_text

class OfficialLLMDisabled(RuntimeError):
    pass

class OfficialLLMProvider:
    def __init__(self, *args: object, **kwargs: object):
        raise OfficialLLMDisabled("OFFICIAL LLM DISABLED: 2026 model/quotas/authorization unverified")

class StructuredFixtureAgent:
    """Parse a LOCAL mock-model JSON completion. Treat model text as untrusted."""
    def __init__(self, provider: DecisionTextProvider):
        self.provider = provider

    def decide(self, posts: list[MockPost]) -> Decision:
        try:
            raw = json.loads(self.provider.generate(posts))
        except (ValueError, TypeError) as exc:
            raise DecisionParseError("Fixture model response is not valid JSON") from exc
        expected = {"kind", "post_id", "text", "reason_code", "confidence"}
        if not isinstance(raw, dict) or raw.keys() != expected:
            raise DecisionParseError("Decision must have exactly the required fields")
        kind, post_id, reply_text = raw["kind"], raw["post_id"], raw["text"]
        if not isinstance(kind, str) or kind not in ("reply", "noop"):
            raise DecisionParseError("Unsupported action type")
        if post_id is not None and (not isinstance(post_id, str) or not post_id):
            raise DecisionParseError("Invalid post_id type")
        if reply_text is not None and not isinstance(reply_text, str):
            raise DecisionParseError("Invalid reply text type")
        if kind == "noop" and (post_id is not None or reply_text is not None):
            raise DecisionParseError("Noop cannot carry action parameters")
        reason = raw["reason_code"]
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", reason):
            raise DecisionParseError("Invalid reason_code")
        conf = raw["confidence"]
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
            raise DecisionParseError("Confidence must be finite and in [0,1]")
        return Decision(AgentAction(kind, post_id, reply_text), reason, float(conf), "local_fixture")
