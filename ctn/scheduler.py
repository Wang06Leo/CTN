"""Bounded OFFLINE loop: no background services, daemons, networking, or threads."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import time
from typing import Callable
from .dedup import InMemoryDeduper
from .decision import OfflineStructuredAgent
from .ports import SocialClient
from .rule_policy import OfflineRulePolicy
from .runner import run_once
from .safety import SlidingWindowLimiter

FAILURES = {"unexpected_failure", "rejected_or_failed"}

def run_offline_loop(
    client: SocialClient, log_path: Path, *, iterations: int = 3,
    interval_seconds: float = 0.0, dry_run: bool = False,
    agent: object | None = None, policy: OfflineRulePolicy | None = None,
    limiter: SlidingWindowLimiter | None = None,
    deduper: InMemoryDeduper | None = None,
    stop_path: Path = Path("STOP"), max_consecutive_failures: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    if type(iterations) is not int or not 1 <= iterations <= 1000:
        raise ValueError("iterations must be between 1 and 1000")
    if not isinstance(interval_seconds, (float, int)) or not 0 <= interval_seconds <= 3600:
        raise ValueError("interval_seconds must be between 0 and 3600")
    if type(max_consecutive_failures) is not int or max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be positive")
    agent = agent if agent is not None else OfflineStructuredAgent()
    limiter = limiter if limiter is not None else SlidingWindowLimiter()
    deduper = deduper if deduper is not None else InMemoryDeduper()
    statuses: Counter[str] = Counter()
    total_observed = 0
    performed = 0
    last_status = None
    last_outcome = None
    reason = "iteration_limit"
    consecutive_failures = 0
    peak_consecutive_failures = 0
    for turn in range(iterations):
        outcome = run_once(client, log_path, dry_run=dry_run, agent=agent,
            policy=policy, limiter=limiter, deduper=deduper, stop_path=stop_path)
        last_outcome = outcome
        performed += 1
        status = outcome["status"]
        last_status = status
        statuses[status] += 1
        total_observed += outcome.get("observed_posts", 0)
        if status == "stopped_by_kill_switch":
            reason = "kill_switch"
            break
        if status in FAILURES:
            consecutive_failures += 1
            peak_consecutive_failures = max(peak_consecutive_failures, consecutive_failures)
            if consecutive_failures >= max_consecutive_failures:
                reason = "failure_threshold"
                break
        elif status == "rate_limited_locally" and consecutive_failures:
            # A throttle is NOT a successful execution (nor another failure).
            # Don't reset the error streak or spin thousands of blocked ticks.
            # Stop safely for offline review; no invented automatic retry delay.
            reason = "rate_limit_after_failure"
            break
        elif status == "rate_limited_locally":
            # Keep v0.3's standalone throttle test behaviour. No request is sent.
            pass
        else:
            # A completed, non-error tick may clear the consecutive error streak.
            consecutive_failures = 0
        if turn + 1 < iterations:
            try:
                sleeper(interval_seconds)
            except KeyboardInterrupt:
                reason = "keyboard_interrupt"
                break
    return {
        "mode": "offline_mock",
        "feedback_type": "local_execution_metrics_only",
        "iterations_requested": iterations,
        "iterations_completed": performed,
        "status_counts": dict(statuses),
        "observed_post_count_total": total_observed,
        "executed_mock_actions": statuses["mock_executed"],
        "stop_reason": reason,
        "consecutive_failures_at_stop": consecutive_failures,
        "peak_consecutive_failures": peak_consecutive_failures,
        "failure_status_count": sum(statuses[code] for code in FAILURES),
        "last_status": last_status,
        "last_outcome": last_outcome,
        "official_score": None,
    }
