"""CTN 2026 v0.3.1. All supported execution modes are strictly OFFLINE."""
import argparse
import json
from pathlib import Path
from ctn.agent import RuleBasedAgent
from ctn.decision import FixtureDecisionProvider, OfflineStructuredAgent, StructuredFixtureAgent
from ctn.mock_client import MockSocialClient
from ctn.models import MockPost
from ctn.preflight import check_preflight
from ctn.replay_client import ReplaySocialClient
from ctn.rule_policy import OfflineRulePolicy
from ctn.scheduler import run_offline_loop

def main() -> None:
    parser = argparse.ArgumentParser(description="CTN 2026 OFFLINE MVP v0.3.1")
    parser.add_argument("--mode", choices=("mock", "replay", "live"), default="mock")
    parser.add_argument("--agent", choices=("structured", "fixture", "legacy"), default="structured")
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/neutral_demo.json"))
    parser.add_argument("--decision-fixture", type=Path, default=Path("fixtures/model_decision_demo.json"))
    parser.add_argument("--offline-policy", type=Path, default=Path("config/offline_policy.json"))
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--empty-feed", action="store_true")
    parser.add_argument("--simulate-error", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--log", type=Path, default=Path("logs/events.jsonl"))
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(check_preflight(Path("config/preflight.json")), indent=2))
        return
    if args.mode == "live":
        from ctn.official_adapter import OfficialSocialClient
        OfficialSocialClient()  # raises before any network session
        return
    policy = OfflineRulePolicy.from_file(args.offline_policy)
    if args.mode == "replay":
        client = ReplaySocialClient.from_file(args.fixture)
    else:
        posts = [] if args.empty_feed else [
            MockPost("demo-101", "mock_user_a", "Where can I find the library hours?"),
            MockPost("demo-102", "mock_user_b", "The local art club meets on Friday."),
        ]
        client = MockSocialClient(posts=posts, fail_execute=args.simulate_error)
    if args.agent == "structured":
        agent = OfflineStructuredAgent()
    elif args.agent == "legacy":
        agent = RuleBasedAgent()
    else:
        # Model output is a static local JSON file. NO external LLM is contacted.
        agent = StructuredFixtureAgent(FixtureDecisionProvider(args.decision_fixture.read_text(encoding="utf-8")))
    summary = run_offline_loop(client, args.log, iterations=args.iterations,
        interval_seconds=args.interval_seconds, max_consecutive_failures=args.max_failures,
        dry_run=args.dry_run, agent=agent, policy=policy)
    # Preserve v0.2's single-run output shape for readability.
    result = summary["last_outcome"] if args.iterations == 1 else summary
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if summary["stop_reason"] in ("failure_threshold", "rate_limit_after_failure") or (
            args.iterations == 1 and summary["last_status"] in ("rejected_or_failed", "unexpected_failure")):
        raise SystemExit(1)

if __name__ == "__main__":
    main()
