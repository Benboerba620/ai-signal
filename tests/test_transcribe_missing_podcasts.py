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

    def test_manual_override_enables_disabled_channel(self):
        item = {"audio_url": "https://example.com/episode.mp3"}
        policy = {"transcribe_missing": False}

        allowed, reason = transcribe_missing_podcasts.should_transcribe(
            item, policy, [], force=True
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "manual channel override")


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
        self.assertEqual(
            channel["transcript_rss_url"],
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCf_KhBXw5TIV0A7butjgFhg",
        )
        self.assertTrue(channel["transcribe_missing"])
        self.assertTrue(channel["require_direct_audio"])
        self.assertEqual(channel["min_transcription_duration_minutes"], 10)
        self.assertEqual(channel["min_transcription_audio_bytes"], 5000000)

    def test_rss_parser_preserves_enclosure_size(self):
        xml = """<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
        <channel><item><title>Full episode</title><guid>episode-1</guid>
        <enclosure url="https://example.com/full.mp3" length="48741563" type="audio/mpeg" />
        <itunes:duration>00:50:46</itunes:duration></item></channel></rss>"""

        episode = generate_feed.parse_rss(xml)[0]

        self.assertEqual(episode["audio_bytes"], 48741563)

    def test_rss_parser_ignores_image_enclosure(self):
        xml = """<rss><channel><item><title>Newsletter article</title>
        <guid>article-1</guid>
        <enclosure url="https://example.com/cover.png" length="12345" type="image/png" />
        </item></channel></rss>"""

        episode = generate_feed.parse_rss(xml)[0]

        self.assertEqual(episode["audio_url"], "")
        self.assertEqual(episode["audio_bytes"], 0)

    def test_spotify_catalog_page_is_not_accepted_as_transcript(self):
        url = "https://podcasters.spotify.com/pod/show/jordan-nanos/episodes/example"

        with mock.patch.object(generate_feed, "fetch_text_url") as fetch:
            result = generate_feed.transcript_from_episode_page(url)

        fetch.assert_not_called()
        self.assertIsNone(result["text"])
        self.assertIn("show notes, not transcripts", result["error"])

    def test_matches_audio_episode_to_official_youtube_transcript(self):
        client = mock.Mock()
        response = mock.Mock(text="rss")
        response.raise_for_status.return_value = None
        client.get.return_value = response
        item = {
            "title": "Ep. 021 - The AI Project Trinity | Jordan Nanos",
        }
        policy = {"transcript_rss_url": "https://example.com/youtube.xml"}
        youtube_episode = {
            "title": "Ep. 021 - The AI Project Trinity",
            "link": "https://www.youtube.com/watch?v=episode-21",
        }
        transcript = {
            "text": "full transcript",
            "source": "youtube_transcript_api",
            "video_id": "episode-21",
            "error": None,
        }

        with mock.patch.object(generate_feed, "parse_rss", return_value=[youtube_episode]), \
                mock.patch.object(
                    generate_feed,
                    "get_youtube_transcript",
                    return_value=transcript,
                ):
            result = transcribe_missing_podcasts.fetch_configured_public_transcript(
                client, item, policy
            )

        self.assertEqual(result["text"], "full transcript")
        self.assertEqual(result["url"], youtube_episode["link"])

    def test_force_and_only_channel_select_semianalysis(self):
        feed = {
            "podcasts": [
                {"channel": "SemiAnalysis", "title": "Episode", "audio_url": "audio"},
                {"channel": "Other", "title": "Other episode", "audio_url": "audio"},
            ]
        }
        sources = {
            "podcasts": {
                "channels": [
                    {"name": "SemiAnalysis", "transcribe_missing": False},
                    {"name": "Other", "transcribe_missing": True},
                ]
            }
        }

        candidates, _ = transcribe_missing_podcasts.candidate_items(
            feed,
            sources,
            force_channels=["SemiAnalysis"],
            only_channels=["SemiAnalysis"],
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1]["channel"], "SemiAnalysis")
        self.assertEqual(candidates[0][2], "manual channel override")


if __name__ == "__main__":
    unittest.main()
