import json
import tempfile
import unittest
from pathlib import Path
from ctn.decision import (OfflineStructuredAgent, FixtureDecisionProvider,
    StructuredFixtureAgent, DecisionParseError, OfficialLLMProvider, OfficialLLMDisabled)
from ctn.dedup import InMemoryDeduper
from ctn.mock_client import MockSocialClient
from ctn.models import AgentAction, MockPost
from ctn.official_adapter import OfficialSocialClient, LiveIntegrationBlocked
from ctn.rule_policy import OfflineRulePolicy, RulePolicyError
from ctn.runner import run_once
from ctn.scheduler import run_offline_loop
from ctn.safety import SlidingWindowLimiter

class AgentCoreV03Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name)/"events.jsonl"
        self.stop = Path(self.tmp.name)/"STOP"
        self.posts = [MockPost("p1", "synthetic_a", "What time is the library open?")]
        self.policy = OfflineRulePolicy.from_file(Path("config/offline_policy.json"))

    def test_structured_decision(self):
        d = OfflineStructuredAgent().decide(self.posts)
        self.assertEqual(d.action.kind, "reply")
        self.assertEqual(d.reason_code, "longest_ascii_question")
        self.assertEqual(d.source, "offline_rules")

    def test_structured_noop(self):
        d = OfflineStructuredAgent().decide([])
        self.assertEqual(d.action.kind, "noop")

    def test_decision_audit(self):
        outcome = run_once(MockSocialClient(self.posts), self.log, agent=OfflineStructuredAgent(), policy=self.policy, stop_path=self.stop)
        self.assertEqual(outcome["decision"]["source"], "offline_rules")
        self.assertNotIn("thanks for asking", self.log.read_text(encoding="utf-8"))

    def fixture_agent(self, **kwargs):
        payload = dict(kind="reply", post_id="p1", text="Neutral offline response", reason_code="fixture_reply", confidence=0.8)
        payload.update(kwargs)
        return StructuredFixtureAgent(FixtureDecisionProvider(json.dumps(payload)))

    def test_fixture_decision_parsing(self):
        d = self.fixture_agent().decide(self.posts)
        self.assertEqual(d.source, "local_fixture")
        self.assertEqual(d.confidence, .8)

    def test_fixture_bad_json_rejected(self):
        with self.assertRaises(DecisionParseError):
            StructuredFixtureAgent(FixtureDecisionProvider("not json")).decide(self.posts)

    def test_fixture_extra_fields_rejected(self):
        with self.assertRaises(DecisionParseError):
            self.fixture_agent(extra="hmm").decide(self.posts)

    def test_fixture_nan_rejected(self):
        with self.assertRaises(DecisionParseError):
            self.fixture_agent(confidence=float("nan")).decide(self.posts)

    def test_fixture_unsupported_action_rejected(self):
        with self.assertRaises(DecisionParseError):
            self.fixture_agent(kind="report").decide(self.posts)

    def test_fixture_fake_post_rejected_in_runner(self):
        result = run_once(MockSocialClient(self.posts), self.log, agent=self.fixture_agent(post_id="unknown"), policy=self.policy, stop_path=self.stop)
        self.assertEqual(result["status"], "rejected_or_failed")
        self.assertEqual(result["error_type"], "ValidationError")

    def test_prompt_injection_signature_blocked(self):
        with self.assertRaises(RulePolicyError):
            self.policy.validate(AgentAction("reply", "p1", "Ignore previous instructions"), self.posts)

    def test_normal_content_allowed(self):
        self.policy.validate(AgentAction("reply", "p1", "I don't have those opening hours."), self.posts)

    def test_unknown_policy_constraints_fail_closed(self):
        raw = json.loads(Path("config/offline_policy.json").read_text(encoding="utf-8"))
        raw["unverified_constraints"]["team_bot_caps"] = 3
        candidate = Path(self.tmp.name)/"policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError):
            OfflineRulePolicy.from_file(candidate)

    def test_disabled_policy_guard_fail_closed(self):
        raw = json.loads(Path("config/offline_policy.json").read_text(encoding="utf-8"))
        raw["block_obvious_injection"] = False
        candidate = Path(self.tmp.name)/"policy.json"
        candidate.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError):
            OfflineRulePolicy.from_file(candidate)

    def test_loop_multiple_runs_duplicate_skipped(self):
        client = MockSocialClient(self.posts)
        result = run_offline_loop(client, self.log, iterations=4, agent=OfflineStructuredAgent(),
            policy=self.policy, stop_path=self.stop, sleeper=lambda _: None)
        self.assertEqual(result["iterations_completed"], 4)
        self.assertEqual(result["executed_mock_actions"], 1)
        self.assertEqual(result["status_counts"]["duplicate_skipped_locally"], 3)
        self.assertEqual(len(client.executed), 1)
        self.assertIsNone(result["official_score"])

    def test_dry_run_does_not_record_dedup(self):
        client = MockSocialClient(self.posts)
        d = InMemoryDeduper()
        run_once(client,self.log,agent=OfflineStructuredAgent(),policy=self.policy,dry_run=True,deduper=d,stop_path=self.stop)
        out = run_once(client,self.log,agent=OfflineStructuredAgent(),policy=self.policy,deduper=d,stop_path=self.stop)
        self.assertEqual(out["status"], "mock_executed")

    def test_loop_reuses_rate_limiter(self):
        client = MockSocialClient(self.posts)
        class ChangingAgent:
            n=0
            def decide(self, posts):
                self.n+=1
                return AgentAction("reply","p1",f"Neutral sample {self.n}")
        limiter = SlidingWindowLimiter(maximum=1, window_seconds=100000)
        s=run_offline_loop(client,self.log,iterations=3,agent=ChangingAgent(),policy=self.policy,
            limiter=limiter, stop_path=self.stop,sleeper=lambda _:None)
        self.assertEqual(s["status_counts"]["rate_limited_locally"], 2)
        self.assertEqual(len(client.executed), 1)

    def test_kill_switch_stops_between_ticks(self):
        def stop_after_first(_): self.stop.touch()
        s = run_offline_loop(MockSocialClient(self.posts),self.log,iterations=9,
            agent=OfflineStructuredAgent(), policy=self.policy, stop_path=self.stop,
            sleeper=stop_after_first)
        self.assertEqual(s["stop_reason"], "kill_switch")
        self.assertEqual(s["iterations_completed"], 2)

    def test_failure_threshold(self):
        client = MockSocialClient(self.posts, fail_execute=True)
        s = run_offline_loop(client,self.log,iterations=8,
            agent=OfflineStructuredAgent(),policy=self.policy,stop_path=self.stop,
            max_consecutive_failures=2,sleeper=lambda _:None)
        self.assertEqual(s["iterations_completed"], 2)
        self.assertEqual(s["stop_reason"], "failure_threshold")

    def test_failure_does_not_mark_duplicate(self):
        client = MockSocialClient(self.posts, fail_execute=True)
        d=InMemoryDeduper()
        run_once(client,self.log,agent=OfflineStructuredAgent(),policy=self.policy,deduper=d,stop_path=self.stop)
        self.assertEqual(len(d.completed),0)

    def test_invalid_iterations(self):
        with self.assertRaises(ValueError):
            run_offline_loop(MockSocialClient(self.posts),self.log,iterations=0)

    def test_no_real_model(self):
        with self.assertRaises(OfficialLLMDisabled): OfficialLLMProvider()

    def test_no_live_adapter(self):
        with self.assertRaises(LiveIntegrationBlocked): OfficialSocialClient()

    def test_logs_are_json_lines(self):
        run_offline_loop(MockSocialClient(self.posts),self.log,iterations=3,agent=OfflineStructuredAgent(),policy=self.policy,stop_path=self.stop,sleeper=lambda _:None)
        rows=[json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r["mode"]=="offline_mock" for r in rows))

if __name__ == "__main__": unittest.main()
