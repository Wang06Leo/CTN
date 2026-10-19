"""v0.4.2 integrated tests: all deterministic, no SDK, internet or credentials."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from ctn.integrated_readonly import run_readonly_cycle, run_readonly_cycles
from ctn.models import MockPost
from ctn.official_readonly import OfficialReadOnlySocialClient
from ctn.review_state import ReadCooldown, ReviewStore
from ctn.replay_client import ReplaySocialClient
from ctn.rule_policy import OfflineRulePolicy

POLICY = OfflineRulePolicy.from_file(Path('config/offline_policy.json'))


class MemoryFeed:
    def __init__(self, items):
        self.items = items
        self.calls = 0
        self.write_attempts = 0
        self.rate_limit = Mock(limit=10, remaining=9, reset=54)

    def feed_trending(self, limit=10, cursor=None):
        self.calls += 1
        return self.items[:limit]

    feed_latest = feed_trending

    def reply(self, *args, **kwargs):
        self.write_attempts += 1
        raise AssertionError('a REAL WRITE was unexpectedly called')


class FailureFeed:
    def get_feed(self):
        raise ConnectionError('PRIVATE data must not leak into logs')


class IntegratedV042Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name)
        self.db = self.path / 'state.sqlite3'
        self.log = self.path / 'events.jsonl'
        self.stop = self.path / 'STOP'
        self.posts = [
            MockPost('short', '1', 'Where?'),
            MockPost('long', '2', 'Can you explain where the library opening hours are published?'),
            MockPost('chinese', '3', ('中文问题但是只有中文符号' * 20) + '？'),
            MockPost('noquestion', '4', 'very long statement ' * 25),
        ]

    def tearDown(self):
        self.td.cleanup()

    def mkclient(self):
        class Fake:
            def __init__(self, posts):
                self.posts = posts
                self.execute_calls = 0
            def get_feed(self):
                return list(self.posts)
            def execute(self, *args):
                self.execute_calls += 1
                raise AssertionError('NO EXECUTION ALLOWED')
        return Fake(self.posts)

    def test_choice_longest_ascii_question_and_zero_writes(self):
        c = self.mkclient()
        with ReviewStore(self.db) as store:
            result = run_readonly_cycle(c, store, self.log, source='mock', stop_path=self.stop, policy=POLICY)
        self.assertEqual(result['selected_post']['post_id'], 'long')
        self.assertEqual(result['ascii_question_candidates'], 2)
        self.assertEqual(result['status'], 'validated_proposal_only')
        self.assertEqual(c.execute_calls, 0)
        self.assertEqual(result['writes_attempted'], 0)

    def test_default_does_not_mark_reviewed(self):
        c = self.mkclient()
        with ReviewStore(self.db) as store:
            run_readonly_cycle(c, store, self.log, source='mock', stop_path=self.stop)
            run_readonly_cycle(c, store, self.log, source='mock', stop_path=self.stop)
            self.assertEqual(store.count_reviewed(), 0)

    def test_explicit_review_then_next_candidate(self):
        c = self.mkclient()
        with ReviewStore(self.db) as store:
            first = run_readonly_cycle(c, store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            second = run_readonly_cycle(c, store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            third = run_readonly_cycle(c, store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            self.assertEqual(store.count_reviewed(), 2)
        self.assertEqual(first['selected_post']['post_id'], 'long')
        self.assertEqual(second['selected_post']['post_id'], 'short')
        self.assertEqual(third['status'], 'no_unreviewed_candidate')
        self.assertEqual(c.execute_calls, 0)

    def test_persistent_across_two_store_instances(self):
        with ReviewStore(self.db) as store:
            run_readonly_cycle(self.mkclient(), store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
        with ReviewStore(self.db) as store:
            value = run_readonly_cycle(self.mkclient(), store, self.log, source='mock', stop_path=self.stop)
            self.assertEqual(store.count_reviewed(), 1)
        self.assertEqual(value['selected_post']['post_id'], 'short')

    def test_review_is_not_marked_as_replied(self):
        with ReviewStore(self.db) as store:
            run_readonly_cycle(self.mkclient(), store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            columns = [item[1] for item in store.connection.execute('PRAGMA table_info(reviewed_posts)')]
        self.assertNotIn('reply_id', columns)
        self.assertNotIn('executed', columns)

    def test_log_has_no_body_or_reply_text(self):
        with ReviewStore(self.db) as store:
            run_readonly_cycle(self.mkclient(), store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
        data = self.log.read_text(encoding='utf-8')
        self.assertNotIn('library opening hours', data)
        self.assertNotIn('Offline test response', data)
        self.assertNotIn('中文问题', data)
        self.assertEqual(len(data.splitlines()), 1)

    def test_sqlite_conservative_cross_restart_cooldown(self):
        fake_time = [1000.]
        with ReviewStore(self.db, clock=lambda: fake_time[0]) as store:
            store.reserve_feed_read(65)
            with self.assertRaises(ReadCooldown) as ctx:
                store.reserve_feed_read(65)
            self.assertAlmostEqual(ctx.exception.seconds, 65)
        fake_time[0] = 1064.
        with ReviewStore(self.db, clock=lambda: fake_time[0]) as store:
            with self.assertRaises(ReadCooldown):
                store.reserve_feed_read(65)
        fake_time[0] = 1066.
        with ReviewStore(self.db, clock=lambda: fake_time[0]) as store:
            store.reserve_feed_read(65)

    def test_official_protected_by_read_gap_and_no_execute(self):
        raw = [dict(post_id=1, author_id=9, body='Where is my library?')]
        fake = MemoryFeed(raw)
        client = OfficialReadOnlySocialClient(social=fake, limit=10)
        t = [100.]
        with ReviewStore(self.db, clock=lambda: t[0]) as store:
            a = run_readonly_cycle(client, store, self.log, source='trending', reserve_official_read=True, stop_path=self.stop)
            b = run_readonly_cycle(client, store, self.log, source='trending', reserve_official_read=True, stop_path=self.stop)
        self.assertEqual(a['status'], 'validated_proposal_only')
        self.assertEqual(a['official_network_calls'], 1)
        self.assertEqual(a['rate_limit']['remaining'], 9)
        self.assertEqual(b['status'], 'local_read_cooldown')
        self.assertEqual(fake.calls, 1)
        self.assertEqual(fake.write_attempts, 0)

    def test_stop_prevents_read_and_review(self):
        self.stop.write_text('', encoding='utf-8')
        client = self.mkclient()
        with ReviewStore(self.db) as store:
            out = run_readonly_cycle(client, store, self.log, source='mock', record_reviewed=True, stop_path=self.stop)
            self.assertEqual(store.count_reviewed(), 0)
        self.assertEqual(out['status'], 'stopped_by_kill_switch')

    def test_failing_feed_does_not_expose_raw_error_text(self):
        with ReviewStore(self.db) as store:
            out = run_readonly_cycle(FailureFeed(), store, self.log, source='mock', stop_path=self.stop)
        self.assertEqual(out['status'], 'read_or_validation_failed')
        self.assertEqual(out['error_type'], 'ConnectionError')
        self.assertNotIn('PRIVATE', self.log.read_text(encoding='utf-8'))

    def test_multicycle_100_only_local_and_summary(self):
        with ReviewStore(self.db) as store:
            out = run_readonly_cycles(self.mkclient(), store, self.log, source='mock',
                cycles=100, interval_seconds=0, record_reviewed=True,
                sleeper=lambda _: None, stop_path=self.stop)
        self.assertEqual(out['cycles_completed'], 100)
        self.assertEqual(out['status_counts'].get('marked_reviewed_locally'), 2)
        self.assertEqual(out['status_counts'].get('no_unreviewed_candidate'), 98)
        self.assertEqual(out['locally_reviewed_total'], 2)
        self.assertEqual(len(self.log.read_text().splitlines()), 100)

    def test_500_cycle_offline(self):
        with ReviewStore(self.db) as store:
            out = run_readonly_cycles(self.mkclient(), store, self.log, source='mock',
                cycles=500, interval_seconds=0, record_reviewed=True,
                sleeper=lambda _: None, stop_path=self.stop)
        self.assertEqual(out['cycles_completed'], 500)
        self.assertEqual(out['writes_attempted'], 0)

    def test_official_fast_polling_rejected(self):
        with ReviewStore(self.db) as store:
            with self.assertRaises(ValueError):
                run_readonly_cycles(self.mkclient(), store, self.log,
                    source='trending', cycles=2, interval_seconds=1, official=True)

    def test_bad_cycle_count_rejected(self):
        with ReviewStore(self.db) as store:
            with self.assertRaises(ValueError):
                run_readonly_cycles(self.mkclient(), store, self.log,
                    source='mock', cycles=501)

    def test_empty_feed_noop(self):
        class Empty:
            def get_feed(self):
                return []
        with ReviewStore(self.db) as store:
            out = run_readonly_cycle(Empty(), store, self.log, source='mock', stop_path=self.stop)
        self.assertEqual(out['status'], 'no_unreviewed_candidate')


if __name__ == '__main__':
    unittest.main()
