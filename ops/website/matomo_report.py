#!/usr/bin/env python3
"""Report spawnwp.com acquisition and SEO-funnel conversion from Matomo.

Matomo measures what visitors do once they arrive; it cannot report search
rankings (Google strips organic keywords). Ranking, impressions and average
position come from Search Console — see ops/website/README.md, "Measurement".

Credentials live in a root-only JSON file (default /etc/spawnwp-matomo.json):

    {"url": "https://stats.presenzaweb.net/", "token_auth": "...", "id_site": 6}

Create the token in Matomo as a dedicated *read-only* user, not as admin. The
token is never printed, logged or included in the output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from matomo_measurement import EVENTS, bounds, collect

DEFAULT_CONFIG = Path(os.environ.get("SPAWNWP_MATOMO_CONFIG", "/etc/spawnwp-matomo.json"))
TIMEOUT = 30

# The events analytics.js emits (SEO Funnel category), in funnel order. The
# event label is the landing path, which is what makes per-page attribution work.
FUNNEL_ACTIONS = list(EVENTS)
NAVIGATION_ACTIONS = [
    "explore_wordpress_sandbox", "explore_alternatives", "explore_plugin_development",
    "read_sandbox_vs_staging", "compare_instawp", "compare_localwp", "compare_tastewp",
    "compare_spinupwp", "compare_easyengine",
]


class MatomoError(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        raise MatomoError(
            f"{path} not found. Create it with 0600 permissions:\n"
            '  {"url": "https://stats.presenzaweb.net/", "token_auth": "...", "id_site": 6}'
        ) from None
    except json.JSONDecodeError as exc:
        raise MatomoError(f"{path} is not valid JSON: {exc}") from None
    for key in ("url", "token_auth", "id_site"):
        if not config.get(key):
            raise MatomoError(f"{path} is missing {key!r}")
    return config


def call(config: dict, method: str, period: str, date: str, **extra) -> object:
    """One Reporting API call. token_auth goes in the POST body, never the URL,
    so it cannot leak into access logs or shell history."""
    params = {
        "module": "API", "format": "JSON", "method": method,
        "idSite": str(config["id_site"]), "period": period, "date": date,
        "token_auth": config["token_auth"],
    }
    params.update({k: str(v) for k, v in extra.items()})
    request = urllib.request.Request(
        urllib.parse.urljoin(config["url"], "index.php"),
        data=urllib.parse.urlencode(params).encode(),
        headers={"User-Agent": "spawnwp-matomo-report/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.URLError as exc:
        raise MatomoError(f"{method}: request failed: {exc}") from None
    except json.JSONDecodeError:
        raise MatomoError(f"{method}: response was not JSON") from None
    if isinstance(payload, dict) and payload.get("result") == "error":
        # Matomo reports auth failures here rather than with an HTTP status.
        raise MatomoError(f"{method}: {payload.get('message', 'unknown API error')}")
    return payload


def rows(payload: object) -> list[dict]:
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def event_breakdown(config: dict, period: str, date: str,
                    actions: list[str]) -> dict[str, dict]:
    """Map selected event actions to their source pages, via the event-name subtable.

    Events.getName returns the top level; the per-page breakdown lives in each
    row's subtable, which is why this needs a second call per action."""
    breakdown: dict[str, dict] = {}
    for row in rows(call(config, "Events.getAction", period, date, filter_limit=-1)):
        action = str(row.get("label", ""))
        if action not in actions:
            continue
        pages: list[tuple[str, int]] = []
        subtable = row.get("idsubdatatable")
        if subtable:
            for entry in rows(call(config, "Events.getNameFromActionId", period, date,
                                   idSubtable=subtable, filter_limit=25)):
                pages.append((str(entry.get("label", "?")), int(entry.get("nb_events", 0) or 0)))
        breakdown[action] = {"total": int(row.get("nb_events", 0) or 0), "visits": int(row.get("nb_visits", 0) or 0), "pages": pages}
    return breakdown


def section(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


def report(config: dict, period: str, date: str) -> None:
    summary = call(config, "VisitsSummary.get", period, date)
    visits = int(summary.get("nb_visits", 0) or 0) if isinstance(summary, dict) else 0

    section(f"spawnwp.com — {period} {date}")
    if isinstance(summary, dict):
        print(f"visits {visits}   unique {summary.get('nb_uniq_visitors', 'n/a')}   "
              f"actions {summary.get('nb_actions', 'n/a')}   "
              f"bounce {summary.get('bounce_rate', 'n/a')}")

    section("Landing pages")
    entry_rows = rows(call(config, "Actions.getEntryPageUrls", period, date,
                           flat=1, filter_limit=20, filter_sort_column="entry_nb_visits"))
    if not entry_rows:
        print("(no data)")
    for row in entry_rows:
        label = str(row.get("label", "?"))
        print(f"{int(row.get('entry_nb_visits', 0) or 0):6d}  {label}")

    section("Acquisition")
    for row in rows(call(config, "Referrers.getReferrerType", period, date)):
        print(f"{int(row.get('nb_visits', 0) or 0):6d}  {row.get('label', '?')}")
    engines = rows(call(config, "Referrers.getSearchEngines", period, date, filter_limit=10))
    if engines:
        print("  search engines:")
        for row in engines:
            print(f"{int(row.get('nb_visits', 0) or 0):6d}    {row.get('label', '?')}")

    section("Website intent (events, not completed installations)")
    event_rows = call(config, "Events.getAction", period, date, filter_limit=-1)
    start, end = bounds(period, date)
    measurement = collect(lambda method, **extra: call(config, method, period, date, **extra),
                          start, end, visits, event_rows)
    breakdown = event_breakdown(config, period, date, FUNNEL_ACTIONS)
    for action, metric in measurement["events"].items():
        if metric["count"] is None:
            print(f"{action}: non disponibile")
            continue
        rate = f"{metric['rate']:.1%}" if metric["rate"] is not None else "n/a"
        print(f"{action}: {metric['count']} events; {metric['visits']} visits; share {rate} [{metric['status']}]")
        for label, count in breakdown.get(action, {}).get("pages", []):
            print(f"        {count:5d}  {label}")

    section("Matomo goals (one conversion per goal per visit)")
    for goal in measurement["goals"].values():
        if goal["conversions"] is None:
            print(f"{goal['name']}: non disponibile (not configured or before activation)")
            continue
        rate = f"{goal['rate']:.1%}" if goal["rate"] is not None else "n/a"
        print(f"{goal['name']}: {goal['conversions']} conversions; rate {rate} [{goal['status']}]")
    print("Partial = interval includes activation day; do not compare rates across this boundary.")

    section("SEO navigation (internal content paths)")
    navigation = event_breakdown(config, period, date, NAVIGATION_ACTIONS)
    found = False
    for action in NAVIGATION_ACTIONS:
        entry = navigation.get(action)
        if entry is None:
            continue
        found = True
        total = entry["total"]
        pages = entry["pages"]
        print(f"{action}: {total}")
        for label, count in sorted(pages, key=lambda item: -item[1]):
            print(f"        {count:5d}  {label}")
    if not found:
        print("(no data; events start after the SEO navigation tracking release)")

    print("\nRanking, impressions and average position are NOT in Matomo — use Search Console.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--period", default="month",
                        choices=["day", "week", "month", "year", "range"])
    parser.add_argument("--date", default="today",
                        help="Matomo date: today, yesterday, last30, 2026-07-01,2026-07-18")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        report(load_config(args.config), args.period, args.date)
    except MatomoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
