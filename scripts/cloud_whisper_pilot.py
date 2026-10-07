"""Bounded, artifact-only CPU Whisper pilot; never updates production feeds.

Run --list first, then select one eligible GUID with --guid. No source override,
paid ASR, YouTube extraction, credentials, or production transcript publishing.
"""

import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from transcribe_missing_podcasts import asr_eligibility, candidate_items, channel_policy_map, duration_seconds

ROOT = Path(__file__).resolve().parent.parent
MAX_AUDIO_BYTES = 128 * 1024 * 1024
MAX_WORK_BYTES = 2 * 1024 * 1024 * 1024
MAX_EPISODE_SECONDS = 7200
MODELS = ("small.en", "small")


class PilotError(RuntimeError):
    pass


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def public_url(url):
    """Validate every redirect too; never send cookies or credentials."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise PilotError("Only credential-free public HTTPS audio URLs are accepted")
    if parts.port not in (None, 443):
        raise PilotError("Only the standard HTTPS port is accepted")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise PilotError("Private or non-global audio destinations are rejected")
    return url


@contextmanager
def public_network():
    """Validate the resolver result used by the actual socket connection.

    A preflight lookup alone is insufficient: DNS could change before connect.
    The pilot has one isolated worker, so this worker-scoped hook also covers
    httpcore's connect-time lookup without relying on private transport APIs.
    """
    original = socket.getaddrinfo

    def checked(*args, **kwargs):
        addresses = original(*args, **kwargs)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise PilotError("Connection resolved to a private or non-global destination")
        return addresses

    socket.getaddrinfo = checked
    try:
        yield
    finally:
        socket.getaddrinfo = original


def worker_environment(work):
    # Do not inherit user API keys, proxy credentials, HF tokens or home caches.
    environment = {key: os.environ[key] for key in ("PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL")
                   if key in os.environ}
    environment.update(HOME=str(work / "home"), HF_HOME=str(work / "huggingface"),
                       HF_HUB_CACHE=str(work / "huggingface" / "hub"),
                       HF_XET_CACHE=str(work / "huggingface" / "xet"),
                       XDG_CACHE_HOME=str(work / "cache"), HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                       HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_XET="1", OMP_NUM_THREADS="4")
    return environment


def artifact_url(url):
    """Do not publish temporary signing tokens/query strings in artifacts."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def download_audio(url, destination, client, max_bytes=MAX_AUDIO_BYTES):
    """Download once, with bounded redirects/size and actual body validation."""
    started = time.monotonic()
    for _ in range(9):
        public_url(url)
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise PilotError("Audio redirect has no Location")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise PilotError(f"Audio HTTP status {response.status_code}; no bypass or retry")
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if not (mime.startswith("audio/") or mime == "application/octet-stream"):
                raise PilotError(f"Rejected non-audio Content-Type: {mime or 'missing'}")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise PilotError("Unexpected compressed HTTP body")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or not 1024 <= int(length) <= max_bytes):
                raise PilotError("Invalid or out-of-budget Content-Length")
            received = 0
            with Path(destination).open("wb") as stream:
                for chunk in response.iter_raw(chunk_size=64 * 1024):
                    received += len(chunk)
                    if received > max_bytes:
                        raise PilotError("Audio exceeds download byte budget")
                    stream.write(chunk)
            if received < 1024 or (length is not None and received != int(length)):
                raise PilotError("Empty, truncated, or mismatched audio body")
            with Path(destination).open("rb") as source:
                prefix = source.read(512).lstrip().lower()
            if prefix.startswith((b"<", b"{", b"[", b"#extm3u")):
                raise PilotError("Audio body looks like HTML, JSON, or a playlist")
            return {"final_url": artifact_url(url), "content_type": mime,
                    "download_bytes": received, "audio_sha256": digest(destination),
                    "download_seconds": round(time.monotonic() - started, 3)}
    raise PilotError("Too many audio redirects")


def probe_audio(path, expected_seconds=0):
    proc = subprocess.run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path),
    ], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(proc.stdout)
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        raise PilotError("Downloaded file has no decodable audio stream")
    seconds = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(seconds) or not 10 <= seconds <= MAX_EPISODE_SECONDS:
        raise PilotError("Decoded audio duration is missing or outside the 10s–2h budget")
    if expected_seconds and abs(seconds - expected_seconds) > max(60, expected_seconds * .05):
        raise PilotError("Audio duration does not match the selected feed episode")
    return seconds


def select_episode(feed, sources, guid):
    candidates, _ = candidate_items(feed, sources)  # No force_channels override.
    eligible = [item for _, item, _ in candidates if item.get("audio_url")]
    if not guid:
        return eligible
    matches = [item for item in eligible if item.get("guid") == guid]
    if len(matches) != 1:
        raise PilotError("GUID must identify exactly one currently eligible direct-audio episode")
    return matches[0]


def transcribe(audio, model_name, language, threads, model_dir):
    from faster_whisper import WhisperModel
    import importlib.metadata
    import ctranslate2

    supported = sorted(ctranslate2.get_supported_compute_types("cpu"))
    if "int8" not in supported:
        raise PilotError("This CPU does not support int8 inference")

    started = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads,
                         num_workers=1, download_root=str(model_dir), use_auth_token=False)
    loaded = time.monotonic()
    segments, info = model.transcribe(str(audio), language=None if language == "auto" else language,
                                     beam_size=5, vad_filter=True, condition_on_previous_text=False)
    # faster-whisper returns a lazy generator: include consumption in timing.
    rows = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
            for s in segments]
    finished = time.monotonic()
    text = "\n".join(row["text"] for row in rows if row["text"])
    if not text:
        raise PilotError("Whisper returned no speech; this is not a successful benchmark")
    return text, rows, {
        "cpu_count": os.cpu_count(), "supported_cpu_compute_types": supported,
        "platform": sys.platform,
        "model_load_and_download_seconds": round(loaded - started, 3),
        "inference_seconds": round(finished - loaded, 3),
        "decoded_sample_seconds": round(info.duration, 3),
        "speech_seconds_after_vad": round(info.duration_after_vad, 3),
        "real_time_factor": round((finished - loaded) / info.duration, 4),
        "detected_language": info.language, "language_probability": info.language_probability,
        "faster_whisper_version": importlib.metadata.version("faster-whisper"),
        "ctranslate2_version": importlib.metadata.version("ctranslate2"),
        "peak_process_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def worker(task_path):
    task = json.loads(Path(task_path).read_text())
    work = Path(task_path).parent
    output = Path(task["output"])
    result = {"request": task["request"], "status": "running", "stage": "download"}
    atomic_json(output / "result.json", result)
    # Bound individual files too; parent bounds aggregate work space and wall time.
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 ** 3, 1024 ** 3))
    audio = work / "source.audio"
    with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False,
                      trust_env=False, headers={"User-Agent": "ai-signal-whisper-pilot/1.0"}) as client:
        result.update(download_audio(task["audio_url"], audio, client))
    episode_seconds = probe_audio(audio, task["expected_seconds"])
    eligible, reason = asr_eligibility({"audio_url": task["audio_url"], "duration": str(math.floor(episode_seconds)),
                                        "audio_bytes": result["download_bytes"]}, task["policy"])
    if not eligible:
        raise PilotError(f"Measured audio fails existing source policy: {reason}")
    result["episode_seconds"] = round(episode_seconds, 3)
    request = task["request"]
    start = request["start_seconds"]
    if start >= episode_seconds - 10:
        raise PilotError("Sample starts too close to or beyond the end of the episode")
    sample = request["sample_seconds"] or episode_seconds - start
    sample = min(sample, episode_seconds - start)
    result["stage"] = "decode"
    atomic_json(output / "result.json", result)
    wav = work / "sample.wav"
    decode_start = time.monotonic()
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-i", str(audio), "-ss", str(start), "-t", str(sample), "-map", "0:a:0",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
    ], capture_output=True, timeout=120, check=True)
    result["audio_decode_seconds"] = round(time.monotonic() - decode_start, 3)
    actual_sample = probe_audio(wav)
    if abs(actual_sample - sample) > 2:
        raise PilotError("Decoded sample is unexpectedly truncated")
    result["stage"] = "model_and_inference"
    atomic_json(output / "result.json", result)
    text, segments, stats = transcribe(wav, request["model"], request["language"],
                                       request["threads"], work / "models")
    result.update(stats)
    # These are experimental artifacts, not a production transcript or accuracy score.
    (output / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    atomic_json(output / "segments.json", segments)
    result.update(status="complete", stage="complete", transcript_sha256=digest(output / "transcript.txt"),
                  segments_sha256=digest(output / "segments.json"), transcript_chars=len(text),
                  quality_review_required=True)
    atomic_json(output / "result.json", result)


def work_bytes(path):
    total = 0
    for file in Path(path).rglob("*"):
        try:
            if file.is_file() and not file.is_symlink():
                total += file.stat().st_size
        except FileNotFoundError:
            pass  # Model downloads use temporary files and atomic renames.
    return total


def supervise(command, work, timeout, max_bytes=MAX_WORK_BYTES, env=None):
    started = time.monotonic()
    process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env)
    try:
        while process.poll() is None:
            if time.monotonic() - started >= timeout:
                raise PilotError(f"Pilot exceeded its {timeout}s total wall-time budget")
            if work_bytes(work) > max_bytes:
                raise PilotError("Pilot exceeded its temporary storage budget")
            time.sleep(.25)
        if process.returncode:
            raise PilotError(f"Worker failed (exit {process.returncode}); inspect result stage/error")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return round(time.monotonic() - started, 3)


def cached_success(output, request):
    try:
        result = json.loads((output / "result.json").read_text())
        return (result.get("status") == "complete" and result.get("request") == request
                and bool((output / "transcript.txt").stat().st_size)
                and result["transcript_sha256"] == digest(output / "transcript.txt")
                and result["segments_sha256"] == digest(output / "segments.json"))
    except (OSError, ValueError, KeyError):
        return False


def run(args):
    feed_path = ROOT / "feeds/feed-podcasts.json"
    sources_path = ROOT / "config/sources.json"
    feed = json.loads(feed_path.read_text())
    sources = json.loads(sources_path.read_text())
    if args.list:
        for item in select_episode(feed, sources, None):
            print(f"{item['guid']}\t{item['channel']}\t{item.get('duration', '?')}\t{item['title']}")
        return 0
    if not args.guid:
        raise PilotError("Choose one --guid from --list; there is no automatic batch mode")
    if args.model.endswith(".en") and args.language != "en":
        raise PilotError("small.en requires --language en; use small for auto/multilingual")
    if args.sample_seconds == 0 and args.start_seconds != 0:
        raise PilotError("Full-episode runs require --start-seconds 0")
    item = select_episode(feed, sources, args.guid)
    request = {"schema": 1, "guid": item["guid"], "title": item["title"], "channel": item["channel"],
               "source_url": artifact_url(item["audio_url"]), "source_url_sha256": hashlib.sha256(item["audio_url"].encode()).hexdigest(),
               "feed_sha256": digest(feed_path), "sources_sha256": digest(sources_path),
               "model": args.model, "language": args.language, "threads": args.threads,
               "sample_seconds": args.sample_seconds, "start_seconds": args.start_seconds,
               "device": "cpu", "compute_type": "int8", "beam_size": 5}
    run_id = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:20]
    base = Path(args.output_dir).resolve()
    # This entrypoint has no production write path, even with a mistaken output flag.
    if base == ROOT or (ROOT in base.parents and not (base == ROOT / "pilot-output" or ROOT / "pilot-output" in base.parents)):
        raise PilotError("Inside the repository, output must stay under pilot-output/")
    output = base / run_id
    if output.is_symlink() or any((output / name).is_symlink() for name in (
        "result.json", "result.json.tmp", "transcript.txt", "segments.json", "segments.json.tmp", ".running"
    )):
        raise PilotError("Symlinked pilot output paths are rejected")
    output.mkdir(parents=True, exist_ok=True)
    if cached_success(output, request):
        print(f"Reused verified completed pilot: {output}")
        return 0
    lock = output / ".running"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PilotError("Pilot is already running or has a stale lock; inspect it before retrying") from exc
    os.close(descriptor)
    result = {"request": request, "status": "running", "stage": "starting"}
    try:
        atomic_json(output / "result.json", result)
        with tempfile.TemporaryDirectory(prefix="ai-signal-whisper-") as temporary:
            work = Path(temporary)
            task = {"request": request, "audio_url": item["audio_url"], "output": str(output),
                    "expected_seconds": duration_seconds(item.get("duration")),
                    "policy": channel_policy_map(sources).get(item.get("channel"), {})}
            atomic_json(work / "task.json", task)
            elapsed = supervise([sys.executable, str(Path(__file__).resolve()), "--_worker", str(work / "task.json")],
                                work, args.timeout_seconds, env=worker_environment(work))
            if not cached_success(output, request):
                raise PilotError("Worker exited without verified complete artifacts")
            result = json.loads((output / "result.json").read_text())
            result["total_worker_seconds"] = elapsed
            atomic_json(output / "result.json", result)
        print(f"Completed experimental pilot: {output}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        try:
            result = json.loads((output / "result.json").read_text())
        except (OSError, ValueError):
            pass
        result.update(status="failed", error=result.get("error") or str(exc) or "interrupted")
        try:
            atomic_json(output / "result.json", result)
        except OSError:
            print("Could not persist failure result (output I/O error)", file=sys.stderr)
        print(f"Pilot failed at {result.get('stage')}: {result['error']}; artifacts: {output}", file=sys.stderr)
        return 1
    finally:
        lock.unlink(missing_ok=True)


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        task_path = Path(sys.argv[2])
        try:
            with public_network():
                worker(task_path)
        except Exception as exc:
            task = json.loads(task_path.read_text())
            path = Path(task["output"]) / "result.json"
            result = json.loads(path.read_text())
            # HTTP/tool errors may contain signed URLs; save the type, not raw exception text.
            result.update(status="failed", error=str(exc) if isinstance(exc, PilotError) else type(exc).__name__)
            atomic_json(path, result)
            return 1
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List current eligible direct-audio episodes without network")
    parser.add_argument("--guid")
    parser.add_argument("--model", choices=MODELS, default="small.en")
    parser.add_argument("--language", choices=("en", "auto", "zh"), default="en")
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--sample-seconds", type=int, choices=(0, 60, 300), default=300,
                        help="Default 5min; 0 explicitly requests the full episode (max 2h)")
    parser.add_argument("--start-seconds", type=int, choices=range(0, 601), default=60)
    parser.add_argument("--timeout-seconds", type=int, choices=range(1, 1801), default=1800)
    parser.add_argument("--output-dir", default=str(ROOT / "pilot-output"))
    args = parser.parse_args()
    for executable in ("ffmpeg", "ffprobe"):
        if not args.list and not shutil.which(executable):
            parser.error(f"{executable} is required")
    try:
        return run(args)
    except PilotError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
