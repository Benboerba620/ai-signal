import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import usage_ping


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class UsagePingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "ping.json"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, status=200, fail_first=False):
        def fake(url, **kwargs):
            self.calls.append((url, kwargs))
            if fail_first and len(self.calls) == 1:
                raise OSError("blocked")
            return Response(status)
        return fake

    def test_pings_once_per_local_day_without_any_payload(self):
        now = datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc)
        first = usage_ping.maybe_ping({}, self.state, get=self.get(), env={}, now=now)
        second = usage_ping.maybe_ping({}, self.state, get=self.get(), env={}, now=now)
        self.assertEqual((first, second), ("sent", "already_today"))
        self.assertEqual(len(self.calls), 1)
        url, kwargs = self.calls[0]
        self.assertEqual(url, usage_ping.PING_BASES[0] + "/feeds/ping.txt")
        self.assertNotIn("?", url)
        self.assertEqual(set(kwargs), {"timeout", "follow_redirects"})
        self.assertEqual(json.loads(self.state.read_text()), {"last_day": "2026-11-26"})

    def test_new_local_day_pings_again(self):
        usage_ping.maybe_ping({}, self.state, get=self.get(), env={},
                              now=datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc))
        # 17:00 UTC is already the next day in Shanghai.
        result = usage_ping.maybe_ping({}, self.state, get=self.get(), env={},
                                       now=datetime(2026, 11, 26, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(result, "sent")
        self.assertEqual(len(self.calls), 2)

    def test_falls_back_to_next_mirror(self):
        result = usage_ping.maybe_ping({}, self.state, get=self.get(fail_first=True), env={})
        self.assertEqual(result, "sent")
        self.assertEqual(self.calls[1][0], usage_ping.PING_BASES[1] + "/feeds/ping.txt")

    def test_failure_is_not_retried_the_same_day(self):
        first = usage_ping.maybe_ping({}, self.state, get=self.get(status=503), env={})
        second = usage_ping.maybe_ping({}, self.state, get=self.get(status=503), env={})
        self.assertEqual((first, second), ("failed", "already_today"))
        self.assertEqual(len(self.calls), len(usage_ping.PING_BASES))

    def test_opt_outs_send_nothing(self):
        for config, env in (({"anonymous_ping": False}, {}), ({}, {"AI_SIGNAL_NO_PING": "1"}),
                            ({}, {"CI": "true"}), ({}, {"GITHUB_ACTIONS": "true"})):
            self.assertEqual(usage_ping.maybe_ping(config, self.state, get=self.get(), env=env), "disabled")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())

    def test_unwritable_state_never_raises(self):
        blocked = Path(self.tmp.name) / "file"
        blocked.write_text("x")
        self.assertEqual(usage_ping.maybe_ping({}, blocked / "ping.json", get=self.get(), env={}), "failed")

    def test_ping_file_is_published(self):
        self.assertTrue((Path(__file__).resolve().parent.parent / "feeds" / "ping.txt").is_file())


if __name__ == "__main__":
    unittest.main()
