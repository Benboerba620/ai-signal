import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import generate_feed as gf


class EnterprisePodcastTests(unittest.TestCase):
    def test_executives_unique_and_company_sources_tagged(self):
        d=json.loads((gf.ROOT_DIR/'config/sources.json').read_text())['podcasts']
        people=[x['person'] for x in d['people']['searches']]
        for name in ['Marc Benioff','Amit Zavery','Philipp Herzig','Satya Nadella','Alex Karp']:
            self.assertEqual(people.count(name),1)
        for name in ['Salesforce','ServiceNow','SAP','Microsoft','Palantir']:
            ch=next(x for x in d['channels'] if x['name']==name)
            self.assertEqual(ch['speaker_type'],'company')
            self.assertIn('topic_keywords',ch)

    def test_filter_runs_before_transcript_fetch_and_keeps_attribution(self):
        ch={'name':'Example','speaker_type':'company','topic_keywords':['agentforce'],'rss_url':'https://example.com'}
        base=dict(pub_date=datetime.now(timezone.utc),description='',audio_url='',duration='',audio_bytes=0)
        episodes=[dict(base,title='Celebrity conversation',guid='no',link='https://example.com/no'),
                  dict(base,title='Agentforce customer deployment',guid='yes',link='https://example.com/yes')]
        fetched=dict(text='full transcript '*500,source='youtube',url=None,video_id='yes',error=None)
        with patch.object(gf,'fetch_rss_with_fallback',return_value=('<rss/>','https://example.com',None)),patch.object(gf,'parse_rss',return_value=episodes),patch.object(gf,'get_podcast_transcript',return_value=fetched) as fetch:
            result,error=gf.fetch_channel(ch,72,{})
        self.assertEqual(len(result),1);self.assertEqual(fetch.call_count,1)
        self.assertEqual(result[0]['speaker_type'],'company')

    def test_corporate_short_clips_rejected(self):
        ch={'name':'Example','min_transcript_chars':1800}
        ep=dict(pub_date=datetime.now(timezone.utc),title='AI',guid='1',link='https://example.com')
        with patch.object(gf,'fetch_rss_with_fallback',return_value=('<rss/>','https://example.com',None)),patch.object(gf,'parse_rss',return_value=[ep]),patch.object(gf,'get_podcast_transcript',return_value={'text':'promo '*100}):
            result,error=gf.fetch_channel(ch,72,{})
        self.assertEqual(result,[])

if __name__=='__main__':unittest.main()
