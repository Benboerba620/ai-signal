#!/usr/bin/env bash
# Only production podcast state is writable by this checkpoint.
set -euo pipefail
cd "$(dirname "$0")/.."
# Never stage conflict markers, complete a failed rebase, or mix unrelated
# pre-staged changes into a podcast checkpoint.
if [ -n "$(git ls-files -u)" ] || \
   [ -d "$(git rev-parse --git-path rebase-merge)" ] || \
   [ -d "$(git rev-parse --git-path rebase-apply)" ]; then
  echo "Refusing checkpoint during an unresolved merge/rebase" >&2
  exit 1
fi
if ! git diff --cached --quiet; then
  echo "Refusing checkpoint with pre-staged changes" >&2
  exit 1
fi
if [ -f feeds/whisper-queue.json ]; then
  python -c 'import json; json.load(open("feeds/whisper-queue.json"))'
fi
if [ "${1:-}" != "--queue-only" ]; then
  python scripts/validate_feeds.py --scope podcasts
fi
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
if [ "${1:-}" = "--queue-only" ]; then
  [ -f feeds/whisper-queue.json ] || exit 0
  paths=(feeds/whisper-queue.json)
else
  paths=(feeds/feed-podcasts.json feeds/feed-transcripts-index.json)
  if [ -n "$(git ls-files -- ':(glob)feeds/transcripts/*.txt')" ] || compgen -G 'feeds/transcripts/*.txt' >/dev/null; then
    paths+=(':(glob)feeds/transcripts/*.txt')
  fi
  if [ -f feeds/whisper-queue.json ]; then paths+=(feeds/whisper-queue.json); fi
fi
git add -f -- "${paths[@]}"
if ! git diff --staged --quiet; then
  git commit -m "Checkpoint cloud Whisper podcast queue"
fi
# Same workflow concurrency serializes podcast writers. An unrelated feed-x
# update may still arrive from another authorized client; retain it via rebase.
if [ "${1:-}" = "--queue-only" ] && ! git diff --quiet; then
  # Publication recovery may intentionally leave a partial feed unstaged.
  # Save only the queue intent with a normal fast-forward push. Never rebase a
  # dirty partial publication or force-push; rejection remains an explicit error.
  timeout 60s git push origin HEAD:main
else
  timeout 60s git pull --rebase origin main
  timeout 60s git push origin HEAD:main
fi
