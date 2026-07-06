"""Mark a prepared AI Signal digest as delivered.

prepare_digest.py writes a delivery-mark.json file with the item IDs selected
for the digest. Run this script only after the digest has actually been shown or
sent successfully. That keeps retries from losing unseen items.

Usage:
    python scripts/mark_delivered.py --file ~/.ai-signal/payload/delivery-mark.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


USER_DIR = Path.home() / ".ai-signal"
SEEN_PATH = USER_DIR / "seen.json"
DEFAULT_MARK_PATH = USER_DIR / "payload" / "delivery-mark.json"
SEEN_RETENTION_DAYS = 14


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_seen():
    seen = {}
    if SEEN_PATH.exists():
        try:
            seen = json.loads(SEEN_PATH.read_text("utf-8"))
        except Exception:
            seen = {}
    for key in ("tweets", "episodes", "papers", "articles"):
        seen.setdefault(key, {})
    return seen


def save_seen(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    for key in ("tweets", "episodes", "papers", "articles"):
        seen[key] = {k: v for k, v in seen.get(key, {}).items() if v > cutoff}
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def load_mark(path):
    data = json.loads(path.read_text("utf-8"))
    ids = data.get("ids", {})
    for key in ("tweets", "episodes", "papers", "articles"):
        ids.setdefault(key, {})
    return ids


def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, default=str(DEFAULT_MARK_PATH),
                        help="Path to delivery-mark.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be marked without writing seen.json")
    args = parser.parse_args()

    mark_path = Path(args.file).expanduser()
    if not mark_path.exists():
        print(json.dumps({"status": "error", "error": f"Missing mark file: {mark_path}"}))
        sys.exit(1)

    ids = load_mark(mark_path)
    counts = {kind: len(values) for kind, values in ids.items()}
    if args.dry_run:
        print(json.dumps({"status": "ok", "dry_run": True, "counts": counts}, indent=2))
        return

    seen = load_seen()
    for kind, values in ids.items():
        seen.setdefault(kind, {}).update(values)
    save_seen(seen)

    print(json.dumps({"status": "ok", "marked": counts, "seen_path": str(SEEN_PATH)}, indent=2))


if __name__ == "__main__":
    main()
