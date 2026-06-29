#!/usr/bin/env python3
"""Email the local aggregate SpawnWP telemetry report."""

import argparse
import json
import smtplib
import ssl
import subprocess
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = Path("/etc/spawnwp-telemetry-mail.json")
DEFAULT_REPORT_COMMAND = "/usr/local/sbin/spawnwp-telemetry-report"


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"host", "port", "security", "username", "password", "from", "to"}
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Missing mail configuration keys: {', '.join(missing)}")
    security = str(config["security"]).lower()
    if security not in {"starttls", "ssl"}:
        raise ValueError("Mail security must be 'starttls' or 'ssl'")
    config["security"] = security
    config["port"] = int(config["port"])
    return config


def render_report(command: str) -> str:
    result = subprocess.run(
        [command], check=True, capture_output=True, text=True, timeout=60
    )
    return result.stdout.strip()


def send_report(config: dict, report: str, now: datetime) -> None:
    message = EmailMessage()
    message["From"] = config["from"]
    message["To"] = config["to"]
    message["Subject"] = f"SpawnWP daily telemetry report — {now:%Y-%m-%d}"
    message.set_content(
        f"SpawnWP aggregate telemetry snapshot\n"
        f"Generated: {now.isoformat()}\n\n{report}\n"
    )

    context = ssl.create_default_context()
    if config["security"] == "ssl":
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30, context=context) as smtp:
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-command", default=DEFAULT_REPORT_COMMAND)
    args = parser.parse_args()

    now = datetime.now(ZoneInfo("Europe/Rome"))
    config = load_config(args.config)
    send_report(config, render_report(args.report_command), now)
    print(f"SpawnWP telemetry report sent for {now:%Y-%m-%d}")


if __name__ == "__main__":
    main()
