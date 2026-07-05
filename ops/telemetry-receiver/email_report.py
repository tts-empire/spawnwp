#!/usr/bin/env python3
"""Email the local aggregate SpawnWP telemetry report as branded HTML.

Runs alongside report.py (same directory). By default it sends a multipart
message (plaintext + HTML) via the configured SMTP relay. Use --dry-run to
write the HTML/plaintext to files instead of sending, for preview.
"""

import argparse
import json
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import report as report_module

DEFAULT_CONFIG = Path("/etc/spawnwp-telemetry-mail.json")

# ── Brand ────────────────────────────────────────────────────────────────────
APRICOT = "#f6b269"
INK = "#0d0d10"
OFFWHITE = "#f4f2ee"
SURFACE = "#ffffff"
PAGE = "#eceae6"
TEXT = "#1d2327"
MUTED = "#6b7280"
BORDER = "#e6e4df"
DOT = "#4a4a52"


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


# ── HTML building blocks ─────────────────────────────────────────────────────

def _logo_cell() -> str:
    """SpawnWP dot-grid mark as a tiny nested table (no external images)."""
    def cell(bg, ring=False):
        style = (f"width:9px;height:9px;border-radius:2px;"
                 f"background:{bg};"
                 + (f"box-shadow:inset 0 0 0 1px {APRICOT};" if ring else ""))
        return f'<td style="padding:2px"><div style="{style}"></div></td>'
    grid = [
        [cell(DOT), cell(DOT), cell("transparent", ring=True)],
        [cell(DOT), cell(APRICOT), cell(DOT)],
        [cell(DOT), cell(DOT), cell(DOT)],
    ]
    rows = "".join(f"<tr>{''.join(r)}</tr>" for r in grid)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse">{rows}</table>')


def _section(title: str, body: str) -> str:
    return (
        f'<tr><td style="padding:22px 28px 0">'
        f'<div style="font:600 12px/1 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'letter-spacing:.09em;text-transform:uppercase;color:{APRICOT};margin:0 0 10px">{escape(title)}</div>'
        f'{body}</td></tr>'
    )


def _rows_table(pairs) -> str:
    rows = ""
    for label, value in pairs:
        rows += (
            f'<tr>'
            f'<td style="padding:7px 0;border-bottom:1px solid {BORDER};'
            f'font:400 14px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{TEXT}">{escape(str(label))}</td>'
            f'<td align="right" style="padding:7px 0;border-bottom:1px solid {BORDER};'
            f'font:600 14px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{TEXT};'
            f'font-variant-numeric:tabular-nums">{escape(str(value))}</td>'
            f'</tr>'
        )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse">{rows}</table>')


def _kpi(value, label) -> str:
    return (
        f'<td align="center" style="padding:0 6px">'
        f'<div style="background:{OFFWHITE};border:1px solid {BORDER};border-radius:10px;padding:16px 10px">'
        f'<div style="font:700 26px/1 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'color:{INK};font-variant-numeric:tabular-nums">{escape(str(value))}</div>'
        f'<div style="font:600 11px/1.3 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'letter-spacing:.04em;text-transform:uppercase;color:{MUTED};margin-top:6px">{escape(label)}</div>'
        f'</div></td>'
    )


def render_html(report: dict, now: datetime) -> str:
    kpis = [(report["installations"], "Installations"),
            (report["environments_current"], "Live environments")]
    outcomes = (report.get("performance") or {}).get("outcomes") or {}
    if outcomes:
        kpis.append((outcomes["succeeded"] + outcomes["failed"], "Creates"))
        kpis.append((f'{outcomes["failure_rate"]:.1f}%', "Failure rate"))
    kpi_cells = "".join(_kpi(v, l) for v, l in kpis)

    sections = ""
    if report["versions"]:
        sections += _section("SpawnWP versions", _rows_table(report["versions"]))
    duo = ""
    if report["operating_systems"]:
        duo += _section("Operating systems", _rows_table(report["operating_systems"]))
    if report["architectures"]:
        duo += _section("Architectures", _rows_table(report["architectures"]))
    sections += duo
    if report["features"]:
        sections += _section("Enabled features", _rows_table(report["features"]))
    if report["feature_usage"]:
        sections += _section("Feature usage (fleet totals)",
                             _rows_table((i["label"], i["value"]) for i in report["feature_usage"]))
    creates = (report.get("performance") or {}).get("creates") or {}
    if creates:
        pairs = [(f"{mode} creates", f"{d['count']} · avg {d['avg_seconds']:.0f}s · worst {d['worst_seconds']}s")
                 for mode, d in creates.items()]
        if outcomes:
            pairs.append(("healthcheck timeouts", outcomes["healthcheck_timeouts"]))
        sections += _section("Create performance", _rows_table(pairs))
    fleet = report.get("hardware") or {}
    if fleet:
        pairs = [(label, ", ".join(f"{n}: {c}" for n, c in counts))
                 for label, counts in fleet["buckets"].items()]
        pairs.append(("Docker footprint",
                      f"avg {fleet['docker_images_gb']:.1f} GB images · {fleet['build_cache_gb']:.1f} GB cache"))
        pairs.append(("PHP versions / host",
                      ", ".join(f"{n}: {c}" for n, c in fleet["php_versions_per_host"])))
        sections += _section("Fleet hardware (rounded)", _rows_table(pairs))

    extended_note = ""
    if not report["feature_usage"] and not creates:
        extended_note = (
            f'<tr><td style="padding:20px 28px 0">'
            f'<div style="background:{OFFWHITE};border:1px solid {BORDER};border-radius:10px;'
            f'padding:14px 16px;font:400 13px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{MUTED}">'
            f'No installation is reporting extended usage metrics yet '
            f'(only consents under the newest telemetry notice send blueprint, WP-CLI and '
            f'performance counters). Those sections appear here once they do.</div></td></tr>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>SpawnWP telemetry</title></head>
<body style="margin:0;padding:0;background:{PAGE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE}">
<tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
 style="width:640px;max-width:100%;background:{SURFACE};border:1px solid {BORDER};border-radius:14px;overflow:hidden">
  <tr><td style="background:{INK};padding:22px 28px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:middle;width:38px">
        <div style="width:40px;height:40px;background:#000;border-radius:9px;padding:5px 0;text-align:center">{_logo_cell()}</div>
      </td>
      <td style="vertical-align:middle;padding-left:14px">
        <div style="font:600 22px/1 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;letter-spacing:-.02em;color:{OFFWHITE}">Spawn<span style="color:{APRICOT}">WP</span></div>
        <div style="font:400 13px/1.3 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#9a968c;margin-top:4px">Telemetry snapshot · {now:%A, %-d %B %Y}</div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:24px 22px 6px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{kpi_cells}</tr></table>
  </td></tr>
  {extended_note}
  {sections}
  <tr><td style="padding:24px 28px 26px">
    <div style="border-top:1px solid {BORDER};padding-top:16px;font:400 12px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{MUTED}">
      Anonymous, aggregate and directional — never domains, IP addresses, site names, content or credentials.
      Counts come from installations that consented within the last 90 days and are not authoritative.<br>
      Generated {now:%Y-%m-%d %H:%M %Z}.
    </div>
  </td></tr>
</table>
</td></tr></table></body></html>"""


def build_message(config: dict, report: dict, now: datetime) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config["from"]
    message["To"] = config["to"]
    message["Subject"] = f"SpawnWP telemetry — {now:%Y-%m-%d}"
    message.set_content(
        "SpawnWP aggregate telemetry snapshot\n"
        f"Generated: {now.isoformat()}\n\n{report_module.render_text(report)}\n"
    )
    message.add_alternative(render_html(report, now), subtype="html")
    return message


def send_message(config: dict, message: EmailMessage) -> None:
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
    parser.add_argument("--dry-run", action="store_true", help="write files instead of sending")
    parser.add_argument("--html-out", type=Path, help="where to write the HTML (with --dry-run)")
    args = parser.parse_args()

    now = datetime.now(ZoneInfo("Europe/Rome"))
    report = report_module.collect()

    if args.dry_run:
        html = render_html(report, now)
        if args.html_out:
            args.html_out.write_text(html, encoding="utf-8")
            print(f"HTML written to {args.html_out}")
        print("\n" + report_module.render_text(report))
        return

    config = load_config(args.config)
    send_message(config, build_message(config, report, now))
    print(f"SpawnWP telemetry report sent for {now:%Y-%m-%d}")


if __name__ == "__main__":
    main()
