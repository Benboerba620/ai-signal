import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from signal_alerts import deliver
from public_feed_watchdog import inspect, expected_at


class AlertTests(unittest.TestCase):
    def test_uncertain_followup_reminder_recovery_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); at=datetime(2026,10,18,tzinfo=timezone.utc)
            sender=Mock(return_value={'status':'delivery_unknown'})
            deliver(root,'all','fail',True,'failed',sender,at)
            deliver(root,'all','fail',True,'failed',sender,at+timedelta(minutes=59))
            self.assertEqual(sender.call_count,1)
            sender.return_value={'status':'sent','message_id':12,'text_verified':True}
            deliver(root,'all','fail',True,'failed',sender,at+timedelta(hours=1))
            self.assertEqual(sender.call_count,2)
            self.assertIn('上次提醒未确认',sender.call_args.args[0])
            deliver(root,'all','fail',True,'failed',sender,at+timedelta(hours=6))
            self.assertEqual(sender.call_count,2)
            deliver(root,'all','fail',True,'failed',sender,at+timedelta(hours=7))
            self.assertEqual(sender.call_count,3)
            deliver(root,'all','recovered',False,'good',sender,at+timedelta(hours=8))
            deliver(root,'all','recovered',False,'good',sender,at+timedelta(hours=20))
            self.assertEqual(sender.call_count,4)
            events=[json.loads(l) for l in (root/'notification-history.jsonl').read_text().splitlines()]
            self.assertEqual(len(events),8)
            self.assertEqual(events[-1]['message_id'],12)

    def test_crashed_reservation_gets_bounded_followup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);at=datetime(2026,10,18,tzinfo=timezone.utc)
            (root/'alert-all.json').write_text(json.dumps({'key':'fail','at':at.isoformat(),'delivery':'reserved'}))
            sender=Mock(return_value={'status':'sent'})
            deliver(root,'all','fail',True,'failed',sender,at+timedelta(hours=1))
            self.assertEqual(sender.call_count,1)

    def test_deadline_and_weekend(self):
        at=datetime(2026,10,19,1,54,tzinfo=timezone.utc)
        self.assertEqual(expected_at(at,9,30,25,True).day,16)
        self.assertEqual(expected_at(at+timedelta(minutes=1),9,30,25,True).day,19)

    def test_fresh_empty_is_valid_but_stale_and_missing_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'feeds').mkdir()
            at=datetime(2026,10,18,0,tzinfo=timezone.utc)
            for name,field in [('podcasts','podcasts'),('blogs','articles'),('arxiv','papers'),('x','x')]:
                (root/f'feeds/feed-{name}.json').write_text(json.dumps({field:[], 'generated_at':at.isoformat()}))
            self.assertEqual(inspect(root,at)[0],[])
            path=root/'feeds/feed-podcasts.json'
            path.write_text(json.dumps({'podcasts':[{'transcript_available':False}], 'generated_at':(at-timedelta(days=3)).isoformat()}))
            problems,_=inspect(root,at)
            self.assertIn('podcasts:stale',problems)
            self.assertNotIn('podcasts:missing_fulltext',problems)
            self.assertEqual(inspect(root,at)[1]['coverage']['coverage_only'],1)
            (root/'feeds/feed-x.json').unlink()
            self.assertIn('x:unreadable',inspect(root,at)[0])


    def test_queue_progress_timeout_failure_and_unconfigured_coverage(self):
        from transcript_health import assess
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'feeds').mkdir()
            at=datetime(2026,10,21,tzinfo=timezone.utc)
            item={'channel':'A','guid':'one','transcript_available':False}
            (root/'feeds/feed-podcasts.json').write_text(json.dumps({'podcasts':[item]}))
            path=root/'feeds/whisper-queue.json'
            entry={'episode':item,'status':'pending','first_seen_at':(at-timedelta(hours=23)).isoformat()}
            path.write_text(json.dumps({'entries':{'id':entry}}))
            self.assertEqual(assess(root,at)[0],[])
            self.assertEqual(assess(root,at)[1]['pending_transcripts'],1)
            self.assertIn('transcript_overdue',assess(root,at+timedelta(hours=1))[0])
            # Updating the daily feed/queue timestamp must not postpone the deadline.
            entry['updated_at']=(at+timedelta(hours=2)).isoformat()
            path.write_text(json.dumps({'entries':{'id':entry}}))
            self.assertIn('transcript_overdue',assess(root,at+timedelta(hours=2))[0])
            entry['status']='failed'
            path.write_text(json.dumps({'entries':{'id':entry}}))
            self.assertIn('transcript_failed',assess(root,at)[0])
            path.write_text(json.dumps({'entries':{}}))
            self.assertEqual(assess(root,at)[0],[])
            self.assertEqual(assess(root,at)[1]['coverage_only'],1)

    def test_claimed_fulltext_corruption_still_alerts(self):
        from transcript_health import assess
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'feeds/transcripts').mkdir(parents=True)
            (root/'feeds/feed-podcasts.json').write_text(json.dumps({'podcasts':[
                {'channel':'A','guid':'one','transcript_available':True,'transcript_path':'feeds/transcripts/missing.txt'}]}))
            self.assertIn('transcript_corrupt',assess(root,datetime.now(timezone.utc))[0])

if __name__=='__main__': unittest.main()
