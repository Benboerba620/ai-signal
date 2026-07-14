import unittest
from pathlib import Path

import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import prepare_digest
import render_digest


class DigestTimeContractTests(unittest.TestCase):
    def test_output_contract_requires_source_times(self):
        contract = prepare_digest.build_output_contract(
            {"language": "zh", "granularity": "summary", "timezone": "Asia/Shanghai"}
        )

        self.assertEqual(contract["time_display"]["timezone"], "Asia/Shanghai")
        self.assertEqual(contract["time_display"]["required_fields"]["x"], "created_at")
        self.assertEqual(contract["time_display"]["required_fields"]["podcasts"], "pub_date")
        self.assertEqual(contract["time_display"]["required_fields"]["papers"], "published")

    def test_formats_source_time_in_user_timezone(self):
        self.assertEqual(
            render_digest.format_source_time("2026-07-14T00:20:00+00:00", "Asia/Shanghai"),
            "2026-07-14 08:20（北京时间）",
        )
        self.assertEqual(render_digest.format_source_time("", "Asia/Shanghai"), "未验证")

    def test_fallback_renderer_shows_all_three_source_times(self):
        data = {
            "config": {"timezone": "Asia/Shanghai"},
            "x": [
                {
                    "name": "Example",
                    "handle": "example",
                    "tweets": [
                        {
                            "id": "x1",
                            "text": "AI model launch with a new inference API",
                            "url": "https://x.com/example/status/1",
                            "created_at": "2026-07-14T00:20:00+00:00",
                        }
                    ],
                }
            ],
            "podcasts": [
                {
                    "guid": "p1",
                    "channel": "AI Podcast",
                    "title": "AI infrastructure interview",
                    "description": "A discussion of AI models and infrastructure.",
                    "link": "https://example.com/p1",
                    "pub_date": "2026-07-13T23:00:00+00:00",
                }
            ],
            "papers": [
                {
                    "arxiv_id": "2607.12345v1",
                    "title": "An AI Agent Benchmark",
                    "abstract": "We evaluate AI agents on a new benchmark.",
                    "abs_url": "https://arxiv.org/abs/2607.12345v1",
                    "published": "2026-07-13T22:00:00+00:00",
                }
            ],
        }
        lines = []
        render_digest.render_tweets(data, lines)
        render_digest.render_podcasts(data, lines)
        render_digest.render_papers(data, lines)
        rendered = "\n".join(lines)

        self.assertIn("发布时间：2026-07-14 08:20（北京时间）", rendered)
        self.assertIn("发布时间：2026-07-14 07:00（北京时间）", rendered)
        self.assertIn("首次提交：2026-07-14 06:00（北京时间）", rendered)


if __name__ == "__main__":
    unittest.main()
