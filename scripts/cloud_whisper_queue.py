"""Durable production queue for public direct-audio podcasts, CPU Whisper only.

Enqueue current policy-eligible episodes before and after feed refresh. Process
one episode per invocation, checkpointing feeds/ after each invocation. The
workflow owns serialization and the hard maximum of two attempts per run.
No paid ASR, credentials, YouTube audio extraction, or source-policy overrides.
"""

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urljoin, urlsplit

import httpx

import cloud_whisper_pilot as pilot
import generate_feed
from podcast_transcripts import index_record, normalize_transcript_text, parse_datetime, transcript_id
from transcribe_missing_podcasts import (candidate_items, canonical_episode_title, channel_policy_map,
                                        duration_seconds, is_youtube_url)

ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = "feeds/whisper-queue.json"
FEED_PATH = "feeds/feed-podcasts.json"
INDEX_PATH = "feeds/feed-transcripts-index.json"
# Fixed production safety caps. CLI may lower timeout/RSS but cannot expand
# these caps. Per-channel eligibility and language come from sources.json.
MAX_RSS_MIB = 6144
MAX_ATTEMPTS = 3
RETRY_HOURS = 6
SNAPSHOT_FIELDS = ("guid", "channel", "title", "description", "link", "audio_url", "audio_bytes",
                   "duration", "pub_date", "domain", "language", "region")
TRANSCRIPT_FIELDS = ("transcript_path", "transcript_chars", "transcript_sha256", "transcript_source",
                     "transcript_url", "transcript_video_id", "transcript_model", "transcript_language")


class QueueError(RuntimeError):
    def __init__(self, message, kind="state_error", retryable=False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


def utcnow():
    return datetime.now(timezone.utc)


def load_json(path, default=None):
    if not Path(path).exists() and default is not None:
        return copy.deepcopy(default)
    # A corrupt state/feed/index must fail closed, never silently reset history.
    return json.loads(Path(path).read_text("utf-8"))


def identity(item):
    if not item.get("guid") or not item.get("channel"):
        return None
    value = json.dumps([str(item["channel"]), str(item["guid"])], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def load_state(root):
    state = load_json(root / QUEUE_PATH, {"schema_version": 1, "entries": {}})
    if state.get("schema_version") != 1 or not isinstance(state.get("entries"), dict):
        raise QueueError("Unrecognized queue schema; refusing to reset persistent state")
    for key, entry in state["entries"].items():
        if not isinstance(entry, dict) or identity(entry.get("episode", {})) != key:
            raise QueueError("Corrupt queue identity; refusing to overwrite persistent state")
    return state


def save_state(root, state, now):
    state["updated_at"] = now.isoformat()
    pilot.atomic_json(root / QUEUE_PATH, state)


def transcript_path(root, value):
    if not value:
        return None
    path = root / str(value)
    allowed = (root / "feeds/transcripts").resolve()
    if path.is_symlink() or allowed not in path.resolve().parents or path.suffix != ".txt":
        return None
    return path


def valid_transcript(root, item):
    """Verify actual content, not a stale transcript_available flag or index row."""
    try:
        if item.get("transcript"):
            text = str(item["transcript"])
        else:
            path = transcript_path(root, item.get("transcript_path"))
            if not path or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                return ""
            text = path.read_text("utf-8")
        if not text.strip() or "\x00" in text or "\ufffd" in text:
            return ""
        if item.get("transcript_sha256") and hashlib.sha256(text.encode()).hexdigest() != item["transcript_sha256"]:
            return ""
        if item.get("transcript_chars") and len(text) != int(item["transcript_chars"]):
            return ""
        return text
    except (OSError, ValueError, UnicodeError):
        return ""


def cache_records(root, feed, index):
    records = {}
    for item in [*index.get("transcripts", []), *feed.get("podcasts", [])]:
        key = identity(item)
        if key and valid_transcript(root, item):
            records[key] = item
    # Atomic orphan sidecars from interrupted/legacy publication are reusable
    # even when their index or feed metadata was never committed.
    for item in feed.get("podcasts", []):
        key = identity(item)
        if key and key not in records:
            recovered = {**item, "transcript_path": f"feeds/transcripts/{transcript_id(item)}.txt"}
            if valid_transcript(root, recovered):
                records[key] = recovered
    return records


def eligible(item, sources):
    # A stale/missing sidecar is not a success. The existing source-selection
    # policy remains authoritative; no force_channels or new source discovery.
    probe = {key: value for key, value in item.items()
             if key != "transcript" and not key.startswith("transcript_")}
    candidates, skipped = candidate_items({"podcasts": [probe]}, sources)
    if not candidates:
        return False, skipped[0][1], "source_policy"
    if not item.get("audio_url") or is_youtube_url(item["audio_url"]):
        return False, "direct public audio required; YouTube audio extraction is disabled", "source_policy"
    parts = urlsplit(item["audio_url"])
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False, "credential-free HTTPS audio is required", "source_policy"
    language = channel_policy_map(sources).get(item.get("channel"), {}).get("language") or "en"
    if language not in ("en", "zh"):
        return False, f"unsupported source language: {language}", "unsupported_language"
    seconds = duration_seconds(item.get("duration"))
    if seconds and not 10 <= seconds <= pilot.MAX_EPISODE_SECONDS:
        return False, "episode exceeds the 10s–2h decode budget", "resource_policy"
    return True, candidates[0][2], "eligible"


def model_policy(item, sources):
    language = channel_policy_map(sources).get(item.get("channel"), {}).get("language") or "en"
    if language not in ("en", "zh"):
        raise QueueError("Unsupported source language", "unsupported_language")
    return ("small", "zh") if language == "zh" else ("small.en", "en")


def record_failure(entry, now, error, kind, retryable):
    retryable = retryable and entry.get("attempts", 0) < MAX_ATTEMPTS
    policy_skip = kind in ("source_policy", "resource_policy", "unsupported_language")
    if policy_skip:
        entry["terminal_skip"] = True
        entry["skip_reason"] = error
    entry.update(status="skipped" if policy_skip else ("retry" if retryable else "failed"), updated_at=now.isoformat(),
                 last_error=error, failure_kind=kind,
                 next_retry_at=(now + timedelta(hours=RETRY_HOURS * 2 ** max(0, entry.get("attempts", 1) - 1))).isoformat()
                 if retryable else None)
    entry.setdefault("history", []).append({"at": now.isoformat(), "attempt": entry.get("attempts", 0),
                                            "status": entry["status"], "kind": kind, "error": error})


def publish(root, entry, text, metadata, now, preserve_bytes=False):
    """Sidecar -> index -> current feed -> completed tombstone, all atomic.

    Persist expected hash/path before publication, so an interrupted publish can
    be repaired without redoing inference. Never resurrect an aged-out episode,
    prune existing files, or rewrite unrelated transcript sidecars.
    """
    text = text if preserve_bytes else normalize_transcript_text(text)
    if not text.strip() or "\x00" in text or "\ufffd" in text:
        raise QueueError("Transcript content failed validation", "invalid_artifacts", True)
    episode = entry["episode"]
    path_text = metadata.get("transcript_path") or f"feeds/transcripts/{transcript_id(episode)}.txt"
    path = transcript_path(root, path_text)
    if path is None:
        raise QueueError("Unsafe transcript publication path")
    metadata = {**metadata, "transcript_path": path_text, "transcript_chars": len(text),
                "transcript_sha256": hashlib.sha256(text.encode()).hexdigest()}
    entry["publication"] = metadata
    entry["publication_text"] = text
    # Caller persists this intent before side effects; recovery handles a file
    # that was atomically committed before index/feed/state publication failed.
    state = load_state(root)
    state["entries"][identity(episode)] = entry
    save_state(root, state, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
        pilot.atomic_text(path, text)
    index = load_json(root / INDEX_PATH, {"transcripts": []})
    key = identity(episode)
    old = next((row for row in index["transcripts"] if identity(row) == key), {})
    row = index_record({**episode, **metadata}, parse_datetime(old.get("cached_at")) or now,
                       now, now + timedelta(days=14))
    row.update({field: metadata[field] for field in TRANSCRIPT_FIELDS if field in metadata})
    index["transcripts"] = [row, *[record for record in index["transcripts"] if identity(record) != key]]
    index.update(generated_at=now.isoformat(), retention_days=14)
    pilot.atomic_json(root / INDEX_PATH, index)
    feed = load_json(root / FEED_PATH)
    changed = False
    for item in feed.get("podcasts", []):
        if identity(item) == key:
            item.pop("transcript", None)
            item.update(metadata, transcript_available=True, transcript_error=None)
            changed = True
    if changed:
        pilot.atomic_json(root / FEED_PATH, feed)
    entry.update(status="completed", completed_at=now.isoformat(), updated_at=now.isoformat(),
                 next_retry_at=None, transcript_path=path_text, transcript_sha256=metadata["transcript_sha256"])
    entry.pop("last_error", None)
    entry.pop("failure_kind", None)
    entry.pop("publication", None)
    entry.pop("publication_text", None)


def refresh(root, state, sources, now):
    feed = load_json(root / FEED_PATH)
    index = load_json(root / INDEX_PATH, {"transcripts": []})
    cache = cache_records(root, feed, index)
    report = {"enqueued": [], "skipped": [], "recovered": [], "warnings": []}
    # Record all real successes as durable source+GUID tombstones, even if their
    # channel is not eligible for Whisper or the index later ages out.
    current = {identity(item): item for item in feed.get("podcasts", []) if identity(item)}
    indexed = {identity(item) for item in index.get("transcripts", []) if identity(item)}
    for key, item in cache.items():
        entry = state["entries"].setdefault(key, {"episode": {field: item[field] for field in SNAPSHOT_FIELDS if field in item},
                                                 "attempts": 0, "first_seen_at": now.isoformat()})
        if key in current and (not valid_transcript(root, current[key]) or not current[key].get("transcript_available")
                               or key not in indexed or item.get("transcript")):
            # Repair index/feed cache pointers without running ASR or rewriting
            # existing sidecar bytes. Orphan sidecars become visible again.
            save_state(root, state, now)
            metadata = {field: item[field] for field in TRANSCRIPT_FIELDS if field in item}
            metadata.setdefault("transcript_source", "recovered_sidecar")
            publish(root, entry, valid_transcript(root, item), metadata, now, preserve_bytes=True)
            report["recovered"].append(key)
        if entry.get("status") != "completed":
            entry.update(status="completed", completed_at=now.isoformat(), updated_at=now.isoformat(),
                         transcript_path=item.get("transcript_path"), next_retry_at=None)
    for item in feed.get("podcasts", []):
        key = identity(item)
        if not key:
            report["skipped"].append({"title": item.get("title"), "reason": "missing source/GUID identity"})
            continue
        existing = state["entries"].get(key)
        if existing and existing.get("status") in ("completed", "failed"):
            continue
        ok, reason, kind = eligible(item, sources)
        # Do not bloat the queue with excluded channels. Report them separately.
        if not existing and not ok and kind == "source_policy":
            report["skipped"].append({"guid": item.get("guid"), "channel": item.get("channel"), "reason": reason})
            continue
        if not existing:
            existing = {"episode": {}, "status": "pending", "attempts": 0, "first_seen_at": now.isoformat(),
                        "updated_at": now.isoformat(), "next_retry_at": None}
            state["entries"][key] = existing
            report["enqueued"].append(key)
        existing["episode"] = {field: item[field] for field in SNAPSHOT_FIELDS if field in item}
    for key, entry in state["entries"].items():
        if entry.get("publication"):
            text = valid_transcript(root, {**entry["publication"], "transcript": entry.get("publication_text", "")})
            if text:
                save_state(root, state, now)
                publish(root, entry, text, entry["publication"], now, preserve_bytes=True)
                report["recovered"].append(key)
        if entry.get("status") == "completed":
            if key not in cache and not valid_transcript(root, entry):
                report["warnings"].append({"id": key, "reason": "completed tombstone retained; cache missing or corrupt"})
            continue
        if entry.get("status") == "failed" or entry.get("terminal_skip"):
            continue  # Terminal denial/failure never automatically retries.
        if entry.get("status") == "running":
            record_failure(entry, now, "Previous attempt interrupted before checkpoint", "interrupted", True)
            report["recovered"].append(key)
        ok, reason, kind = eligible(entry["episode"], sources)
        if not ok:
            entry.update(status="skipped", skip_reason=reason, failure_kind=kind, updated_at=now.isoformat())
            report["skipped"].append({"id": key, "reason": reason, "kind": kind})
        elif entry.get("status") == "skipped":
            entry.update(status="pending", next_retry_at=None, updated_at=now.isoformat())
            entry.pop("skip_reason", None)
            entry.pop("failure_kind", None)
    save_state(root, state, now)
    return report


def due_entries(state, now):
    return sorted(((key, entry) for key, entry in state["entries"].items()
                   if entry.get("status") in ("pending", "retry")
                   and (not entry.get("next_retry_at") or
                        (parse_datetime(entry["next_retry_at"]) or datetime.max.replace(tzinfo=timezone.utc)) <= now)),
                  key=lambda pair: (pair[1].get("first_seen_at", ""),
                                    pair[1]["episode"].get("pub_date") or "", pair[0]))


def fetch_public(item, policy):
    """Only configured public captions, without audio extraction or fallback APIs."""
    url = policy.get("transcript_rss_url")
    if not url:
        return None
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        for _ in range(9):
            pilot.public_url(url)
            with client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code in (401, 403, 429, 451):
                    raise QueueError(f"Public transcript feed denied HTTP {response.status_code}", "source_denied")
                if response.status_code != 200:
                    raise QueueError(f"Public transcript feed HTTP {response.status_code}", "source_error",
                                     response.status_code >= 500)
                if "html" in response.headers.get("content-type", "").lower():
                    raise QueueError("Public transcript feed returned HTML; no bypass attempted", "source_denied")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise QueueError("Public transcript RSS exceeds budget", "source_error", True)
                break
        else:
            raise QueueError("Too many public transcript feed redirects", "source_error", True)
    if re.search(br"<(?:!doctype\s+html|html)(?:\s|>)", bytes(body[:1024]).lower()):
        raise QueueError("Public transcript feed returned HTML; no bypass attempted", "source_denied")
    episodes = generate_feed.parse_rss(bytes(body).decode("utf-8"))
    target = canonical_episode_title(item.get("title"))
    match = next((ep for ep in episodes if canonical_episode_title(ep.get("title")) == target), None)
    if not match:
        return None
    # This is the same configured public-caption route as the previous pipeline,
    # not a new YouTube/audio candidate route. Credentials/proxies are absent.
    result = generate_feed.get_youtube_transcript(match.get("link"), generate_feed.caption_langs_for(policy))
    if result.get("text"):
        return {**result, "url": match.get("link")}
    error = str(result.get("error") or "").lower()
    if any(word in error for word in ("blocked", "blocking", "forbidden", "403", "401", "429", "sign in", "captcha", "denied", "too many requests", "restricted")):
        raise QueueError("Configured public captions denied access; no bypass attempted", "source_denied")
    if error and not any(word in error for word in ("no transcripts", "disabled", "too short", "no youtube video id")):
        raise QueueError("Configured public captions unavailable", "source_error", True)
    return None


def classify_error(error, stage=""):
    if isinstance(error, QueueError):
        return error.kind, error.retryable, str(error)
    message = str(error) if isinstance(error, pilot.PilotError) else type(error).__name__
    if re.search(r"HTTP status (401|403|429|451)\b", message):
        return "source_denied", False, message
    if re.search(r"HTTP status 4\d\d\b", message):
        return "source_unavailable", False, message
    if any(word in message for word in ("RSS memory", "temporary storage")):
        return "resource_budget", False, message
    if any(word in message for word in ("Measured audio", "duration is missing", "Private", "private", "public HTTPS", "standard HTTPS")):
        return "source_policy", False, message
    if any(word in message for word in ("Content-Type", "Content-Length", "byte budget", "HTML, JSON", "no decodable", "compressed HTTP")):
        return "invalid_audio", False, message
    return ("model_error" if stage == "model_and_inference" else "worker_error"), True, message


def worker(task_path):
    task = load_json(task_path)
    output = Path(task["output"])
    result = {"request": task["request"], "status": "running", "stage": "public_transcript"}
    pilot.atomic_json(output / "result.json", result)
    caption_note = task.get("public_caption_unavailable")
    try:
        with pilot.public_network():
            public = None
            if not caption_note:
                try:
                    public = fetch_public(task["episode"], task["policy"])
                except Exception as exc:
                    kind, _, error = classify_error(exc, "public_transcript")
                    caption_note = {"status": "unavailable", "failure_kind": kind, "error": error,
                                    "fallback": "original_public_rss_audio"}
                    # Never retry/bypass a denied caption resource. The original
                    # publisher RSS enclosure is an independently authorized
                    # source, validated by the strict audio downloader below.

            if public:
                text = normalize_transcript_text(public["text"])
                pilot.atomic_text(output / "transcript.txt", text)
                pilot.atomic_json(output / "segments.json", [])
                result.update(status="complete", stage="complete", transcript_source=public["source"],
                              transcript_url=(f"https://www.youtube.com/watch?v={public['video_id']}"
                                              if re.fullmatch(r"[A-Za-z0-9_-]{11}", str(public.get("video_id") or ""))
                                              else pilot.artifact_url(public.get("url") or "")),
                              transcript_video_id=public.get("video_id"),
                              transcript_sha256=pilot.digest(output / "transcript.txt"),
                              segments_sha256=pilot.digest(output / "segments.json"))
                pilot.atomic_json(output / "result.json", result)
            else:
                if caption_note:
                    task["public_caption_unavailable"] = caption_note
                    pilot.atomic_json(task_path, task)
                pilot.worker(task_path)
            if caption_note:
                result = load_json(output / "result.json", result)
                result["public_caption_unavailable"] = caption_note
                pilot.atomic_json(output / "result.json", result)
        return 0
    except Exception as exc:
        result = load_json(output / "result.json", result)
        kind, retryable, error = classify_error(exc, result.get("stage"))
        result.update(status="failed", error=error, failure_kind=kind, retryable=retryable)
        if caption_note:
            result["public_caption_unavailable"] = caption_note
        pilot.atomic_json(output / "result.json", result)
        return 1


def run_episode(entry, sources, output, timeout, max_rss_mib):
    item = entry["episode"]
    model, language = model_policy(item, sources)
    request = {"schema": 1, "guid": item["guid"], "channel": item["channel"], "title": item.get("title"),
               "source_url": pilot.artifact_url(item["audio_url"]),
               "source_url_sha256": hashlib.sha256(item["audio_url"].encode()).hexdigest(),
               "model": model, "language": language, "threads": 4, "sample_seconds": 0, "start_seconds": 0,
               "device": "cpu", "compute_type": "int8", "beam_size": 5}
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ai-signal-production-whisper-") as temporary:
        work = Path(temporary)
        task = {"request": request, "episode": item, "audio_url": item["audio_url"], "output": str(output),
                "expected_seconds": duration_seconds(item.get("duration")),
                "policy": channel_policy_map(sources).get(item["channel"], {}),
                "public_caption_unavailable": entry.get("public_caption_unavailable")}
        pilot.atomic_json(work / "task.json", task)
        pilot.atomic_json(output / "result.json", {"request": request, "status": "running", "stage": "starting"})
        try:
            elapsed = pilot.supervise([sys.executable, str(Path(__file__).resolve()), "--_worker", str(work / "task.json")],
                                      work, timeout, env=pilot.worker_environment(work),
                                      max_rss_bytes=max_rss_mib * 1024 * 1024)
        except Exception as exc:
            result = load_json(output / "result.json", {})
            if result.get("public_caption_unavailable"):
                entry["public_caption_unavailable"] = result["public_caption_unavailable"]
            if result.get("status") == "failed":
                raise QueueError(result.get("error", "Worker failed"), result.get("failure_kind", "worker_error"),
                                 result.get("retryable", True)) from exc
            kind, retryable, error = classify_error(exc, result.get("stage"))
            result.update(status="failed", failure_kind=kind, retryable=retryable, error=error)
            pilot.atomic_json(output / "result.json", result)
            raise QueueError(error, kind, retryable) from exc
        if not pilot.cached_success(output, request):
            raise QueueError("Worker output failed content/hash verification", "invalid_artifacts", True)
        result = load_json(output / "result.json")
        if result.get("public_caption_unavailable"):
            entry["public_caption_unavailable"] = result["public_caption_unavailable"]
        result["total_worker_seconds"] = elapsed
        pilot.atomic_json(output / "result.json", result)
        metadata = {"transcript_source": result.get("transcript_source", "local_whisper"),
                    "transcript_url": result.get("transcript_url", pilot.artifact_url(item["audio_url"]))}
        if metadata["transcript_source"] == "local_whisper":
            metadata.update(transcript_model=model, transcript_language=language)
        elif result.get("transcript_video_id"):
            metadata["transcript_video_id"] = result["transcript_video_id"]
        return (output / "transcript.txt").read_text("utf-8"), metadata


def checkpoint_git(root):
    """Persist the claim remotely before work; never leave orphan Git writers."""
    process = subprocess.Popen(["bash", str(root / "scripts/checkpoint_whisper.sh")],
                               cwd=root, start_new_session=True)
    try:
        returncode = process.wait(timeout=180)
        if returncode:
            raise QueueError("Pre-attempt Git checkpoint failed; no external work started", "checkpoint_error", True)
    except (subprocess.SubprocessError, OSError) as exc:
        raise QueueError("Pre-attempt Git checkpoint failed; no external work started", "checkpoint_error", True) from exc
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def summary(state, now):
    counts = {}
    for entry in state["entries"].values():
        status = entry.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    details = [{"id": key, "guid": entry["episode"].get("guid"), "channel": entry["episode"].get("channel"),
                **{field: entry.get(field) for field in ("status", "attempts", "next_retry_at", "failure_kind", "last_error", "skip_reason")}}
               for key, entry in state["entries"].items() if entry.get("status") != "completed"]
    return {"counts": counts, "due": len(due_entries(state, now)), "total": len(state["entries"]), "items": details}


def run(args, root=ROOT, now=None):
    root = Path(root).resolve()
    now = now or utcnow()
    state = load_state(root)
    if args.status:
        print(json.dumps(summary(state, now), ensure_ascii=False, indent=2))
        return 0
    output = Path(args.output_dir).resolve()
    if output == root or (root in output.parents and not (root / "artifacts") in output.parents):
        raise QueueError("Queue artifacts must be outside the repository or under artifacts/")
    output.mkdir(parents=True, exist_ok=True)
    sources = load_json(root / "config/sources.json")
    report = {"started_at": now.isoformat(), "mode": "enqueue" if args.enqueue_only else "process-one"}
    report.update(refresh(root, state, sources, now))
    code = 0
    due = due_entries(state, now)
    if getattr(args, "only_channel", None):
        due = [(key, entry) for key, entry in due if entry["episode"].get("channel") in args.only_channel]
    if not args.enqueue_only and due:
        key, entry = due[0]
        entry.update(status="running", attempts=entry.get("attempts", 0) + 1, started_at=now.isoformat(),
                     updated_at=now.isoformat(), next_retry_at=None)
        save_state(root, state, now)  # Must succeed before download/inference.
        attempt_output = output / key / f"attempt-{entry['attempts']}"
        attempt_output.mkdir(parents=True, exist_ok=True)
        report.update(id=key, guid=entry["episode"]["guid"], channel=entry["episode"]["channel"],
                      attempt=entry["attempts"], artifacts=str(attempt_output))
        try:
            if getattr(args, "checkpoint_git", False):
                # Fixed repository-owned helper, no shell/user command injection.
                checkpoint_git(root)
            text, metadata = run_episode(entry, sources, attempt_output, args.timeout_seconds, args.max_rss_mib)
            publish(root, entry, text, metadata, now)
        except (Exception, KeyboardInterrupt) as exc:
            kind, retryable, error = classify_error(exc)
            record_failure(entry, now, error, kind, retryable)
            code = 0 if entry["status"] == "skipped" else 1
        # Write only after publication is fully consistent, or with an explicit
        # failed attempt and retry policy. Publication intent survives failures.
        save_state(root, state, now)
        report["outcome"] = {field: entry.get(field) for field in
                             ("status", "attempts", "last_error", "failure_kind", "next_retry_at", "transcript_path", "public_caption_unavailable")}
    else:
        report["outcome"] = {"status": "enqueued" if args.enqueue_only else "no_due_items"}
    report["queue"] = summary(state, now)
    pilot.atomic_json(output / "run-report.json", report)
    if report.get("id"):
        pilot.atomic_json(attempt_output / "queue-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        return worker(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enqueue-only", action="store_true")
    mode.add_argument("--process-one", action="store_true")
    mode.add_argument("--status", action="store_true", help="Read-only queue summary; no files or network touched")
    parser.add_argument("--only-channel", action="append", default=[],
                        help="Limit processing only; never override source eligibility")
    parser.add_argument("--checkpoint-git", action="store_true",
                        help="Run fixed scripts/checkpoint_whisper.sh before external work")
    parser.add_argument("--timeout-seconds", type=int, choices=range(1, 1801), default=1800)
    parser.add_argument("--max-rss-mib", type=int, choices=range(256, MAX_RSS_MIB + 1), default=MAX_RSS_MIB)
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts/whisper"))
    args = parser.parse_args()
    try:
        return run(args)
    except (Exception, KeyboardInterrupt) as exc:
        # Never leak URLs/signatures from arbitrary HTTP/OSError exception text.
        print(f"Queue stopped before a safe checkpoint: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
