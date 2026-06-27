#!/usr/bin/env python3
"""Validate and resolve SpawnWP blueprint manifests."""

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

BUILTIN_DIR = Path(os.environ.get("SPAWNWP_BUILTIN_BLUEPRINTS", "/srv/wp-dev/blueprints"))
CUSTOM_DIR = Path(os.environ.get("SPAWNWP_CUSTOM_BLUEPRINTS", "/etc/spawnwp/blueprints.d"))
BLUEPRINT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
WPORG_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
PHP_VERSIONS = {"7.4", "8.2", "8.3", "8.4"}
FIELDS = {
    "schema_version", "id", "name", "version", "description", "php",
    "wordpress", "debug", "plugins", "theme", "devkit", "content_preset",
}


class BlueprintError(ValueError):
    pass


def validate(raw: object, source: Path) -> dict:
    if not isinstance(raw, dict):
        raise BlueprintError("manifest must be a JSON object")
    unknown = set(raw) - FIELDS
    missing = FIELDS - set(raw)
    if unknown:
        raise BlueprintError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise BlueprintError(f"missing fields: {', '.join(sorted(missing))}")
    if raw["schema_version"] != 1:
        raise BlueprintError("schema_version must be 1")
    if not isinstance(raw["id"], str) or not BLUEPRINT_ID.fullmatch(raw["id"]):
        raise BlueprintError("id must use lowercase letters, digits and hyphens")
    if source.stem != raw["id"]:
        raise BlueprintError("filename must match blueprint id")
    if not isinstance(raw["name"], str) or not 1 <= len(raw["name"]) <= 60:
        raise BlueprintError("name must contain 1-60 characters")
    if not isinstance(raw["description"], str) or not 1 <= len(raw["description"]) <= 240:
        raise BlueprintError("description must contain 1-240 characters")
    if not isinstance(raw["version"], str) or not SEMVER.fullmatch(raw["version"]):
        raise BlueprintError("version must use MAJOR.MINOR.PATCH")
    php = raw["php"]
    if not isinstance(php, dict) or set(php) != {"default", "allowed"}:
        raise BlueprintError("php must contain only default and allowed")
    allowed = php["allowed"]
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise BlueprintError("php.allowed must be a non-empty unique list")
    if any(version not in PHP_VERSIONS for version in allowed):
        raise BlueprintError("php.allowed contains an unsupported version")
    if php["default"] not in allowed:
        raise BlueprintError("php.default must be present in php.allowed")
    if raw["wordpress"] != "latest":
        raise BlueprintError("wordpress must be latest in schema v1")
    if not isinstance(raw["debug"], bool) or not isinstance(raw["devkit"], bool):
        raise BlueprintError("debug and devkit must be booleans")
    plugins = raw["plugins"]
    if not isinstance(plugins, list) or len(set(plugins)) != len(plugins):
        raise BlueprintError("plugins must be a unique list")
    if any(not isinstance(slug, str) or not WPORG_SLUG.fullmatch(slug) for slug in plugins):
        raise BlueprintError("plugins may contain only WordPress.org-style slugs")
    theme = raw["theme"]
    if theme is not None and (not isinstance(theme, str) or not WPORG_SLUG.fullmatch(theme)):
        raise BlueprintError("theme must be null or a WordPress.org-style slug")
    if raw["content_preset"] not in {"empty", "demo"}:
        raise BlueprintError("content_preset must be empty or demo")
    result = dict(raw)
    result["source"] = "custom" if source.parent == CUSTOM_DIR else "built-in"
    return result


def discover() -> tuple[dict[str, dict], list[dict]]:
    found: dict[str, dict] = {}
    errors: list[dict] = []
    for directory in (BUILTIN_DIR, CUSTOM_DIR):
        if not directory.is_dir():
            continue
        for source in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(source.read_text(encoding="utf-8"))
                item = validate(raw, source)
                if item["id"] in found:
                    raise BlueprintError(f"duplicate id already provided by {found[item['id']]['source']}")
                found[item["id"]] = item
            except (OSError, json.JSONDecodeError, BlueprintError) as exc:
                errors.append({"file": str(source), "error": str(exc)})
    return found, errors


def resolve(blueprint_id: str, php_version: str | None) -> dict:
    found, errors = discover()
    if blueprint_id not in found:
        details = next((e["error"] for e in errors if Path(e["file"]).stem == blueprint_id), None)
        raise BlueprintError(details or f"unknown blueprint: {blueprint_id}")
    item = dict(found[blueprint_id])
    selected = php_version or item["php"]["default"]
    if selected not in item["php"]["allowed"]:
        raise BlueprintError(f"PHP {selected} is not allowed by blueprint {blueprint_id}")
    item["selected_php"] = selected
    item["wordpress_series"] = "6" if selected == "7.4" else "7"
    return item


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    resolve_parser = commands.add_parser("resolve")
    resolve_parser.add_argument("blueprint_id")
    resolve_parser.add_argument("--php")
    resolve_parser.add_argument("--output", type=Path)
    resolve_parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "list":
            found, errors = discover()
            print(json.dumps({"blueprints": list(found.values()), "errors": errors}, separators=(",", ":")))
            return 0
        item = resolve(args.blueprint_id, args.php)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")
        if args.shell:
            values = {
                "BLUEPRINT_ID": item["id"],
                "BLUEPRINT_NAME": item["name"],
                "BLUEPRINT_VERSION": item["version"],
                "PHP_VERSION": item["selected_php"],
                "WP_VERSION": item["wordpress"],
                "WORDPRESS_SERIES": item["wordpress_series"],
                "WP_DEBUG_VALUE": "true" if item["debug"] else "",
                "BLUEPRINT_PLUGINS": " ".join(item["plugins"]),
                "BLUEPRINT_THEME": item["theme"] or "",
                "BLUEPRINT_DEVKIT": "1" if item["devkit"] else "0",
                "BLUEPRINT_CONTENT": item["content_preset"],
            }
            for key, value in values.items():
                print(f"{key}={shlex.quote(value)}")
        elif not args.output:
            print(json.dumps(item, separators=(",", ":")))
        return 0
    except BlueprintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
