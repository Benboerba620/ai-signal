import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


class PodcastSourceConfigTests(unittest.TestCase):
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
