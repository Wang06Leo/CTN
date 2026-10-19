"""Contract adapter tests without network, official package or real credentials."""
import unittest
from unittest.mock import Mock, patch

from ctn.models import AgentAction
from ctn.official_readonly import OfficialReadOnlySocialClient, OfficialSchemaMismatch, OfficialWriteBlocked, normalize_public_post
from ctn.selection import select_longest_ascii_question
from official_sdk_probe import offline_demo, get_limit_info


class FakeSocial:
    def __init__(self, posts):
        self.posts = posts
        self.rate_limit = None
        self.trending_calls = 0
        self.latest_calls = 0

    def feed_trending(self, limit=10):
        self.trending_calls += 1
        return self.posts[:limit]

    def feed_latest(self, limit=10):
        self.latest_calls += 1
        return self.posts[:limit]


class OfficialReadOnlyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSocial([
            {"post_id": 101, "author_id": 1, "body": "Where?"},
            {"post_id": 102, "author_id": 2, "body": "Could you explain exactly how the library schedule works?"},
            {"post_id": 103, "author_id": 3, "body": "为什么这个问题很长？" * 8},
        ])

    def test_offline_demo_no_network(self):
        self.assertEqual(offline_demo()["selected_post_id"], "b")

    def test_normalize_real_schema_fields(self):
        item = normalize_public_post(self.fake.posts[0])
        self.assertEqual((item.id, item.author, item.text), ("101", "1", "Where?"))

    def test_trending_one_call_and_candidate(self):
        client = OfficialReadOnlySocialClient(social=self.fake, limit=10)
        posts = client.get_feed()
        self.assertEqual(self.fake.trending_calls, 1)
        self.assertEqual(self.fake.latest_calls, 0)
        self.assertEqual(select_longest_ascii_question(posts).id, "102")

    def test_latest_one_call(self):
        client = OfficialReadOnlySocialClient(social=self.fake, feed="latest")
        self.assertEqual(len(client.get_feed()), 3)
        self.assertEqual(self.fake.latest_calls, 1)

    def test_empty_body_handled(self):
        self.assertEqual(normalize_public_post({"post_id": 1, "author_id": 2, "body": None}).text, "")

    def test_unknown_schema_fails_closed(self):
        for item in ({"id": 1, "author_id": 2, "text": "A?"}, {"post_id": 1, "author_id": 2, "body": []}):
            with self.assertRaises(OfficialSchemaMismatch):
                normalize_public_post(item)

    def test_execute_never_writes(self):
        client = OfficialReadOnlySocialClient(social=self.fake)
        with self.assertRaises(OfficialWriteBlocked):
            client.execute(AgentAction("reply", "101", "test"))
        self.assertEqual(self.fake.trending_calls, 0)

    def test_limit_guard(self):
        for value in (0, 51, True):
            with self.assertRaises(ValueError):
                OfficialReadOnlySocialClient(social=self.fake, limit=value)

    def test_rate_limit_report_optional(self):
        self.assertIsNone(get_limit_info(None))
        self.fake.rate_limit = Mock(limit=10, remaining=9, reset=58)
        client = OfficialReadOnlySocialClient(social=self.fake)
        self.assertEqual(get_limit_info(client.rate_limit), {"limit": 10, "remaining": 9, "reset": 58})


if __name__ == '__main__':
    unittest.main()


class FakePage(list):
    def __init__(self, posts, cursor=None):
        super().__init__(posts)
        self.next_cursor = cursor


class CursorSocial:
    def __init__(self, total=35):
        self.items = [
            {"post_id": i, "author_id": i + 100, "body": "Question? " + "x" * i}
            for i in range(total)
        ]
        self.calls = []
        self.rate_limit = None

    def feed_trending(self, *, limit=10, cursor=None):
        self.calls.append((limit, cursor))
        start = int(cursor) if cursor is not None else 0
        end = min(start + limit, len(self.items))
        next_cursor = str(end) if end < len(self.items) else None
        return FakePage(self.items[start:end], cursor=next_cursor)

    feed_latest = feed_trending


class PaginationRegressionTests(unittest.TestCase):
    def test_30_items_are_read_as_three_pages(self):
        fake = CursorSocial(total=35)
        client = OfficialReadOnlySocialClient(social=fake, limit=30)
        posts = client.get_feed()
        self.assertEqual(len(posts), 30)
        self.assertEqual([p.id for p in posts], [str(i) for i in range(30)])
        self.assertEqual(fake.calls, [(10, None), (10, '10'), (10, '20')])
        self.assertEqual(client.read_calls, 3)
        self.assertEqual(select_longest_ascii_question(posts).id, '29')

    def test_feed_exhaustion_stops_before_total(self):
        fake = CursorSocial(total=7)
        client = OfficialReadOnlySocialClient(social=fake, limit=30)
        self.assertEqual(len(client.get_feed()), 7)
        self.assertEqual(client.read_calls, 1)

    def test_one_page_does_not_need_cursor_kwarg(self):
        fake = CursorSocial(total=40)
        client = OfficialReadOnlySocialClient(social=fake, limit=9)
        self.assertEqual(len(client.get_feed()), 9)
        self.assertEqual(fake.calls, [(9, None)])

    def test_malformed_post_stops_closed(self):
        fake = CursorSocial(total=30)
        fake.items[14]["body"] = ["invalid"]
        client = OfficialReadOnlySocialClient(social=fake, limit=30)
        with self.assertRaises(OfficialSchemaMismatch):
            client.get_feed()
        self.assertEqual(client.read_calls, 2)

    def test_repeat_cursor_fails_closed(self):
        class BrokenCursor:
            def __init__(self):
                self.rate_limit = None
            def feed_trending(self, *, limit=10, cursor=None):
                return FakePage([{"post_id": 1, "author_id": 2, "body": "Hi?"}], cursor="same")
        client = OfficialReadOnlySocialClient(social=BrokenCursor(), limit=30)
        with self.assertRaises(OfficialSchemaMismatch):
            client.get_feed()
        self.assertEqual(client.read_calls, 2)

    def test_30_no_longer_raises_value_error(self):
        OfficialReadOnlySocialClient(social=CursorSocial(), limit=30)
