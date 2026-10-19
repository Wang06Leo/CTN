import json
import tempfile
import unittest
from pathlib import Path
from ctn.mock_client import MockSocialClient
from ctn.models import MockPost
from ctn.official_adapter import OfficialSocialClient, LiveIntegrationBlocked
from ctn.preflight import check_preflight, REQUIRED
from ctn.replay_client import ReplaySocialClient
from ctn.runner import run_once

class TestAdapterBoundary(unittest.TestCase):
    def test_live_cannot_be_instantiated(self):
        with self.assertRaises(LiveIntegrationBlocked):
            OfficialSocialClient()

    def test_preflight_fails_closed_by_default(self):
        data = check_preflight(Path("config/preflight.json"))
        self.assertFalse(data["ready_for_review"])
        self.assertEqual(len(data["missing"]), len(REQUIRED))

    def test_preflight_partial_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"pf.json"
            p.write_text(json.dumps({REQUIRED[0]:True}),encoding="utf-8")
            self.assertFalse(check_preflight(p)["ready_for_review"])

    def test_fixture_is_only_normalized_local_data(self):
        client = ReplaySocialClient.from_file(Path("fixtures/neutral_demo.json"))
        self.assertEqual(len(client.get_feed()), 2)

    def test_replay_invalid_shape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"bad.json"
            p.write_text('{"posts":[{"id":42,"author":"a","text":"b"}]}',encoding="utf-8")
            with self.assertRaises(ValueError):
                ReplaySocialClient.from_file(p)

    def test_dry_run_does_not_write_mock_action(self):
        with tempfile.TemporaryDirectory() as d:
            client=MockSocialClient([MockPost("p1", "user", "Any news?")])
            outcome=run_once(client,Path(d)/"events.jsonl",dry_run=True)
            self.assertEqual(outcome["status"],"validated_only")
            self.assertEqual(client.executed,[])

    def test_log_never_contains_reply_text(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"events.jsonl"
            client=MockSocialClient([MockPost("p1", "user", "Any news?")])
            run_once(client,p)
            self.assertNotIn("thanks for asking",p.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
