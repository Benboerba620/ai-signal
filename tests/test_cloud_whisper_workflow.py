"""Offline checks for migration triggers, fail-safe checkpoints and scope."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/daily-digest.yml'


class ProductionWorkflowTests(unittest.TestCase):
    def plan(self, **kwargs):
        text = WORKFLOW.read_text()
        section = text.split('      - name: Plan podcast processing', 1)[1].split('      - name: Install dependencies', 1)[0]
        shell = textwrap.dedent(section.split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            env = dict(os.environ, EVENT_NAME='workflow_dispatch', SCHEDULE='', PUSH_MESSAGE='',
                       TRANSCRIBE_ONLY='false', TRANSCRIBE_MISSING='config', SEMIANALYSIS='false',
                       GITHUB_OUTPUT=str(output), GITHUB_ENV=str(Path(temp)/'env'))
            env.update(kwargs)
            subprocess.run(['bash', '-euc', shell], cwd=ROOT, env=env, check=True)
            return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_daily_runs_whisper(self):
        self.assertEqual(self.plan(EVENT_NAME='schedule', SCHEDULE='13 20 * * *'),
                         dict(transcribe='true', capture='true', skip_generation='false'))

    def test_arxiv_refresh_keeps_existing_scope(self):
        self.assertEqual(self.plan(EVENT_NAME='schedule', SCHEDULE='30 1 * * 1-5'),
                         dict(transcribe='false', capture='false', skip_generation='false'))

    def test_local_inbox_import_does_not_run_asr(self):
        self.assertEqual(self.plan(EVENT_NAME='push'),
                         dict(transcribe='false', capture='true', skip_generation='false'))

    def test_explicit_migration_push_is_podcast_only(self):
        self.assertEqual(self.plan(EVENT_NAME='push', PUSH_MESSAGE='[run-whisper-production] Switch backend'),
                         dict(transcribe='true', capture='true', skip_generation='true'))

    def test_manual_opt_out_and_transcription_only(self):
        self.assertEqual(self.plan(TRANSCRIBE_MISSING='false')['transcribe'], 'false')
        self.assertEqual(self.plan(TRANSCRIBE_ONLY='true')['skip_generation'], 'true')
        self.assertEqual(self.plan(TRANSCRIBE_MISSING='false', SEMIANALYSIS='true')['transcribe'], 'true')

    def test_no_paid_asr_secret_or_entrypoint(self):
        source = WORKFLOW.read_text()
        self.assertNotIn('VOLC_ASR_API_KEY', source)
        self.assertNotIn('scripts/transcribe_missing_podcasts.py', source)
        self.assertNotIn('--force-channel', source)
        self.assertIn('for attempt in 1 2;', source)
        self.assertIn('--timeout-seconds "$timeout_seconds" --checkpoint-git', source)
        self.assertIn('65 * 60', source)
        self.assertIn('remaining', source)
        self.assertIn('group: ai-signal-feed-main', source)
        self.assertIn('timeout-minutes: 75', source)
        self.assertNotIn('pull_request_target', source)
        self.assertNotIn('self-hosted', source)

    def test_checkpoint_order_and_failure_reporting(self):
        source = WORKFLOW.read_text()
        names = ['Preserve pending podcasts before refresh', 'Generate feeds', 'Capture eligible refreshed podcasts',
                 'Validate feed uniqueness', 'Commit feeds', 'Process at most two cloud Whisper episodes',
                 'Preserve interrupted Whisper checkpoints', 'Keep cloud Whisper results and queue status',
                 'Report unsuccessful transcription attempts']
        positions = [source.index('      - name: ' + name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('bash scripts/checkpoint_whisper.sh --queue-only', source)
        self.assertIn("steps.whisper.outputs.failed == '1'", source)
        self.assertIn('retention-days: 7', source)

    def test_checkpoint_whitelist_excludes_unrelated_feeds(self):
        source = (ROOT / 'scripts/checkpoint_whisper.sh').read_text()
        self.assertIn('feeds/whisper-queue.json', source)
        self.assertIn('feeds/feed-podcasts.json', source)
        self.assertIn('feeds/feed-transcripts-index.json', source)
        self.assertIn('feeds/transcripts', source)
        self.assertNotIn('git add .', source)
        self.assertNotIn('git add -A', source)
        self.assertNotIn('feeds/feed-arxiv.json', source)
        self.assertNotIn('feeds/feed-blogs.json', source)
        self.assertNotIn('HEAD:main --force', source)
        self.assertIn('git ls-files -u', source)
        self.assertIn('rebase-merge', source)
        self.assertIn('git diff --cached --quiet', source)
        self.assertIn('python scripts/validate_feeds.py --scope podcasts', source)


if __name__ == '__main__':
    unittest.main()

class CheckpointIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'repo'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'init', '-b', 'main', str(self.repo)], check=True, capture_output=True)
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        (self.repo/'scripts').mkdir()
        (self.repo/'feeds/transcripts').mkdir(parents=True)
        (self.repo/'scripts/checkpoint_whisper.sh').write_text((ROOT/'scripts/checkpoint_whisper.sh').read_text())
        (self.repo/'scripts/validate_feeds.py').write_text('raise SystemExit(0)\n')
        for path in ('feed-podcasts.json', 'feed-transcripts-index.json', 'whisper-queue.json'):
            (self.repo/'feeds'/path).write_text('{}\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def checkpoint(self, *args):
        return subprocess.run(['bash', 'scripts/checkpoint_whisper.sh', *args], cwd=self.repo, capture_output=True, text=True)

    def test_empty_transcript_directory_can_checkpoint_queue(self):
        (self.repo/'feeds/whisper-queue.json').write_text('{"entries":{}}\n')
        result=self.checkpoint()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.git('rev-parse','HEAD'),self.git('rev-parse','origin/main'))

    def test_temp_and_unrelated_files_not_published(self):
        (self.repo/'feeds/transcripts/good.txt').write_text('transcript\n')
        (self.repo/'feeds/transcripts/.partial.txt.tmp').write_text('partial')
        (self.repo/'feeds/feed-x.json').write_text('unrelated')
        result=self.checkpoint()
        self.assertEqual(result.returncode,0,result.stderr)
        tracked=self.git('ls-tree','-r','--name-only','HEAD')
        self.assertIn('feeds/transcripts/good.txt',tracked)
        self.assertNotIn('.partial',tracked)
        self.assertNotIn('feeds/feed-x.json',tracked)

    def test_refuses_staged_unrelated_file(self):
        before=self.git('rev-parse','HEAD')
        (self.repo/'unrelated.txt').write_text('do not publish')
        self.git('add','unrelated.txt')
        self.assertNotEqual(self.checkpoint().returncode,0)
        self.assertEqual(before,self.git('rev-parse','HEAD'))

    def test_refuses_active_rebase(self):
        (self.repo/'.git/rebase-merge').mkdir()
        self.assertNotEqual(self.checkpoint().returncode,0)
