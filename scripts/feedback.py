"""Local-only feedback storage and aggregation for AI Signal."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_FEEDBACK_PATH = Path.home() / ".ai-signal" / "feedback.jsonl"
VALID_ACTIONS = {"useful", "noise", "more", "less", "expanded"}


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def record_feedback(
    action,
    *,
    kind="",
    source="",
    item_id="",
    stable_id="",
    note="",
    path=DEFAULT_FEEDBACK_PATH,
    now=None,
):
    action = str(action or "").strip().lower()
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown feedback action: {action}")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    record = {
        "created_at": now.isoformat(),
        "action": action,
        "kind": str(kind or ""),
        "source": str(source or ""),
        "item_id": str(item_id or ""),
        "stable_id": str(stable_id or ""),
        "note": str(note or ""),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_feedback(path=DEFAULT_FEEDBACK_PATH, days=90, now=None):
    target = Path(path)
    if not target.is_file():
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=days)
    records = []
    for line in target.read_text("utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        created_at = parse_datetime(record.get("created_at"))
        if created_at and created_at >= cutoff and record.get("action") in VALID_ACTIONS:
            records.append(record)
    return records


def summarize_feedback(path=DEFAULT_FEEDBACK_PATH, days=90, now=None):
    records = load_feedback(path, days=days, now=now)
    action_counts = Counter(record.get("action") for record in records)
    source_counts = defaultdict(Counter)
    for record in records:
        kind = record.get("kind") or "unknown"
        source = record.get("source") or "unknown"
        source_counts[(kind, source)][record.get("action")] += 1

    sources = []
    for (kind, source), counts in source_counts.items():
        sources.append(
            {
                "kind": kind,
                "source": source,
                **{action: counts.get(action, 0) for action in sorted(VALID_ACTIONS)},
                "preference_score": (
                    counts.get("useful", 0)
                    + counts.get("more", 0) * 2
                    - counts.get("noise", 0)
                    - counts.get("less", 0) * 2
                ),
            }
        )
    sources.sort(key=lambda item: (-abs(item["preference_score"]), item["kind"], item["source"]))

    preferences = [
        {
            "created_at": record.get("created_at"),
            "action": record.get("action"),
            "kind": record.get("kind"),
            "source": record.get("source"),
            "note": record.get("note"),
        }
        for record in records
        if record.get("action") in {"more", "less"}
        or (record.get("action") in {"useful", "noise"} and record.get("note"))
    ][-20:]
    return {
        "storage": "local_only",
        "window_days": days,
        "total": len(records),
        "actions": {action: action_counts.get(action, 0) for action in sorted(VALID_ACTIONS)},
        "sources": sources,
        "recent_preferences": preferences,
    }


def main():
    parser = argparse.ArgumentParser(description="Record or summarize local AI Signal feedback")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--action", required=True, choices=sorted(VALID_ACTIONS))
    record_parser.add_argument("--kind", default="")
    record_parser.add_argument("--source", default="")
    record_parser.add_argument("--item-id", default="")
    record_parser.add_argument("--stable-id", default="")
    record_parser.add_argument("--note", default="")

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--days", type=int, default=90)

    args = parser.parse_args()
    if args.command == "record":
        result = record_feedback(
            args.action,
            kind=args.kind,
            source=args.source,
            item_id=args.item_id,
            stable_id=args.stable_id,
            note=args.note,
        )
    else:
        result = summarize_feedback(days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
