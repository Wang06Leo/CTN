import json
import tempfile
import unittest
from pathlib import Path
from ctn.agent import RuleBasedAgent
from ctn.models import AgentAction, MockPost
from ctn.mock_client import MockSocialClient
from ctn.runner import run_once
from ctn.safety import ActionValidator, SlidingWindowLimiter, ValidationError


class OfflinePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "events.jsonl"
        self.posts = [MockPost("p1", "test_user", "Where is the library?")]

    def test_pipeline_executes_only_in_mock(self):
        client = MockSocialClient(self.posts)
        result = run_once(client, self.log)
        self.assertEqual(result["status"], "mock_executed")
        self.assertEqual(len(client.executed), 1)
        self.assertEqual(result["action"]["kind"], "reply")
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_dry_run_does_not_execute(self):
        client = MockSocialClient(self.posts)
        result = run_once(client, self.log, dry_run=True)
        self.assertEqual(result["status"], "validated_only")
        self.assertEqual(client.executed, [])

    def test_no_questions_produces_noop(self):
        client = MockSocialClient([MockPost("p2", "x", "Statement without question")])
        result = run_once(client, self.log)
        self.assertEqual(result["status"], "no_action")
        self.assertEqual(client.executed, [])

    def test_empty_feed_produces_noop(self):
        result = run_once(MockSocialClient([]), self.log)
        self.assertEqual(result["status"], "no_action")

    def test_invalid_post_id_rejected(self):
        with self.assertRaises(ValidationError):
            ActionValidator().validate(AgentAction("reply", "not-present", "Hello"), self.posts)

    def test_long_text_rejected(self):
        with self.assertRaises(ValidationError):
            ActionValidator(max_chars=3).validate(AgentAction("reply", "p1", "Four"), self.posts)

    def test_network_failure_simulated_and_logged(self):
        client = MockSocialClient(self.posts, fail_execute=True)
        result = run_once(client, self.log)
        self.assertEqual(result["status"], "rejected_or_failed")
        self.assertEqual(result["error_type"], "ConnectionError")
        self.assertEqual(client.executed, [])
        self.assertEqual(json.loads(self.log.read_text().strip())["status"], "rejected_or_failed")

    def test_local_rate_limiter(self):
        clock = [100.0]
        limiter = SlidingWindowLimiter(maximum=2, window_seconds=60, clock=lambda: clock[0])
        self.assertTrue(limiter.permit())
        self.assertTrue(limiter.permit())
        self.assertFalse(limiter.permit())
        clock[0] = 161.0
        self.assertTrue(limiter.permit())

    def test_rate_limit_blocks_execution(self):
        client = MockSocialClient(self.posts)
        limiter = SlidingWindowLimiter(maximum=0)
        result = run_once(client, self.log, limiter=limiter)
        self.assertEqual(result["status"], "rate_limited_locally")
        self.assertEqual(client.executed, [])

    def test_rejects_non_whitelisted_executable_action(self):
        with self.assertRaises(ValidationError):
            ActionValidator().validate(AgentAction("noop"), self.posts)


if __name__ == "__main__":
    unittest.main()
