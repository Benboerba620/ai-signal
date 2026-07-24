import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import transcribe_missing_podcasts
import generate_feed


class DurationTests(unittest.TestCase):
    def test_parses_common_rss_duration_formats(self):
        parse = transcribe_missing_podcasts.duration_seconds

        self.assertEqual(parse("3046"), 3046)
        self.assertEqual(parse("50:46"), 3046)
        self.assertEqual(parse("00:50:46"), 3046)
        self.assertEqual(parse("unknown"), 0)

    def test_rejects_known_short_audio_before_transcription(self):
        item = {
            "audio_url": "https://example.com/short.mp3",
            "duration": "00:01:12",
        }
        policy = {
            "transcribe_missing": True,
            "min_transcription_duration_minutes": 10,
        }

        allowed, reason = transcribe_missing_podcasts.should_transcribe(
            item, policy, []
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "too short (72s < 10m)")

    def test_rejects_known_small_audio_when_duration_is_missing(self):
        item = {
            "audio_url": "https://example.com/short.mp3",
            "audio_bytes": 1157746,
        }
        policy = {
            "transcribe_missing": True,
            "min_transcription_audio_bytes": 5000000,
        }

        allowed, reason = transcribe_missing_podcasts.should_transcribe(
            item, policy, []
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "audio too small (1157746 bytes < 5000000)")

    def test_direct_audio_policy_rejects_stale_youtube_feed_item(self):
        item = {
            "link": "https://www.youtube.com/watch?v=0YOf6QTCNuY",
            "duration": "",
        }
        policy = {
            "transcribe_missing": True,
            "require_direct_audio": True,
        }

        allowed, reason = transcribe_missing_podcasts.should_transcribe(
            item, policy, []
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "missing direct audio_url")

    def test_accepts_full_episode(self):
        item = {
            "audio_url": "https://example.com/full.mp3",
            "duration": "00:50:46",
        }
        policy = {
            "transcribe_missing": True,
            "min_transcription_duration_minutes": 10,
        }

        allowed, reason = transcribe_missing_podcasts.should_transcribe(
            item, policy, []
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "channel default")


class SemiAnalysisConfigTests(unittest.TestCase):
    def test_uses_direct_audio_feed_with_transcription_guard(self):
        sources = json.loads((ROOT_DIR / "config" / "sources.json").read_text("utf-8"))
        channel = next(
            item
            for item in sources["podcasts"]["channels"]
            if item["name"] == "SemiAnalysis"
        )

        self.assertEqual(
            channel["rss_url"], "https://anchor.fm/s/10fbee758/podcast/rss"
        )
        self.assertTrue(channel["transcribe_missing"])
        self.assertTrue(channel["require_direct_audio"])
        self.assertEqual(channel["asr_audio_proxy"], "github_release")
        self.assertEqual(channel["min_transcription_duration_minutes"], 10)
        self.assertEqual(channel["min_transcription_audio_bytes"], 5000000)

    def test_rss_parser_preserves_enclosure_size(self):
        xml = """<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
        <channel><item><title>Full episode</title><guid>episode-1</guid>
        <enclosure url="https://example.com/full.mp3" length="48741563" type="audio/mpeg" />
        <itunes:duration>00:50:46</itunes:duration></item></channel></rss>"""

        episode = generate_feed.parse_rss(xml)[0]

        self.assertEqual(episode["audio_bytes"], 48741563)

    def test_spotify_catalog_page_is_not_accepted_as_transcript(self):
        url = "https://podcasters.spotify.com/pod/show/jordan-nanos/episodes/example"

        with mock.patch.object(generate_feed, "fetch_text_url") as fetch:
            result = generate_feed.transcript_from_episode_page(url)

        fetch.assert_not_called()
        self.assertIsNone(result["text"])
        self.assertIn("show notes, not transcripts", result["error"])


class AudioProxyFallbackTests(unittest.TestCase):
    def test_retries_download_failure_through_github_release(self):
        proxy = {"url": "https://github.com/example/repo/releases/download/cache/audio.mp3"}
        with mock.patch.object(
            transcribe_missing_podcasts,
            "submit_task",
            side_effect=["direct-request", "proxy-request"],
        ), mock.patch.object(
            transcribe_missing_podcasts,
            "query_task",
            side_effect=[RuntimeError("Invalid audio URI: audio download failed"), "transcript"],
        ), mock.patch.object(
            transcribe_missing_podcasts,
            "publish_github_audio_proxy",
            return_value=proxy,
        ) as publish, mock.patch.object(
            transcribe_missing_podcasts,
            "cleanup_github_audio_proxy",
        ) as cleanup:
            request_id, text = transcribe_missing_podcasts.transcribe_item(
                mock.Mock(),
                "api-key",
                {"guid": "episode-1", "audio_url": "https://example.com/audio.mp3"},
                {"asr_audio_proxy": "github_release"},
                1,
                10,
            )

        self.assertEqual(request_id, "proxy-request")
        self.assertEqual(text, "transcript")
        publish.assert_called_once()
        cleanup.assert_called_once_with(proxy)


if __name__ == "__main__":
    unittest.main()
