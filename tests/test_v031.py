"""v0.3.1 regression tests for priority selection and masked failure handling."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctn.agent import RuleBasedAgent
from ctn.decision import OfflineStructuredAgent
from ctn.mock_client import MockSocialClient
from ctn.models import AgentAction, MockPost
from ctn.rule_policy import OfflineRulePolicy
from ctn.runner import run_once
from ctn.scheduler import run_offline_loop
from ctn.safety import SlidingWindowLimiter
from ctn.selection import select_longest_ascii_question


class OfflineSelectionV031Tests(unittest.TestCase):
    def setUp(self):
        self.posts = [
            MockPost("short", "a", "What?"),
            MockPost("only-chinese", "b", "这是一条很长的长长长句子，只有中文问号？"),
            MockPost("long", "c", "Could you tell me where the library is located and when it opens?"),
            MockPost("no-question", "d", "A" * 300),
        ]

    def test_longest_ascii_question_ignores_long_non_questions(self):
        self.assertEqual(select_longest_ascii_question(self.posts).id, "long")

    def test_offline_agent_uses_longest_question(self):
        result = OfflineStructuredAgent().decide(self.posts)
        self.assertEqual(result.action.post_id, "long")
        self.assertEqual(result.reason_code, "longest_ascii_question")

    def test_legacy_agent_follows_same_priority(self):
        self.assertEqual(RuleBasedAgent().decide(self.posts).post_id, "long")

    def test_fullwidth_chinese_question_mark_alone_is_not_eligible(self):
        posts = [MockPost("zh", "x", "这个问题只用中文问号？")]
        self.assertIsNone(select_longest_ascii_question(posts))
        self.assertEqual(OfflineStructuredAgent().decide(posts).action.kind, "noop")

    def test_mixed_text_ascii_question_mark_is_eligible(self):
        posts = [MockPost("mix", "x", "一个混合语句包含英文问号?")]
        self.assertEqual(OfflineStructuredAgent().decide(posts).action.post_id, "mix")

    def test_equal_length_keeps_first_in_feed_order(self):
        posts = [MockPost("first", "x", "Why?"), MockPost("second", "x", "How?")]
        self.assertEqual(select_longest_ascii_question(posts).id, "first")
        self.assertEqual(select_longest_ascii_question(list(reversed(posts))).id, "second")

    def test_empty_feed_is_noop(self):
        self.assertIsNone(select_longest_ascii_question([]))
        self.assertEqual(OfflineStructuredAgent().decide([]).action.kind, "noop")

    def test_runner_executes_selected_longer_post(self):
        with tempfile.TemporaryDirectory() as temp:
            client = MockSocialClient(self.posts)
            outcome = run_once(client, Path(temp) / "log.jsonl",
                               agent=OfflineStructuredAgent(),
                               policy=OfflineRulePolicy.from_file(Path('config/offline_policy.json')),
                               stop_path=Path(temp) / "STOP")
            self.assertEqual(outcome["status"], "mock_executed")
            self.assertEqual(client.executed[0].post_id, "long")


class RateLimitFailureV031Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "log.jsonl"
        self.stop = Path(self.tmp.name) / "STOP"
        self.posts = [MockPost("p1", "a", "Where?")]
        self.policy = OfflineRulePolicy.from_file(Path('config/offline_policy.json'))

    def test_rate_limit_after_failures_stops_instead_of_masking(self):
        client = MockSocialClient(self.posts, fail_execute=True)
        result = run_offline_loop(client, self.log, iterations=100,
                                 max_consecutive_failures=3,
                                 agent=OfflineStructuredAgent(), policy=self.policy,
                                 stop_path=self.stop, sleeper=lambda _: None)
        self.assertEqual(result["stop_reason"], "rate_limit_after_failure")
        self.assertEqual(result["iterations_completed"], 3)
        self.assertEqual(result["status_counts"], {
            "rejected_or_failed": 2,
            "rate_limited_locally": 1,
        })
        self.assertEqual(result["consecutive_failures_at_stop"], 2)
        self.assertEqual(result["failure_status_count"], 2)

    def test_actual_third_failure_after_cooldown_reaches_threshold(self):
        fake_time = [1000.0]
        limiter = SlidingWindowLimiter(maximum=2, window_seconds=60,
                                       clock=lambda: fake_time[0])
        # One simulated minute passes between ticks, so each attempted call is
        # actually executed (and fails), rather than being blocked by limiter.
        def advance_clock(seconds):
            fake_time[0] += 61

        result = run_offline_loop(MockSocialClient(self.posts, fail_execute=True),
                                 self.log, iterations=100, max_consecutive_failures=3,
                                 interval_seconds=1, agent=OfflineStructuredAgent(),
                                 policy=self.policy, limiter=limiter,
                                 stop_path=self.stop, sleeper=advance_clock)
        self.assertEqual(result["stop_reason"], "failure_threshold")
        self.assertEqual(result["iterations_completed"], 3)
        self.assertEqual(result["status_counts"], {"rejected_or_failed": 3})
        self.assertEqual(result["consecutive_failures_at_stop"], 3)

    def test_unrelated_rate_limit_does_not_claim_error(self):
        class DifferentTextAgent:
            i = 0
            def decide(self, posts):
                self.i += 1
                return AgentAction("reply", "p1", f"Neutral synthetic text {self.i}")

        limiter = SlidingWindowLimiter(maximum=1, window_seconds=60,
                                       clock=lambda: 1000.0)
        client = MockSocialClient(self.posts)
        result = run_offline_loop(client, self.log, iterations=5,
                                 agent=DifferentTextAgent(), policy=self.policy,
                                 limiter=limiter, stop_path=self.stop,
                                 sleeper=lambda _: None)
        self.assertEqual(result["stop_reason"], "iteration_limit")
        self.assertEqual(result["failure_status_count"], 0)
        self.assertEqual(result["consecutive_failures_at_stop"], 0)
        self.assertEqual(result["status_counts"], {
            "mock_executed": 1, "rate_limited_locally": 4,
        })

    def test_success_resets_prior_failure_streak(self):
        class SometimesFailsClient(MockSocialClient):
            count = 0
            def execute(self, action):
                self.count += 1
                if self.count in (1, 3):
                    raise ConnectionError("Local intermittent simulated failure")
                return super().execute(action)

        fake_time = [1000.0]
        limiter = SlidingWindowLimiter(maximum=100, window_seconds=60,
                                       clock=lambda: fake_time[0])
        class ChangingTextAgent:
            i = 0
            def decide(self, posts):
                self.i += 1
                return AgentAction("reply", "p1", f"Neutral changing text {self.i}")

        client = SometimesFailsClient(self.posts)
        result = run_offline_loop(client, self.log, iterations=4,
                                 max_consecutive_failures=2,
                                 agent=ChangingTextAgent(), policy=self.policy,
                                 limiter=limiter, stop_path=self.stop,
                                 sleeper=lambda _: None)
        # Fail -> success -> fail -> success; no consecutive pair of failures.
        self.assertEqual(result["stop_reason"], "iteration_limit")
        self.assertEqual(result["status_counts"], {"rejected_or_failed": 2, "mock_executed": 2})
        self.assertEqual(result["failure_status_count"], 2)
        self.assertEqual(result["peak_consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
