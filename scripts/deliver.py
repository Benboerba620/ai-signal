"""Subscriber-side: deliver digest text via Telegram / Feishu / email.

Reads delivery config from ~/.ai-signal/config.json and API keys from
~/.ai-signal/.env

Usage:
    echo "digest text" | python scripts/deliver.py
    python scripts/deliver.py --message "digest text"
    python scripts/deliver.py --file /path/to/digest.md
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

USER_DIR = Path.home() / ".ai-signal"
CONFIG_PATH = USER_DIR / "config.json"
ENV_PATH = USER_DIR / ".env"

TELEGRAM_MAX_LEN = 4000


def log(msg):
    print(msg, file=sys.stderr)


def load_env():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def split_message(text, max_len=TELEGRAM_MAX_LEN):
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len * 0.3:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks


def send_telegram(text, bot_token, chat_id):
    for chunk in split_message(text):
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=30,
        )
        if not resp.is_success:
            err = resp.json()
            if "can't parse" in err.get("description", ""):
                httpx.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                           json={"chat_id": chat_id, "text": chunk,
                                 "disable_web_page_preview": True}, timeout=30)
            else:
                log(f"❌ Telegram: {err.get('description', resp.text)}")
                return False
        import time; time.sleep(0.3)
    return True


def send_feishu(text, webhook_url):
    resp = httpx.post(webhook_url, json={"msg_type": "text", "content": {"text": text}}, timeout=30)
    if resp.is_success:
        r = resp.json()
        if r.get("code") == 0 or r.get("StatusCode") == 0:
            return True
        log(f"❌ Feishu: {r}")
    return False


def send_email(text, api_key, to_email):
    from datetime import datetime
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": "Daily Digest <digest@resend.dev>", "to": [to_email],
              "subject": f"Daily Digest — {datetime.now().strftime('%Y-%m-%d')}",
              "text": text},
        timeout=30,
    )
    return resp.is_success


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", "-m", type=str)
    parser.add_argument("--file", "-f", type=str)
    args = parser.parse_args()

    if args.message:
        text = args.message
    elif args.file:
        text = Path(args.file).read_text("utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        log("No input. Use --message, --file, or pipe stdin.")
        sys.exit(1)

    if not text.strip():
        log("Empty digest, skipping.")
        return

    load_env()

    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text("utf-8"))

    delivery = config.get("delivery", {"method": "stdout"})
    method = delivery.get("method", "stdout")

    if method == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = delivery.get("chat_id", "")
        if not token or not chat_id:
            log("❌ Set TELEGRAM_BOT_TOKEN in ~/.ai-signal/.env and chat_id in config.json")
            sys.exit(1)
        ok = send_telegram(text, token, chat_id)
        log("✅ Sent to Telegram" if ok else "❌ Telegram failed")

    elif method == "feishu":
        webhook = delivery.get("webhook_url", os.environ.get("FEISHU_WEBHOOK_URL", ""))
        if not webhook:
            log("❌ Set webhook_url in config.json delivery section")
            sys.exit(1)
        ok = send_feishu(text, webhook)
        log("✅ Sent to Feishu" if ok else "❌ Feishu failed")

    elif method == "email":
        api_key = os.environ.get("RESEND_API_KEY", "")
        email = delivery.get("email", "")
        if not api_key or not email:
            log("❌ Set RESEND_API_KEY in .env and email in config.json")
            sys.exit(1)
        ok = send_email(text, api_key, email)
        log("✅ Sent to email" if ok else "❌ Email failed")

    else:
        print(text)


if __name__ == "__main__":
    main()
