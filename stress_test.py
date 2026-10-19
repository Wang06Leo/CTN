"""Offline-only stress harness for CTN 2026 Agent Core v0.3.1.

Run from the project root: python stress_test.py
Uses synthetic posts and local clients only. Does not call any network API.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import time
import tracemalloc

from ctn.decision import OfflineStructuredAgent
from ctn.mock_client import MockSocialClient
from ctn.models import AgentAction, MockPost
from ctn.rule_policy import OfflineRulePolicy
from ctn.scheduler import run_offline_loop

OUTPUT_DIR = Path("logs/stress_v031")


class ChangingFeedClient:
    """Each get_feed() returns a different neutral, synthetic question."""

    def __init__(self) -> None:
        self.count = 0
        self.executed: list[AgentAction] = []

    def get_feed(self) -> list[MockPost]:
        self.count += 1
        return [MockPost(f"synthetic-{self.count}", "offline_bot", "Where is the library?")]

    def execute(self, action: AgentAction) -> dict[str, str]:
        self.executed.append(action)
        return {"mock_action_id": f"sim-{len(self.executed)}", "status": "mock_only"}


def run_trial(scenario: str, iterations: int, policy: OfflineRulePolicy) -> dict:
    if scenario == "static_feed":
        client = MockSocialClient([MockPost("constant-1", "offline_bot", "Where is the library?")])
        dry_run = False
        max_failures = 3
    elif scenario == "changing_feed":
        client = ChangingFeedClient()
        dry_run = False
        max_failures = 3
    elif scenario == "dry_run":
        client = ChangingFeedClient()
        dry_run = True
        max_failures = 3
    elif scenario in ("failure_threshold_2", "failure_masked_fixed"):
        client = MockSocialClient([MockPost("error-1", "offline_bot", "Where is the library?")], fail_execute=True)
        dry_run = False
        max_failures = 2 if scenario == "failure_threshold_2" else 3
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    log_path = OUTPUT_DIR / f"{scenario}_{iterations}.jsonl"
    log_path.unlink(missing_ok=True)  # start a fresh local experiment, do not append previous runs

    tracemalloc.start()
    begin = time.perf_counter()
    try:
        summary = run_offline_loop(
            client,
            log_path,
            iterations=iterations,
            interval_seconds=0,
            max_consecutive_failures=max_failures,
            agent=OfflineStructuredAgent(),
            policy=policy,
            dry_run=dry_run,
        )
        elapsed = time.perf_counter() - begin
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    counted_lines = sum(1 for _ in log_path.open(encoding="utf-8"))
    assert counted_lines == summary["iterations_completed"], "Audit log line count mismatch"
    assert len(client.executed) == summary["executed_mock_actions"], "Mock execution count mismatch"

    counts = summary["status_counts"]
    if scenario == "static_feed":
        assert counts == {"mock_executed": 1, "duplicate_skipped_locally": iterations - 1}
        assert summary["stop_reason"] == "iteration_limit"
    elif scenario == "changing_feed":
        assert counts == {"mock_executed": 2, "rate_limited_locally": iterations - 2}
        assert summary["stop_reason"] == "iteration_limit"
    elif scenario == "dry_run":
        assert counts == {"validated_only": iterations}
        assert summary["stop_reason"] == "iteration_limit"
    elif scenario == "failure_threshold_2":
        assert counts == {"rejected_or_failed": 2}
        assert summary["stop_reason"] == "failure_threshold"
        assert summary["iterations_completed"] == 2
    elif scenario == "failure_masked_fixed":
        assert counts == {"rejected_or_failed": 2, "rate_limited_locally": 1}
        assert summary["stop_reason"] == "rate_limit_after_failure"
        assert summary["iterations_completed"] == 3
        assert summary["consecutive_failures_at_stop"] == 2

    return {
        "scenario": scenario,
        "iterations_requested": iterations,
        "iterations_completed": summary["iterations_completed"],
        "status_counts": counts,
        "mock_executed": summary["executed_mock_actions"],
        "stop_reason": summary["stop_reason"],
        "failure_status_count": summary["failure_status_count"],
        "log_lines": counted_lines,
        "elapsed_seconds": round(elapsed, 4),
        "python_peak_kib": round(peak / 1024, 1),
        "official_score": summary["official_score"],
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    policy = OfflineRulePolicy.from_file(Path("config/offline_policy.json"))
    trials = [(scenario, n) for scenario in ("static_feed", "changing_feed", "dry_run") for n in (100, 300, 500)]
    trials.append(("failure_threshold_2", 500))
    trials.append(("failure_masked_fixed", 500))
    records: list[dict] = []
    for scenario, count in trials:
        row = run_trial(scenario, count, policy)
        records.append(row)
        print(f"{scenario:19} requested={count:3} completed={row['iterations_completed']:3} "
              f"status={row['status_counts']}  elapsed={row['elapsed_seconds']:.4f}s "
              f"peak={row['python_peak_kib']:.1f}KiB")

    (OUTPUT_DIR / "stress_report.json").write_text(
        json.dumps({"harness": "offline_v031", "trials": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (OUTPUT_DIR / "stress_report.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        for item in records:
            writer.writerow({**item, "status_counts": json.dumps(item["status_counts"], ensure_ascii=False)})
    print("All OFFLINE checks passed. Reports: logs/stress_v031/stress_report.{json,csv}")


if __name__ == "__main__":
    main()
