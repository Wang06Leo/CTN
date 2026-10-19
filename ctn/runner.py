"""Single offline execution tick, with structured audit fields and safety gates."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from .agent import RuleBasedAgent
from .decision import Decision, DecisionParseError
from .dedup import InMemoryDeduper
from .ports import SocialClient
from .rule_policy import OfflineRulePolicy
from .safety import ActionValidator, SlidingWindowLimiter, ValidationError

def run_once(
    client: SocialClient,
    log_path: Path,
    *,
    dry_run: bool = False,
    agent: object | None = None,
    validator: ActionValidator | None = None,
    limiter: SlidingWindowLimiter | None = None,
    policy: OfflineRulePolicy | None = None,
    deduper: InMemoryDeduper | None = None,
    stop_path: Path = Path("STOP"),
) -> dict:
    """This function is called ONLY with offline clients from main.py."""
    agent = agent if agent is not None else RuleBasedAgent()  # preserve v0.2 API
    validator = validator if validator is not None else ActionValidator()
    limiter = limiter if limiter is not None else SlidingWindowLimiter()
    outcome: dict = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_mock", "dry_run": dry_run,
    }
    try:
        if stop_path.exists():
            outcome["status"] = "stopped_by_kill_switch"
        else:
            posts = client.get_feed()
            outcome["observed_posts"] = len(posts)
            # A Decision holds audit metadata; the legacy AgentAction still works.
            result = agent.decide(posts)
            if isinstance(result, Decision):
                action = result.action
                outcome["decision"] = {"reason_code": result.reason_code,
                    "confidence": result.confidence, "source": result.source}
            else:
                action = result
            outcome["action"] = {
                "kind": action.kind,
                "content_sha256": hashlib.sha256((action.text or "").encode()).hexdigest(),
            }
            if action.kind == "noop":
                outcome["status"] = "no_action"
            else:
                validator.validate(action, posts)
                if policy is not None:
                    policy.validate(action, posts)
                if dry_run:
                    outcome["status"] = "validated_only"
                elif deduper is not None and deduper.seen(action):
                    outcome["status"] = "duplicate_skipped_locally"
                elif not limiter.permit():
                    outcome["status"] = "rate_limited_locally"
                else:
                    outcome["mock_response"] = client.execute(action)
                    if deduper is not None:
                        deduper.record_completed(action)
                    outcome["status"] = "mock_executed"
    except (ValidationError, DecisionParseError, ConnectionError) as exc:
        outcome["status"] = "rejected_or_failed"
        outcome["error_type"] = type(exc).__name__
        # Do not copy unknown server/model error strings (can contain private data).
    except Exception as exc:
        outcome["status"] = "unexpected_failure"
        outcome["error_type"] = type(exc).__name__
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    return outcome
