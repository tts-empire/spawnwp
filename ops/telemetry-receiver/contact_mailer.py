#!/usr/bin/env python3
"""Deliver queued SpawnWP contact-concierge messages by email, then remove them.

The internet-facing receiver only spools validated messages as JSON files; this
script (run by a systemd timer as root, so it can read the SMTP credentials)
picks them up and relays each one. Keeping the credentials out of the web
service limits what a compromise of the public endpoint could do.
"""
import argparse
import json
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from email_report import load_config, send_message

DEFAULT_SPOOL = Path("/var/lib/spawnwp-contact")
DEFAULT_CONFIG = Path("/etc/spawnwp-telemetry-mail.json")

INTENT_LABELS = {
    "support": "Question / support",
    "bug": "Bug",
    "business": "Business / private",
    "security": "Security",
}


def build_message(config: dict, record: dict) -> EmailMessage:
    intent = INTENT_LABELS.get(record.get("intent"), record.get("intent", "contact"))
    visitor = record.get("email", "")
    message = EmailMessage()
    message["From"] = config["from"]
    message["To"] = config["to"]
    if visitor:
        message["Reply-To"] = visitor
    message["Subject"] = f"[SpawnWP contact] {intent} — {visitor}"
    message.set_content(
        "New message from the spawnwp.com contact concierge.\n\n"
        f"Intent:   {intent}\n"
        f"Email:    {visitor}\n"
        f"Consent to use the email only for a reply: "
        f"{'yes' if record.get('consent') else 'no'}\n"
        f"Received: {record.get('received_at', '')}\n\n"
        "Message:\n"
        f"{record.get('message', '')}\n"
    )
    return message


def flush(spool: Path, config_path: Path) -> int:
    if not spool.exists():
        return 0
    pending = sorted(spool.glob("*.json"))
    if not pending:
        return 0
    config = load_config(config_path)
    sent = 0
    for path in pending:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        send_message(config, build_message(config, record))
        path.unlink(missing_ok=True)
        sent += 1
    return sent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, default=DEFAULT_SPOOL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    sent = flush(args.spool, args.config)
    if sent:
        print(f"Sent {sent} contact message(s) at {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
