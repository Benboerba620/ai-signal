import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import generate_feed
import prepare_digest
import validate_feeds


class TweetAuthorTests(unittest.TestCase):
    def test_uses_tweet_user(self):
        tweet = SimpleNamespace(
            user=SimpleNamespace(username="OpenAI"),
            url="https://x.com/sama/status/123",
        )
        self.assertEqual(generate_feed.tweet_author_handle(tweet), "OpenAI")

    def test_falls_back_to_canonical_url(self):
        tweet = SimpleNamespace(
            user=None,
            url="https://x.com/OpenAI/status/2075274271845404744",
        )
        self.assertEqual(generate_feed.tweet_author_handle(tweet), "OpenAI")


class TwitterFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_reposts_returned_by_search_are_not_attributed_to_tracked_account(self):
        def tweet(tweet_id, author, text):
            return SimpleNamespace(
                id=tweet_id,
                user=SimpleNamespace(username=author),
                url=f"https://x.com/{author}/status/{tweet_id}",
                rawContent=text,
                date=datetime.now(timezone.utc),
                likeCount=20,
                retweetCount=2,
                replyCount=1,
            )

        search_results = {
            "from:sama": [
                tweet(1, "OpenAI", "AI product announcement"),
                tweet(2, "sama", "AI cost improvements"),
            ],
            "from:nvidia": [
                tweet(1, "OpenAI", "AI product announcement"),
                tweet(3, "nvidia", "AI infrastructure update"),
            ],
        }

        class FakePool:
            async def get_account(self, _):
                return object()

        class FakeAPI:
            def __init__(self, *_args, **_kwargs):
                self.pool = FakePool()

            def search(self, query, **_kwargs):
                return search_results[query]

        async def fake_gather(items):
            return items

        fake_twscrape = types.ModuleType("twscrape")
        fake_twscrape.API = FakeAPI
        fake_twscrape.gather = fake_gather
        sources = {
            "twitter": {
                "lookback_hours": 48,
                "max_tweets_per_user": 5,
                "accounts": [
                    {"handle": "sama", "name": "Sam Altman"},
                    {"handle": "nvidia", "name": "NVIDIA"},
                ],
            }
        }

        with mock.patch.dict(os.environ, {"TWITTER_COOKIES": "test"}), \
                mock.patch.dict(sys.modules, {"twscrape": fake_twscrape}), \
                mock.patch.object(generate_feed, "detect_proxy", return_value=""):
            result = await generate_feed.fetch_twitter(sources)

        self.assertEqual(
            [[item["id"] for item in account["tweets"]] for account in result["x"]],
            [["2"], ["3"]],
        )

class DigestDedupTests(unittest.TestCase):
    def test_dedupes_current_batch_across_all_content_types(self):
        duplicate_tweet = {"id": "tweet-1", "url": "https://x.com/OpenAI/status/1"}
        feed_x = {
            "x": [
                {"handle": "sama", "tweets": [duplicate_tweet]},
                {"handle": "nvidia", "tweets": [duplicate_tweet, {"id": "tweet-2"}]},
            ]
        }
        feed_podcasts = {
            "podcasts": [
                {"guid": "episode-1", "title": "Episode"},
                {"guid": "episode-1", "title": "Episode duplicate"},
            ]
        }
        papers = [{"arxiv_id": "2607.00001"}, {"arxiv_id": "2607.00001"}]
        articles = [{"id": "blog-1"}, {"id": "blog-1"}]
        seen = {"tweets": {}, "episodes": {}, "papers": {}, "articles": {}}

        accounts, episodes, fresh_papers, fresh_articles, marks = prepare_digest.filter_unseen(
            feed_x, feed_podcasts, papers, articles, seen
        )

        self.assertEqual([[tweet.get("id") for tweet in a["tweets"]] for a in accounts],
                         [["tweet-1"], ["tweet-2"]])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(len(fresh_papers), 1)
        self.assertEqual(len(fresh_articles), 1)
        self.assertEqual(set(marks["tweets"]), {"tweet-1", "tweet-2"})
        self.assertEqual(set(marks["episodes"]), {"episode-1"})


class FeedValidationTests(unittest.TestCase):
    def test_duplicate_values_ignores_empty_keys(self):
        self.assertEqual(validate_feeds.duplicate_values(["a", "", None, "a", "b"]), ["a"])


if __name__ == "__main__":
    unittest.main()
