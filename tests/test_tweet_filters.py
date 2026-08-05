"""Tests for the tweet relevance gates and quote-tweet handling.

Background (measured 2026-08-05): the keyword gate rejected 100% of
@jimkxa's and 67% of @GavinSBaker's original posts, because analysts write
in plain language while announcement accounts always name products. The
same posts also lost their substance when only ``rawContent`` was read, as
several analysts publish by quoting someone and adding one line.
"""

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
import generate_summaries
import render_digest


class RelevanceGateRoutingTests(unittest.TestCase):
    def test_judgment_tiers_skip_the_keyword_gate(self):
        cfg = {}
        self.assertFalse(generate_feed.uses_relevance_filter({"tier": "analyst"}, cfg))
        self.assertFalse(generate_feed.uses_relevance_filter({"tier": "exec"}, cfg))

    def test_announcement_tiers_keep_the_keyword_gate(self):
        cfg = {}
        self.assertTrue(generate_feed.uses_relevance_filter({"tier": "builder"}, cfg))
        self.assertTrue(generate_feed.uses_relevance_filter({"tier": ""}, cfg))

    def test_tier_matching_ignores_case_and_padding(self):
        self.assertFalse(generate_feed.uses_relevance_filter({"tier": " Analyst "}, {}))

    def test_config_can_redefine_judgment_tiers(self):
        cfg = {"judgment_tiers": ["builder"]}
        self.assertTrue(generate_feed.uses_relevance_filter({"tier": "analyst"}, cfg))
        self.assertFalse(generate_feed.uses_relevance_filter({"tier": "builder"}, cfg))

    def test_empty_judgment_tiers_gates_everyone(self):
        cfg = {"judgment_tiers": []}
        self.assertTrue(generate_feed.uses_relevance_filter({"tier": "analyst"}, cfg))

    def test_account_override_beats_tier(self):
        self.assertTrue(
            generate_feed.uses_relevance_filter({"tier": "analyst", "relevance_filter": True}, {})
        )
        self.assertFalse(
            generate_feed.uses_relevance_filter({"tier": "builder", "relevance_filter": False}, {})
        )


class ExcludeKeywordTests(unittest.TestCase):
    def test_social_noise_is_excluded_even_for_judgment_tiers(self):
        self.assertTrue(generate_feed.is_excluded_tweet("Happy birthday to my wife", {}))

    def test_substantive_post_is_not_excluded(self):
        self.assertFalse(
            generate_feed.is_excluded_tweet("Semis are cyclical, DRAM pricing is moving fast", {})
        )

    def test_empty_text_is_excluded(self):
        self.assertTrue(generate_feed.is_excluded_tweet("", {}))


class QuotedTweetTests(unittest.TestCase):
    def _tweet(self, text, quoted=None):
        return SimpleNamespace(rawContent=text, quotedTweet=quoted)

    def test_payload_carries_handle_text_and_url(self):
        quoted = SimpleNamespace(
            rawContent="Zero B200 availability across 13 providers.",
            user=SimpleNamespace(username="Suhail"),
            url="https://x.com/Suhail/status/999",
        )
        payload = generate_feed.quoted_tweet_payload(self._tweet("Yep", quoted))
        self.assertEqual(payload["handle"], "Suhail")
        self.assertEqual(payload["url"], "https://x.com/Suhail/status/999")
        self.assertIn("B200", payload["text"])

    def test_no_quote_returns_none(self):
        self.assertIsNone(generate_feed.quoted_tweet_payload(self._tweet("standalone post")))

    def test_empty_quote_text_returns_none(self):
        quoted = SimpleNamespace(rawContent="   ", user=None, url="")
        self.assertIsNone(generate_feed.quoted_tweet_payload(self._tweet("Yep", quoted)))

    def test_filter_text_includes_the_quoted_post(self):
        combined = generate_feed.tweet_filter_text(
            self._tweet("Underappreciated risk imo"), {"text": "GPU capacity is gone"}
        )
        self.assertIn("Underappreciated risk", combined)
        self.assertIn("GPU capacity", combined)

    def test_quoted_post_can_carry_an_otherwise_offtopic_comment(self):
        """A builder-tier account still passes when what it quotes is on topic."""
        self.assertFalse(generate_feed.is_relevant_tweet("Underappreciated risk imo", {}))
        combined = generate_feed.tweet_filter_text(
            self._tweet("Underappreciated risk imo"), {"text": "Inference costs are collapsing"}
        )
        self.assertTrue(generate_feed.is_relevant_tweet(combined, {}))


class FetchTwitterTierTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, accounts, search_results):
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
                "min_engagement": 0,
                "accounts": accounts,
            }
        }
        with mock.patch.dict(os.environ, {"TWITTER_COOKIES": "test"}), \
                mock.patch.dict(sys.modules, {"twscrape": fake_twscrape}), \
                mock.patch.object(generate_feed, "detect_proxy", return_value=""):
            return await generate_feed.fetch_twitter(sources)

    def _tweet(self, tweet_id, author, text, quoted=None):
        return SimpleNamespace(
            id=tweet_id,
            user=SimpleNamespace(username=author),
            url=f"https://x.com/{author}/status/{tweet_id}",
            rawContent=text,
            date=datetime.now(timezone.utc),
            likeCount=500,
            retweetCount=10,
            replyCount=5,
            quotedTweet=quoted,
        )

    async def test_analyst_keeps_plain_language_post_that_names_no_product(self):
        """The real @jimkxa DRAM post that the keyword gate used to drop."""
        text = "Semi's are cyclical. It's not if but when. I don't remember a price increase this fast on DRAM."
        result = await self._run(
            [
                {"handle": "jimkxa", "name": "Jim Keller", "tier": "analyst"},
                {"handle": "rauchg", "name": "Guillermo Rauch", "tier": "builder"},
            ],
            {
                "from:jimkxa": [self._tweet(1, "jimkxa", text)],
                "from:rauchg": [self._tweet(2, "rauchg", text)],
            },
        )
        by_handle = {a["handle"]: a["tweets"] for a in result["x"]}
        self.assertEqual(len(by_handle["jimkxa"]), 1, "analyst post must survive")
        self.assertEqual(by_handle["rauchg"], [], "builder tier still needs a keyword")

    async def test_judgment_tier_still_drops_social_noise(self):
        result = await self._run(
            [{"handle": "jimkxa", "name": "Jim Keller", "tier": "analyst"}],
            {"from:jimkxa": [self._tweet(1, "jimkxa", "Merry christmas everyone")]},
        )
        self.assertEqual(result["x"][0]["tweets"], [])

    async def test_quote_tweet_is_emitted_with_its_source(self):
        quoted = SimpleNamespace(
            rawContent="Zero B200 availability. Inference will get more expensive.",
            user=SimpleNamespace(username="Suhail"),
            url="https://x.com/Suhail/status/999",
        )
        result = await self._run(
            [{"handle": "GavinSBaker", "name": "Gavin Baker", "tier": "analyst"}],
            {"from:GavinSBaker": [self._tweet(1, "GavinSBaker", "Yep", quoted)]},
        )
        tweets = result["x"][0]["tweets"]
        self.assertEqual(len(tweets), 1)
        self.assertEqual(tweets[0]["text"], "Yep", "the account's own text stays verbatim")
        self.assertEqual(tweets[0]["quoted"]["handle"], "Suhail")
        self.assertIn("B200", tweets[0]["quoted"]["text"])

    async def test_plain_post_has_no_quoted_key(self):
        result = await self._run(
            [{"handle": "sama", "name": "Sam Altman", "tier": "exec"}],
            {"from:sama": [self._tweet(1, "sama", "New model is live")]},
        )
        self.assertNotIn("quoted", result["x"][0]["tweets"][0])


class DigestGateTests(unittest.TestCase):
    def test_judgment_account_short_comment_survives_when_it_quotes_substance(self):
        account = {"handle": "GavinSBaker", "tier": "analyst"}
        tweet = {
            "id": "1",
            "text": "Yep",
            "quoted": {"handle": "Suhail", "text": "Zero B200 availability across 13 providers."},
        }
        selected = render_digest.selected_tweets({"x": [dict(account, tweets=[tweet])]})
        self.assertEqual(len(selected), 1)

    def test_bare_short_comment_without_a_quote_is_still_noise(self):
        account = {"handle": "GavinSBaker", "tier": "analyst"}
        tweet = {"id": "1", "text": "Yep"}
        self.assertEqual(render_digest.selected_tweets({"x": [dict(account, tweets=[tweet])]}), [])

    def test_builder_account_still_needs_a_topic_keyword(self):
        account = {"handle": "rauchg", "tier": "builder"}
        tweet = {"id": "1", "text": "Spent the whole weekend rearranging my bookshelf again"}
        self.assertEqual(render_digest.selected_tweets({"x": [dict(account, tweets=[tweet])]}), [])

    def test_quoted_post_is_rendered_so_the_comment_is_readable(self):
        data = {
            "x": [
                {
                    "handle": "GavinSBaker",
                    "name": "Gavin Baker",
                    "tier": "analyst",
                    "tweets": [
                        {
                            "id": "1",
                            "text": "Yep",
                            "url": "https://x.com/GavinSBaker/status/1",
                            "created_at": "2026-08-04T14:16:29+00:00",
                            "quoted": {
                                "handle": "Suhail",
                                "text": "Zero B200 availability across 13 providers.",
                                "url": "https://x.com/Suhail/status/999",
                            },
                        }
                    ],
                }
            ]
        }
        lines = []
        render_digest.render_tweets(data, lines)
        body = "\n".join(lines)
        self.assertIn("引用（@Suhail）：", body)
        self.assertIn("B200", body)


class SummaryGateTests(unittest.TestCase):
    def test_quoted_text_reaches_the_prompt_inside_the_untrusted_block(self):
        item = {
            "text": "Yep",
            "quoted": {"handle": "Suhail", "text": "Zero B200 availability."},
        }
        prompt = generate_summaries.build_x_prompt(item, {"language": "zh"})
        self.assertIn("quoted post by @Suhail", prompt)
        body = prompt.split("<untrusted_source_data")[1].split("</untrusted_source_data>")[0]
        self.assertIn("B200", body, "quoted content must stay inside the untrusted block")

    def test_prompt_omits_the_block_when_there_is_no_quote(self):
        prompt = generate_summaries.build_x_prompt({"text": "New model is live"}, {"language": "zh"})
        self.assertNotIn("quoted post by", prompt)

    def test_judgment_account_reaches_the_summarizer(self):
        self.assertTrue(generate_summaries.is_judgment_account({"tier": "exec"}))
        self.assertFalse(generate_summaries.is_judgment_account({"tier": "builder"}))

    def test_gate_text_merges_the_quoted_post(self):
        merged = generate_summaries.tweet_gate_text(
            {"text": "Indeed", "quoted": {"text": "Capex guidance was raised again"}}
        )
        self.assertIn("Indeed", merged)
        self.assertIn("Capex", merged)


if __name__ == "__main__":
    unittest.main()
