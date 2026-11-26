import sys
import unittest
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deep_read


class DeepReadTests(unittest.TestCase):
    def build(self, config=None, language="zh"):
        x = [{"name": "Sam Altman", "tweets": [{"text": "We are testing Private Inference" + "x" * 900,
                                                "url": "https://x.com/sama/status/1"}]}]
        eps = [{"channel": "a16z", "title": "Biotech", "link": "https://a16z.example/ep",
                "transcript_available": True, "transcript_path": "feeds/transcripts/abc.txt"},
               {"channel": "No Priors", "title": "Ep", "link": "https://np.example/ep"}]
        arts = [{"source_name": "Anthropic", "title": "Rust", "url": "https://www.anthropic.com/r"}]
        deep_read.attach_deep_read_urls(config or {}, language, x, eps, arts)
        return x, eps, arts

    def test_default_is_claude_prefill_with_transcript(self):
        _, eps, _ = self.build()
        url = eps[0]["deep_read_url"]
        self.assertTrue(url.startswith("https://claude.ai/new?q="))
        prompt = unquote(url.split("q=", 1)[1])
        self.assertIn("全文转录：https://cdn.jsdelivr.net/gh/Benboerba620/ai-signal@main/feeds/transcripts/abc.txt", prompt)
        self.assertIn("原文：https://a16z.example/ep", prompt)
        self.assertNotIn(" ", url.split("q=", 1)[1])

    def test_episode_without_transcript_only_links_page(self):
        _, eps, _ = self.build()
        self.assertNotIn("全文转录", unquote(eps[1]["deep_read_url"]))

    def test_tweet_text_is_carried_and_capped(self):
        x, _, _ = self.build()
        prompt = unquote(x[0]["tweets"][0]["deep_read_url"].split("q=", 1)[1])
        self.assertIn("We are testing Private Inference", prompt)
        self.assertLess(len(prompt), deep_read.MAX_TWEET_CHARS + 400)

    def test_chatgpt_and_english(self):
        _, _, arts = self.build({"deep_read": "chatgpt"}, language="en")
        url = arts[0]["deep_read_url"]
        self.assertTrue(url.startswith("https://chatgpt.com/?q="))
        self.assertIn("Deep-read", unquote(url))

    def test_off_adds_nothing(self):
        x, eps, arts = self.build({"deep_read": "off"})
        self.assertNotIn("deep_read_url", eps[0])
        self.assertNotIn("deep_read_url", arts[0])
        self.assertNotIn("deep_read_url", x[0]["tweets"][0])


if __name__ == "__main__":
    unittest.main()
