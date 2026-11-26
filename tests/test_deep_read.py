import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deep_read


class DeepReadTests(unittest.TestCase):
    def build(self, config=None, language="zh"):
        x = [{"name": "Sam Altman", "tweets": [{"id": "1", "text": "We are testing Private Inference",
                                                "url": "https://x.com/sama/status/1"}]}]
        eps = [{"channel": "a16z", "title": "Biotech", "link": "https://a16z.example/ep",
                "transcript_available": True, "transcript_path": "feeds/transcripts/abc.txt"},
               {"channel": "No Priors", "title": "Ep", "link": "https://np.example/ep"}]
        arts = [{"source_name": "Anthropic", "title": "Rust", "url": "https://www.anthropic.com/r"}]
        deep_read.attach_deep_read_urls(config or {}, language, x, eps, arts)
        return x, eps, arts

    def test_link_is_short_and_points_at_transcript(self):
        _, eps, _ = self.build()
        url = eps[0]["deep_read_url"]
        self.assertTrue(url.startswith(deep_read.PAGE + "#"))
        params = parse_qs(url.split("#", 1)[1])
        self.assertEqual(params["k"], ["p"])
        self.assertEqual(params["tr"], ["abc"])
        self.assertEqual(params["u"], ["https://a16z.example/ep"])
        self.assertNotIn("lang", params)
        self.assertLess(len(url), 200)

    def test_episode_without_transcript_has_no_tr(self):
        _, eps, _ = self.build()
        self.assertNotIn("tr=", eps[1]["deep_read_url"])

    def test_tweets_get_no_link(self):
        x, _, _ = self.build()
        self.assertNotIn("deep_read_url", x[0]["tweets"][0])

    def test_default_is_deepseek_without_via(self):
        _, eps, _ = self.build()
        self.assertNotIn("via=", eps[0]["deep_read_url"])
        self.assertEqual(deep_read.target({}), "deepseek")
        self.assertEqual(deep_read.target({"deep_read": "kimi"}), "kimi")

    def test_chatgpt_and_english(self):
        _, _, arts = self.build({"deep_read": "chatgpt"}, language="en")
        params = parse_qs(arts[0]["deep_read_url"].split("#", 1)[1])
        self.assertEqual((params["via"], params["lang"], params["k"]), (["chatgpt"], ["en"], ["b"]))

    def test_page_is_published(self):
        page = Path(__file__).resolve().parent.parent / "docs" / "r.html"
        text = page.read_text("utf-8")
        for site in ("chat.deepseek.com", "kimi.moonshot.cn/_prefill_chat", "claude.ai/new?q=", "chatgpt.com/?q="):
            self.assertIn(site, text)

    def test_off_adds_nothing(self):
        x, eps, arts = self.build({"deep_read": "off"})
        self.assertNotIn("deep_read_url", eps[0])
        self.assertNotIn("deep_read_url", arts[0])
        self.assertNotIn("deep_read_url", x[0]["tweets"][0])


if __name__ == "__main__":
    unittest.main()
