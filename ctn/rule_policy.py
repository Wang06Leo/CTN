"""Offline policy gate. Public rule references + conservative local heuristics.

NOT a certification of competition compliance; official unknowns remain unresolved.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import re
from .models import AgentAction, MockPost
from .safety import ActionValidator, ValidationError

class RulePolicyError(ValidationError):
    pass

# Heuristic red flags; neither exhaustive nor an official classifier.
PROMPT_INJECTION_SIGNATURES = (
    re.compile(r"ignore (?:all |any )?(?:previous|prior) instructions", re.I),
    re.compile(r"\[im_start\]", re.I),
    re.compile(r"<\|im_start\|>", re.I),
    re.compile(r"(?:reveal|print|dump) (?:your |the )?(?:system prompt|developer message)", re.I),
    re.compile(r"(?:you are now|act as) (?:the |a )?system (?:message|administrator)", re.I),
)

@dataclass(frozen=True)
class OfflineRulePolicy:
    mock_max_chars: int = 200
    block_obvious_injection: bool = True

    @classmethod
    def from_file(cls, path: Path) -> "OfflineRulePolicy":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {
            "offline_only", "mock_max_chars", "block_obvious_injection", "reference", "unverified_constraints"
        }:
            raise ValueError("Malformed offline policy manifest")
        if data["offline_only"] is not True:
            raise ValueError("Policy must remain offline_only")
        n = data["mock_max_chars"]
        if type(n) is not int or not 1 <= n <= 10000:
            raise ValueError("Invalid mock_max_chars")
        if data["block_obvious_injection"] is not True:
            raise ValueError("Public-rule prompt injection check cannot be disabled in this harness")
        if not isinstance(data["unverified_constraints"], dict) or not data["unverified_constraints"] or any(
            value is not None for value in data["unverified_constraints"].values()
        ):
            raise ValueError("Unknown official constraints must remain null")
        return cls(n, True)

    def validate(self, action: AgentAction, posts: list[MockPost]) -> None:
        # This local allowlist intentionally does not include REPORT actions.
        ActionValidator(max_chars=self.mock_max_chars).validate(action, posts)
        if self.block_obvious_injection and any(pattern.search(action.text or "") for pattern in PROMPT_INJECTION_SIGNATURES):
            raise RulePolicyError("Blocked by local potential-prompt-injection heuristic")
