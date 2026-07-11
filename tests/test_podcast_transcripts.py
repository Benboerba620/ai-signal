import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import fetch_transcript
from podcast_transcripts import externalize_transcripts, load_index, read_local_transcript


class PodcastTranscriptSidecarTests(unittest.TestCase):
    def test_externalizes_and_reloads_one_transcript(self):
        feed = {
            "podcasts": [
                {
                    "guid": "episode-1",
                    "title": "Example",
                    "link": "https://example.com/1",
                    "transcript": "A useful transcript.",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            externalize_transcripts(feed, root_dir=root)
            episode = feed["podcasts"][0]

            self.assertNotIn("transcript", episode)
            self.assertTrue(episode["transcript_available"])
            self.assertEqual(episode["transcript_chars"], len("A useful transcript.\n"))
            self.assertEqual(read_local_transcript(episode, root), "A useful transcript.\n")

    def test_on_demand_fetch_prefers_sidecar_path(self):
        episode = {
            "transcript_path": "feeds/transcripts/example.txt",
            "transcript_available": True,
        }

        original_remote = fetch_transcript.fetch_text_any
        original_local = fetch_transcript.load_local_text
        try:
            fetch_transcript.fetch_text_any = lambda path: "remote transcript" if path.endswith("example.txt") else None
            fetch_transcript.load_local_text = lambda path: None
            self.assertEqual(fetch_transcript.transcript_for_episode(episode), "remote transcript")
        finally:
            fetch_transcript.fetch_text_any = original_remote
            fetch_transcript.load_local_text = original_local

    def test_retains_sidecar_for_14_days_after_leaving_main_feed(self):
        captured_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        feed = {
            "podcasts": [
                {
                    "guid": "episode-1",
                    "title": "Example",
                    "transcript": "retained text",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            externalize_transcripts(feed, root, now=captured_at)
            sidecar = root / feed["podcasts"][0]["transcript_path"]

            externalize_transcripts(
                {"podcasts": []}, root, now=captured_at + timedelta(days=13)
            )
            self.assertTrue(sidecar.is_file())
            self.assertEqual(len(load_index(root)["transcripts"]), 1)

            externalize_transcripts(
                {"podcasts": []}, root, now=captured_at + timedelta(days=14)
            )
            self.assertFalse(sidecar.exists())
            self.assertEqual(load_index(root)["transcripts"], [])

    def test_active_episode_refreshes_its_expiry_window(self):
        captured_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        feed = {"podcasts": [{"guid": "episode-1", "transcript": "text"}]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            externalize_transcripts(feed, root, now=captured_at)
            externalize_transcripts(
                feed, root, now=captured_at + timedelta(days=13)
            )
            record = load_index(root)["transcripts"][0]

            self.assertEqual(
                record["expires_at"],
                (captured_at + timedelta(days=27)).isoformat(),
            )

    def test_index_entry_matches_episode_after_main_feed_expires(self):
        index_entry = {
            "guid": "episode-1",
            "title": "Old episode",
            "transcript_path": "feeds/transcripts/example.txt",
        }
        matched = fetch_transcript.match_episode([index_entry], guid="episode-1")
        self.assertEqual(matched["transcript_path"], index_entry["transcript_path"])

    def test_fetch_command_falls_back_to_retention_index(self):
        index_entry = {
            "guid": "expired-from-main-feed",
            "title": "Retained episode",
            "transcript_path": "feeds/transcripts/retained.txt",
            "expires_at": "2026-07-20T00:00:00+00:00",
        }
        sources = [
            ({"podcasts": []}, {"source": "remote", "generated_at": "now"}),
            ({"transcripts": [index_entry]}, {"source": "remote", "generated_at": "now"}),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "episode.txt"
            with mock.patch.object(fetch_transcript, "fetch_feed", side_effect=sources), \
                    mock.patch.object(fetch_transcript, "transcript_for_episode", return_value="retained text"), \
                    mock.patch.object(fetch_transcript, "record_feedback") as record_mock, \
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "fetch_transcript.py",
                            "--guid",
                            "expired-from-main-feed",
                            "--out",
                            str(output),
                        ],
                    ):
                self.assertEqual(fetch_transcript.main(), 0)
            self.assertEqual(output.read_text("utf-8"), "retained text")
            record_mock.assert_called_once_with(
                "expanded",
                kind="podcast",
                source="",
                stable_id="expired-from-main-feed",
                note="Retained episode",
            )


if __name__ == "__main__":
    unittest.main()
