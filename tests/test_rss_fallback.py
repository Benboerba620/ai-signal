import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import generate_feed as g

RSS = '''<rss><channel><item><title>Episode</title><guid>substack:post:42</guid>
<link>https://publisher.example/p/episode</link>
<enclosure url="https://audio.example/42.mp3" type="audio/mpeg"/>
</item></channel></rss>'''


class RSSFallbackTests(unittest.TestCase):
    def check_fallback(self, status, text):
        calls = []
        def fetch(url, **kwargs):
            calls.append(url)
            return httpx.Response(status if url == 'https://primary' else 200,
                                  text=text if url == 'https://primary' else RSS,
                                  request=httpx.Request('GET', url))
        with patch.object(g.httpx, 'get', side_effect=fetch), patch.object(g.time, 'sleep') as sleep:
            rss, url, error = g.fetch_rss_with_fallback(
                {'rss_url': 'https://primary', 'fallback_rss_urls': ['https://publisher']})
        self.assertIsNone(error)
        self.assertEqual(url, 'https://publisher')
        self.assertEqual(calls, ['https://primary', 'https://publisher'])
        sleep.assert_not_called()
        self.assertEqual(g.parse_rss(rss)[0]['guid'], 'substack:post:42')

    def test_403_switches_without_retrying_same_route(self):
        self.check_fallback(403, 'Forbidden')

    def test_http_200_html_is_not_mistaken_for_rss(self):
        self.check_fallback(200, '<html>Service unavailable</html>')

    def test_all_routes_fail_returns_error(self):
        with patch.object(g.httpx, 'get', side_effect=lambda url, **kwargs:
                          httpx.Response(403, request=httpx.Request('GET', url))):
            rss, url, error = g.fetch_rss_with_fallback(
                {'rss_url': 'https://primary', 'fallback_rss_urls': ['https://publisher']})
        self.assertIsNone(rss)
        self.assertIsNone(url)
        self.assertIn('https://primary', error)
        self.assertIn('https://publisher', error)

    def test_transient_503_retries_primary(self):
        responses = [httpx.Response(503, request=httpx.Request('GET', 'https://primary')),
                     httpx.Response(200, text=RSS, request=httpx.Request('GET', 'https://primary'))]
        with patch.object(g.httpx, 'get', side_effect=responses) as fetch, \
             patch.object(g.time, 'sleep'):
            rss, url, error = g.fetch_rss_with_fallback({'rss_url': 'https://primary'})
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(error)
        self.assertEqual(url, 'https://primary')
