import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import generate_feed


class YouTubePublishedDateTests(unittest.TestCase):
    def test_parses_structured_page_date(self):
        html = (
            '<html><head><meta itemprop="datePublished" '
            'content="2025-08-06T07:14:26-07:00"></head></html>'
        )
        published = generate_feed.parse_youtube_published_date(html)
        self.assertEqual(published.isoformat(), "2025-08-06T14:14:26+00:00")

    def test_rejects_old_video_when_flat_search_has_no_date(self):
        video = {
            "id": "old-video",
            "title": "A Cheeky Pint with Anthropic CEO Dario Amodei",
            "channel": "Stripe",
            "channel_url": "",
            "upload_date": "",
            "duration": 3600,
            "description": "",
        }
        search = {"person": "Dario Amodei", "query": "Dario Amodei interview"}
        config = {
            "search_recency": "week",
            "max_results_per_search": 3,
            "min_duration_minutes": 15,
            "min_channel_subscribers": 0,
        }
        since = datetime.now(timezone.utc) - timedelta(days=7)

        with mock.patch.object(generate_feed, "run_ytdlp_search", return_value=[video]), \
                mock.patch.object(generate_feed, "fetch_video_meta", return_value=None), \
                mock.patch.object(
                    generate_feed,
                    "fetch_video_page_published_date",
                    return_value=datetime(2025, 8, 6, tzinfo=timezone.utc),
                ):
            result = generate_feed.search_person_appearances(search, config, since, set())

        self.assertEqual(result, [])

    def test_keeps_but_marks_video_when_date_is_still_unknown(self):
        video = {
            "id": "unknown-date",
            "title": "Dario Amodei interview on AI",
            "channel": "Example",
            "channel_url": "",
            "upload_date": "",
            "duration": 3600,
            "description": "",
        }
        search = {"person": "Dario Amodei", "query": "Dario Amodei interview"}
        config = {
            "search_recency": "week",
            "max_results_per_search": 3,
            "min_duration_minutes": 15,
            "min_channel_subscribers": 0,
        }

        with mock.patch.object(generate_feed, "run_ytdlp_search", return_value=[video]), \
                mock.patch.object(generate_feed, "fetch_video_meta", return_value=None), \
                mock.patch.object(generate_feed, "fetch_video_page_published_date", return_value=None):
            result = generate_feed.search_person_appearances(
                search, config, datetime.now(timezone.utc) - timedelta(days=7), set()
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0]["pub_date_source"], "unverified")
        self.assertIsNone(result[0][1])

    def test_rechecks_and_drops_carried_entry_with_old_page_date(self):
        now = datetime.now(timezone.utc)
        sources = {
            "podcasts": {
                "people": {
                    "lookback_hours": 168,
                    "searches": [
                        {"person": "Dario Amodei", "query": "Dario Amodei interview"}
                    ],
                }
            }
        }
        existing = {
            "podcasts": [
                {
                    "person": "Dario Amodei",
                    "title": "A Cheeky Pint with Anthropic CEO Dario Amodei",
                    "pub_date": "",
                    "first_seen": now.isoformat(),
                    "link": "https://www.youtube.com/watch?v=old-video",
                    "transcript_video_id": "old-video",
                }
            ]
        }

        with mock.patch.object(
                generate_feed,
                "fetch_video_page_published_date",
                return_value=datetime(2025, 8, 6, tzinfo=timezone.utc),
        ), mock.patch.object(generate_feed, "search_person_appearances", return_value=[]):
            episodes, errors = generate_feed.fetch_people(sources, existing, set())

        self.assertEqual(episodes, [])
        self.assertEqual(errors, [])


class ArxivFreshnessTests(unittest.TestCase):
    @staticmethod
    def atom_feed(arxiv_id, published):
        return f'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>https://arxiv.org/abs/{arxiv_id}</id>
            <updated>{published}</updated>
            <published>{published}</published>
            <title>Fresh paper</title>
            <summary>Fresh abstract</summary>
            <author><name>Example Author</name></author>
            <category term="cs.AI" />
            <arxiv:primary_category term="cs.AI" />
            <link title="pdf" href="https://arxiv.org/pdf/{arxiv_id}" />
          </entry>
        </feed>'''

    def test_merges_both_sort_orders_and_dedupes(self):
        published = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        xml = self.atom_feed("2607.12345v1", published)
        response = mock.Mock(text=xml)
        response.raise_for_status.return_value = None
        sources = {
            "arxiv": {
                "lookback_hours": 120,
                "max_papers": 30,
                "categories": [{"id": "cs.AI", "name": "AI"}],
            }
        }

        with mock.patch.object(generate_feed.httpx, "get", return_value=response) as get, \
                mock.patch.object(generate_feed.time, "sleep"):
            result = generate_feed.fetch_arxiv(sources)

        self.assertEqual(get.call_count, 2)
        self.assertEqual([paper["arxiv_id"] for paper in result["papers"]], ["2607.12345v1"])


if __name__ == "__main__":
    unittest.main()
