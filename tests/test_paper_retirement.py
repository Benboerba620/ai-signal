import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import generate_feed as generate
import macmini_health as health
from public_feed_watchdog import inspect

class PaperRetirementTests(unittest.TestCase):
    def test_disabled_fetch_never_calls_network(self):
        with patch.object(generate.httpx,'get',side_effect=AssertionError('network')):
            self.assertEqual(generate.fetch_arxiv({'arxiv':{'enabled':False}})['papers'],[])

    def test_disabled_missing_paper_has_no_health_requirement(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config').mkdir();(root/'feeds').mkdir()
            (root/'config/sources.json').write_text(json.dumps({'arxiv':{'enabled':False}}))
            at=datetime.now(timezone.utc)
            for name,key in [('podcasts','podcasts'),('blogs','articles'),('x','x')]:
                (root/f'feeds/feed-{name}.json').write_text(json.dumps({key:[],'generated_at':at.isoformat()}))
            self.assertEqual(inspect(root,at)[0],[])
            with patch.object(health,'accept',return_value={}),patch.object(health,'notify'):
                self.assertNotIn('arxiv',health.monitor(root,root,at=at))

if __name__=='__main__':unittest.main()
