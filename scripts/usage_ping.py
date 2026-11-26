"""Anonymous once-a-day usage count (opt-out).

Each install downloads one tiny static file from jsDelivr at most once per local
calendar day. Nothing is sent: no ID, no config, no query string, no cookies.
jsDelivr's public per-file statistics then show roughly how many installs ran a
digest that day. Like every feed download, jsDelivr sees the requesting IP.

Opt out with `"anonymous_ping": false` in ~/.ai-signal/config.json or the
environment variable AI_SIGNAL_NO_PING=1. CI runs never ping.

Maintainers read the counts with: python scripts/usage_ping.py --stats
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PING_FILE = "/feeds/ping.txt"
PING_BASES = (
    "https://cdn.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://fastly.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://gcore.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://testingcf.jsdelivr.net/gh/Benboerba620/ai-signal@main",
)
STATE_PATH = Path.home() / ".ai-signal" / "ping.json"
STATS_URL = ("https://data.jsdelivr.com/v1/stats/packages/gh/Benboerba620/ai-signal@main/files"
             "?period={period}&limit=100")
TIMEOUT_SECONDS = 3


def ping_enabled(config, env=None):
    env = os.environ if env is None else env
    if config.get("anonymous_ping", True) is False:
        return False
    if str(env.get("AI_SIGNAL_NO_PING", "")).strip().lower() in ("1", "true", "yes"):
        return False
    return not (env.get("CI") or env.get("GITHUB_ACTIONS"))


def local_today(config, now=None):
    try:
        zone = ZoneInfo(config.get("timezone") or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("Asia/Shanghai")
    return (now or datetime.now(zone)).astimezone(zone).date().isoformat()


def maybe_ping(config, state_path=STATE_PATH, get=None, env=None, now=None):
    """Return 'disabled', 'already_today', 'sent' or 'failed'. Never raises.

    The day is recorded before the request, so a failed ping is not retried
    the same day: at most one short request per install per day.
    """
    try:
        if not ping_enabled(config, env):
            return "disabled"
        today = local_today(config, now)
        state_path = Path(state_path)
        try:
            state = json.loads(state_path.read_text("utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("last_day") == today:
            return "already_today"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"last_day": today}), "utf-8")
        if get is None:
            import httpx
            get = httpx.get
        for base in PING_BASES:
            try:
                response = get(base + PING_FILE, timeout=TIMEOUT_SECONDS, follow_redirects=True)
                if response.status_code == 200:
                    return "sent"
            except Exception:
                continue
        return "failed"
    except Exception:
        return "failed"


def daily_counts(period="month", get=None):
    if get is None:
        import httpx
        get = httpx.get
    rows = get(STATS_URL.format(period=period), timeout=30).json()
    for row in rows:
        if row.get("name") == PING_FILE:
            return row["hits"].get("dates", {}), row["hits"].get("total", 0)
    return {}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", action="store_true", help="Print daily ping counts from jsDelivr")
    parser.add_argument("--period", default="month", choices=("week", "month", "quarter", "year"))
    args = parser.parse_args()
    if not args.stats:
        parser.print_help()
        return 0
    dates, total = daily_counts(args.period)
    for day, hits in sorted(dates.items()):
        print(f"{day}  {hits}")
    print(f"total  {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
