import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from feedback import record_feedback, summarize_feedback


class FeedbackTests(unittest.TestCase):
    def test_records_and_summarizes_local_preferences(self):
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.jsonl"
            record_feedback(
                "useful", kind="podcast", source="Dwarkesh Patel", path=path, now=now
            )
            record_feedback(
                "more",
                kind="podcast",
                source="Dwarkesh Patel",
                note="More technical interviews",
                path=path,
                now=now,
            )
            record_feedback(
                "noise", kind="x", source="example", path=path, now=now
            )
            record_feedback(
                "expanded",
                kind="podcast",
                source="Dwarkesh Patel",
                note="Episode title is not a preference",
                path=path,
                now=now,
            )

            summary = summarize_feedback(path, now=now)

        self.assertEqual(summary["storage"], "local_only")
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["actions"]["useful"], 1)
        dwarkesh = next(item for item in summary["sources"] if item["source"] == "Dwarkesh Patel")
        self.assertEqual(dwarkesh["preference_score"], 3)
        self.assertEqual(summary["recent_preferences"][0]["note"], "More technical interviews")
        self.assertEqual(len(summary["recent_preferences"]), 1)

    def test_excludes_feedback_outside_the_window(self):
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.jsonl"
            record_feedback(
                "useful",
                kind="paper",
                source="cs.AI",
                path=path,
                now=now - timedelta(days=91),
            )
            summary = summarize_feedback(path, days=90, now=now)

        self.assertEqual(summary["total"], 0)


if __name__ == "__main__":
    unittest.main()
