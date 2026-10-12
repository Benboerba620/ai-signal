# Mac mini feed runner

The daily non-X feed runs on the Mac at 04:13 Asia/Shanghai; arXiv-only refresh runs on weekdays at 09:30. The existing 04:20 X writer and existing user delivery jobs remain separate. The destination stays the public repository's `feeds/` tree, not a new notification channel.

`scripts/macmini_feed.py` defaults to preview. It reuses the durable queue, original source policy, public-caption preference, per-episode checkpoints, terminal denials and retry backoff. It processes at most two episodes. Audio stays capped at 128 MiB and two hours, each worker at 30 minutes / 6 GiB, and the full run at a 65-minute processing budget. Historical failed entries are retained, not reset during migration.

The Mac adapter calls the already-installed shared `podcast_rss_transcribe.py` through its existing Python runtime and cached MLX medium model. Inference is offline, receives no API keys or HF token, and does not download models. Its user-site library path is explicitly passed because the worker isolates HOME. The existing loopback HTTP network transport is configured explicitly for public audio; every source/redirect still passes public URL checks, and HTTP denials remain terminal. Linux manual runs keep CPU small/small.en.

## Deployment order

1. Verify a preview from an isolated checkout, plus a real local inference run. Preview changes only that checkout and saves reports externally.
2. Merge the migration change: remove only `Generate Daily Feed`'s schedule and inbox/request push triggers. Keep `Tests`, the artifact-only CPU Whisper pilot, and `workflow_dispatch`. A manual production run requires confirming the Mac jobs are paused.
3. Wait for any already queued/running cloud production run to finish. Do not discard its queue/feed updates.
4. Use a dedicated clean main checkout outside the synchronized working repository. Start the local runner with `--publish`. It refuses publication while cloud auto-triggers remain or a cloud production run is active/queued.
5. After verifying the resulting remote commit, load the daily and weekday arXiv launchd jobs. `RunAtLoad` is false. X's existing job remains the only X writer. The old local inbox exporter may still submit inbox files; these no longer trigger cloud production and will be imported at the next local daily refresh.

Example (paths are machine-specific, with existing credentials and dependencies):

```sh
python scripts/macmini_feed.py --publish --scope all \
  --state-dir /path/outside/checkout/runs \
  --transcriber /existing/workspace/scripts/podcast_rss_transcribe.py \
  --asr-python /usr/bin/python3 \
  --model-cache /existing/.cache/huggingface/hub \
  --audio-proxy http://127.0.0.1:12343
```

The runner locks both local scopes, preserves unexpected edits, validates feeds, commits only the non-X feed/queue/transcript paths, rebases on unrelated X/inbox updates, pushes without force and verifies remote ancestry. Preview never pushes. A failed fetch or interrupted write leaves files for inspection rather than resetting them. Inspect `latest.json`, each run's `report.json`, `generation.log`, and `whisper-*/` artifacts. A completed pipeline can still report partial source/transcript health; fresh feed timestamps do not prove every source succeeded.

## Rollback

Unload only the two new ai-signal launchd jobs first. Preserve the dedicated checkout, queue, reports and unpushed commits. The existing X/podcast-report/delivery jobs remain untouched. Once no local production run remains, the retained manual cloud workflow can run with `confirm_local_paused=true`. Restoring automatic cloud schedules is a separate reviewed change; never run both automatic producers together. No workflow history, queue or feed deletion is necessary.
