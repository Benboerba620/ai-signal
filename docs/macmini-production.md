# Mac mini feed runner

The daily non-X feed runs on the Mac at 04:13 Asia/Shanghai; arXiv-only refresh runs on weekdays at 09:30. The existing 04:20 X writer and existing user delivery jobs remain separate. The destination stays the public repository's `feeds/` tree, not a new notification channel.

`scripts/macmini_feed.py` defaults to preview. It reuses the durable queue, original source policy, public-caption preference, per-episode checkpoints, terminal denials and retry backoff. It processes at most two episodes. Local audio stays capped at 160 MiB and two hours (the standalone CPU/cloud path retains 128 MiB), each worker at 30 minutes / 6 GiB, and the full run at a 65-minute processing budget. Historical failed entries are retained, not reset during migration.

The Mac adapter calls the already-installed shared `podcast_rss_transcribe.py` through its existing Python runtime and cached MLX medium model. Inference is offline, receives no API keys or HF token, and does not download models. Its user-site library path is explicitly passed because the worker isolates HOME. The existing loopback HTTP network transport is configured explicitly for public audio; every source/redirect still passes public URL checks, and HTTP denials remain terminal. Linux manual runs keep CPU small/small.en.

## Deployment order

1. Verify a preview from an isolated checkout, plus a real local inference run. Preview changes only that checkout and saves reports externally.
2. Merge the migration change: remove only `Generate Daily Feed`'s schedule and inbox/request push triggers. Keep `Tests`, the artifact-only CPU Whisper pilot, and `workflow_dispatch`. A manual production run requires confirming the Mac jobs are paused.
3. Wait for any already queued/running cloud production run to finish. Do not discard its queue/feed updates.
4. Use a dedicated clean main checkout outside the synchronized working repository. Start the local runner with `--publish`. It refuses publication while cloud auto-triggers remain or a cloud production run is active/queued.
5. After verifying the resulting remote commit, load the daily and weekday arXiv launchd jobs. `RunAtLoad` is false. X's existing job remains the only X writer. The old local inbox exporter may still submit inbox files; these no longer trigger cloud production and will be imported at the next local daily refresh.

Example (paths are machine-specific, with existing credentials and dependencies):

```sh
python scripts/macmini_runtime.py --publish --auto-repair --scope all \
  --state-dir /path/outside/checkout/runs \
  --transcriber /existing/workspace/scripts/podcast_rss_transcribe.py \
  --asr-python /usr/bin/python3 \
  --model-cache /existing/.cache/huggingface/hub \
  --audio-proxy http://127.0.0.1:12343
```

The runner locks both local scopes, preserves unexpected edits, validates feeds, commits only the non-X feed/queue/transcript paths, rebases on unrelated X/inbox updates, pushes without force and verifies remote ancestry. Preview never pushes. A failed fetch or interrupted write leaves files for inspection rather than resetting them. Inspect `latest.json`, each run's `report.json`, `generation.log`, and `whisper-*/` artifacts. A completed pipeline can still report partial source/transcript health; fresh feed timestamps do not prove every source succeeded.

## Rollback

Unload only the two new ai-signal launchd jobs first. Preserve the dedicated checkout, queue, reports and unpushed commits. The existing X/podcast-report/delivery jobs remain untouched. Once no local production run remains, the retained manual cloud workflow can run with `confirm_local_paused=true`. Restoring automatic cloud schedules is a separate reviewed change; never run both automatic producers together. No workflow history, queue or feed deletion is necessary.

## Bounded recovery

The recovery code does not modify itself, interpret logs as instructions, call an LLM, change credentials, increase quotas, or use paid ASR. Deploy source and launcher changes under the operator's repair authorization. Existing X and podcast delivery jobs keep their schedules.

- HTTP GETs: at most three attempts for transport errors or 408/429/500/502/503/504, exponential waits, at most 30 seconds sleeping per request. A longer `Retry-After` is deferred rather than shortened. The local generator persists host cooldowns in `http-deferred.json` across runs. Audio/caption throttling is persisted in the existing queue, at least the server deadline or existing 6h/12h backoff, with three lifetime attempts. Audio is not downloaded repeatedly within one attempt.
- Denied sources, malformed audio and resource limits are terminal. The local runtime recognizes only the prior false binary/octet-stream rejection and a known Content-Length rejection within the local 160 MiB cap. Eligible current entries receive one revision-specific retry within three lifetime attempts, retaining history. HTTP denials and unknown errors require inspection. The two-hour, 6 GiB and two-episode caps remain. SIGTERM/interruptions checkpoint the queue and stop work; subprocess timeouts kill the process group.
- `run.lock` uses an OS advisory lock, released automatically after process death. Replace the old outer `mkdir launch.lock` wrapper; do not automatically remove a directory lock while an old producer could still be running. Preflight failures now create reports. The whole producer has a 65-minute deadline; individual generation and ASR budgets remain bounded.
- Git transport failures retry at most three times. `publication.json` records the exact owned paths, hashes, base and pending commit. An interrupted checkpoint can resume only if those bytes/commit still match, no other edits exist, and the cloud writer is paused. No force push, reset, conflict clearing or arbitrary file deletion. A crash between committing/rebasing and saving its journal can require manual review. A crash during generation can leave unrecognized edits, which are intentionally preserved.
- Feed JSON writes are atomic. Acceptance checks the existing feed contracts plus actual timestamps, transcript character counts/content and available hashes. A stale retained feed is not accepted as a successful refresh. If its data contract is valid, other valid partial updates are checkpointed so the next scheduled attempt starts clean; the run still records a freshness failure. arXiv publication validates its own scope so unrelated stale podcast retention does not block it.
- Reports distinguish `pipeline_health`, `source_health`, current source failures and historical terminal entries. Coverage records total podcast items, complete transcripts and pending current transcripts. A healthy capture with a transcript backlog remains degraded until the backlog clears. A published valid refresh with a source failure is degraded at the source level; it does not count as a pipeline failure. After three consecutive pipeline failures **per scope**, the circuit latches open and starts no fetch/inference/publication. There is no timer-based circuit reset.

Inspect `runs/latest-all.json`, `runs/latest-arxiv.json`, `runs/health-*.json`, the per-run `diagnostic.json`, redacted Git `diagnostic.log`, and private `generation.log`/worker evidence. State directories are mode 0700, raw generation logs 0600. Do not attach raw logs to public issues: source URLs can contain signed parameters. Diagnostic records never contain command suggestions derived from external text. Unknown errors need a candidate patch and isolated tests reviewed by a human.

After repairing and inspecting the cause, explicitly reset one scope (this command performs no production run):

```sh
python scripts/macmini_feed.py --state-dir /existing/runs --scope arxiv --reset-circuit
```

### Existing notification channel and health observer

Pass `--notifier-module /existing/workspace/scripts/telegram_direct.py` to reuse the already-configured Telegram private recipient. No new destination, token or external service is provisioned. Messages include fixed failure categories, last accepted success, recovery action count and manual next steps; they contain no raw URLs, feed titles, logs or credentials. A local receipt reserves the incident fingerprint **before** sending. The same incident is suppressed, including uncertain delivery; confirmed recovery creates one recovery message. Inspect `alert-*.json` if delivery is uncertain rather than blindly resending.

An optional hourly launchd observer can run:

```sh
python scripts/macmini_runtime.py --state-dir /existing/runs --health-only --auto-repair \
  --notifier-module /existing/workspace/scripts/telegram_direct.py
```

It checks Beijing 04:13 daily (80-minute grace) and weekday 09:30 arXiv (25-minute grace), actual feed integrity/freshness, and the active bounded run. It does not start or restart the producer. Host sleep/offline means launchd cannot alert until the Mac is running; the existing machine-alive monitor remains responsible for host availability. Notifications cannot be guaranteed while Telegram or its credentials are unavailable; local reports remain the evidence.

Deployment: first back up the existing launcher and the two launchd plists, verify no active producer/old directory lock, then replace the launcher with an `exec` call to this runner (no outer mkdir lock or pre-run Git command). Keep the same repository, state path, cached engine and 04:13/09:30 schedules. Run an approved acceptance refresh; enable the hourly observer only after accepted state exists for both scopes (or after their first scheduled runs). Inspect remote commits and Telegram receipt. Do not send synthetic failure alerts to the real recipient during tests.

Rollback: boot out only the optional health observer, wait for producer idle, restore the backed-up launcher and approved prior code. Preserve all feed/queue commits, pending journal, circuit state and logs; do not reset the checkout or clear terminal failures. Keep the manual-only cloud workflow. Moving production back to GitHub remains a separate approved action.


## Opt-in automatic repair

Add `--auto-repair` to the existing producer and hourly health command. Keep
04:13 daily, 09:30 weekday arXiv and 04:20 X schedules. The observer can start
one missed/failed scope per invocation. The shared producer lock still applies.
Each scope gets at most one extra recovery attempt per 24 hours, reserved before
work. An open circuit closes only after a genuinely accepted success. Unknown
failures, real local edits, credentials, conflicts and corruption remain blocked.

Repairs back up exact originals outside the checkout. A lone unstaged queue edit
can be restored only when parsed content differs solely in the top-level
`updated_at`, and restored bytes match the original Git blob. No attempt, denial,
backoff or publication intent is reset. No-op queue saves preserve the timestamp.
Before checkpoints, inactive expired transcripts are backed up and pruned under
the existing 14-day rule. Verified active entries can renew that same window.
Feed freshness never changes during maintenance. Publication intent is recorded
before validation, so a validation failure retains a recoverable manifest.

Automatic mode separates arXiv, blogs and podcasts within the same 15-minute
generation budget. Valid partial updates can publish, but failed scopes stay
failed in the report. The configured loopback HTTP transport reaches RSS and
people searches as well as audio. No new proxy or credential is created.
ASR attempt, time and memory limits remain unchanged.

For private deployment before a public source release, `macmini_runtime.py`
loads six operator-installed modules pinned in an adjacent manifest. It checks
both original production source and patch hashes. Logical paths remain those of
the clean production checkout, so only data commits are public. Keep this patch
outside the checkout. Back up the launcher and plists first, then point the same
launcher to this entry. Review the patch if production source changes. Rollback
restores the old launcher; keep backups, journals, queue and reports.

The private health entry also reuses `com.yibo.x-feed` only after its last log
explicitly reports a proxy failure and the existing guard has fresh healthy
Google/Telegram checks. It uses `launchctl kickstart` without `-k`, leaves running
jobs alone and reserves at most one attempt per 24 hours. A later read of the
actual public X feed confirms recovery. Login failures do not qualify. Unknown X failures, missing jobs and failed publication checks use the same private deduplicated alerts; in-progress jobs remain untouched.

The checked-in `macmini_runtime.py` also runs directly from a Git checkout and routes isolated workers through that same entry. Private patch directories still require a valid manifest. The original pinned private patch remains available for rollback.

After a run or existing health check, the private entry checks changed public
feed aliases on jsDelivr. Consumers and verification use the default-branch CDN URLs rather than the stale `@main` aliases observed on October 18. Verified timestamp-plus-content versions make no further CDN requests. Stale
aliases use the official purge API, at most once per version per six hours, and
must return a sufficiently recent `generated_at` and the expected JSON content hash on a subsequent read. This detects transcript-only publication without falsifying the capture timestamp. Requests
share a 45-second budget with an eight-second request limit. Failed/uncertain
purges remain pending and use the same existing notification channel. A CDN purge
acknowledgement is never publication acceptance. Queue files and private logs
never enter this path.


## Transcript catch-up

The existing a16z AI title filter and The Acquirers Podcast now allow missing-transcript fallback. Other channels, filters and models remain unchanged. Generic binary Content-Types require recognized audio signatures and successful media probing before inference. HTML and other non-audio downloads remain rejected.

For an operator-requested backlog, add `--repair-transcripts --publish` to the normal runtime arguments. Each invocation shares the producer lock, processes at most two due episodes and checkpoints each outcome. It preserves podcast/arXiv/blog capture timestamps and does not claim a new capture success. A bounded one-off sequence may invoke it again after completion; it is not a recurring scheduler. Queue attempts, completed transcripts and terminal denials remain intact.


When origin is unavailable, the consumer resolves the latest public main commit once through the unauthenticated GitHub API and uses immutable commit URLs for the existing CDN endpoints. Feed and transcript requests share that revision. API failure retains the existing mirrored fallback; custom mirror URLs are preserved. The health observer can verify the identical pinned route against the published content hash when a branch alias remains stale. It records `delivery=commit_pinned` and retains `alias_status=pending`, rather than claiming the old alias was purged. The existing purge cooldown and 45-second shared budget still apply.


## Alerts

生产任务失败后立即尝试原私人 Telegram 通道。通知未确认时，在后续检查中至少间隔一小时发送新的状态提醒。持续故障每六小时提醒，验收恢复后提醒一次。重复提醒可能包含此前已送达但回执丢失的事件，正文会明确上次送达未确认。`notification-history.jsonl` 追加保存每次尝试和结果，消息回执不代表用户已读。

独立的 `Published Feed Watchdog` 每小时 UTC 第 7 分钟运行。它只读取主分支公开数据，不抓取、不转录、不修改数据。按北京时间主任务 04:13 加 80 分钟、X 04:20 加 60 分钟、工作日论文 09:30 加 25 分钟判断过期。没有新文章但当天完成抓取不算失败。检查来源错误、全文是否存在及内容校验值。

维护者配置仓库 Secrets `AI_SIGNAL_ALERT_BOT_TOKEN` 和 `AI_SIGNAL_ALERT_CHAT_ID`，指向原私人目的地。可通过手动运行的 `test_alert` 验证发送。状态通过 Actions cache 保留；缓存丢失可能导致额外提醒。每次检查的脱敏回执保留为 30 天 artifact，通知失败会让 workflow 失败。GitHub 定时调度可能延迟，不提供严格分钟级送达保证。主机和 GitHub 同时不可用、Telegram 本身故障仍可能影响提醒。

停用云端检查可禁用此 workflow；停用本机提醒不应停掉生产任务。


### Alert classification (2026-10-21)

正常排队只记录进度，首次入队满 24 小时才提醒。未进入转录范围的节目只显示覆盖缺口，不作故障处理。转录失败/重试、已完成全文丢失或损坏、抓取失败、来源错误及数据过期仍提醒。两端共用 transcript_health.py，小时检查读取当前状态，历史报告不改写。

正式 feeds 上传会立即触发独立检查；每小时 cron 仍保留。GitHub 定时事件实测存在数小时间隔，不能保证 06:07 或 10:07 按时执行。需要严格时效的部署应使用独立服务器定时器，不能将增加 cron 频率视为已解决平台延迟。


### Paper retirement (2026-10-22)

论文已停用。卸载 `com.yibo.ai-signal-arxiv` 的定时任务。主任务跳过论文，健康检查和镜像检查不再监控论文。旧 `feed-arxiv.json` 保留空列表及 `enabled=false/status=retired` 兼容标记，不再更新时间。日报仅显示播客、博客及 X。


### Unified X recovery and incident notifications (2026-10-26)

X 修复已并入正式源码。部署完成后 `run.sh` 应指向专用 repo 的 `scripts/macmini_runtime.py`，撤去旧临时补丁入口。通知仍使用原私人适配器，不更换 Bot 或目的地。

恢复程序先核对最近一次 X 日志。仅对连接失败，在出口通过现场检查后补跑原 `com.yibo.x-feed`。代理检查按小时更新时，不把超过三分钟的记录直接当作断网。每日最多补跑一次，运行中的任务不重复启动。补跑后必须读到补跑开始后的正式 feed 才记恢复；20 分钟无新发布则报告未恢复。

同次中断的检查分类变化不再触发新提醒。已发送、发送结果未知或中断时结果不明的提醒保持安静；明确本地发送失败可在一小时后重试。恢复只更新状态，不发送恢复提醒。重新发生故障可以再次首报。发送未知仍未被确认，不等于已送达；云端保留失败状态。两端仍独立观察，本机任务首报与云端停更兜底各自保留一次通知职责。

正常排队、论文停用和抓取时间保持原规则。GitHub 定时触发延迟仍是平台限制。
