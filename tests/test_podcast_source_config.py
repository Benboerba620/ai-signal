import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


class PodcastSourceConfigTests(unittest.TestCase):
    def test_substack_podcasts_use_audio_only_feeds(self):
        sources = json.loads((ROOT_DIR / "config" / "sources.json").read_text("utf-8"))
        channels = {
            channel["name"]: channel
            for channel in sources["podcasts"]["channels"]
        }

        self.assertEqual(
            channels["Latent Space"]["rss_url"],
            "https://api.substack.com/feed/podcast/1084089.rss",
        )
        self.assertEqual(
            channels["Lenny's Podcast"]["rss_url"],
            "https://api.substack.com/feed/podcast/10845.rss",
        )
        self.assertNotIn("fallback_rss_urls", channels["Latent Space"])
        self.assertNotIn("fallback_rss_urls", channels["Lenny's Podcast"])

    def test_y_combinator_uses_current_feed(self):
        sources = json.loads((ROOT_DIR / "config" / "sources.json").read_text("utf-8"))
        channels = sources["podcasts"]["channels"]
        yc_channels = [channel for channel in channels if "Combinator" in channel["name"]]

        self.assertEqual(len(yc_channels), 1)
        self.assertEqual(yc_channels[0]["name"], "Y Combinator Startup Podcast")
        self.assertEqual(
            yc_channels[0]["rss_url"],
            "https://anchor.fm/s/8c1524bc/podcast/rss",
        )
        self.assertNotIn(
            "https://anchor.fm/s/f58d3330/podcast/rss",
            {channel["rss_url"] for channel in channels},
        )


if __name__ == "__main__":
    unittest.main()
