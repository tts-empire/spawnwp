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
PAYLOAD_DIR = Path(os.environ.get("SPAWNWP_BLUEPRINT_PAYLOADS", "/var/lib/spawnwp/blueprints"))
BLUEPRINT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
WPORG_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DIR_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CREATED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
PAYLOAD_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PHP_VERSIONS = {"7.4", "8.2", "8.3", "8.4"}
MAX_PAYLOAD_BYTES = 2 * 1024**3
FIELDS = {
    "schema_version", "id", "name", "version", "description", "php",
    "wordpress", "debug", "plugins", "theme", "devkit", "content_preset",
}
V2_FIELDS = {
    "schema_version", "id", "name", "version", "description", "php",
    "wordpress", "created_at", "capture", "payload",
    "wporg_plugins", "premium_plugins", "theme",
}
CAPTURE_KEYS = {"plugins", "themes", "uploads", "database"}


class BlueprintError(ValueError):
    pass


def _validate_identity(raw: dict, source: Path) -> None:
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
        raise BlueprintError("wordpress must be latest")


def _validate_v1(raw: dict, source: Path) -> dict:
    unknown = set(raw) - FIELDS
    missing = FIELDS - set(raw)
    if unknown:
        raise BlueprintError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise BlueprintError(f"missing fields: {', '.join(sorted(missing))}")
    _validate_identity(raw, source)
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
    return dict(raw)


def _validate_v2(raw: dict, source: Path, check_payload: bool) -> dict:
    unknown = set(raw) - V2_FIELDS
    missing = V2_FIELDS - set(raw)
    if unknown:
        raise BlueprintError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise BlueprintError(f"missing fields: {', '.join(sorted(missing))}")
    if source.parent != CUSTOM_DIR:
        raise BlueprintError("schema v2 blueprints are only allowed in the custom directory")
    _validate_identity(raw, source)
    if not isinstance(raw["created_at"], str) or not CREATED_AT.fullmatch(raw["created_at"]):
        raise BlueprintError("created_at must be an UTC timestamp like 2026-01-01T00:00:00Z")
    capture = raw["capture"]
    if not isinstance(capture, dict) or set(capture) != CAPTURE_KEYS:
        raise BlueprintError("capture must contain exactly plugins, themes, uploads and database")
    if any(not isinstance(v, bool) for v in capture.values()):
        raise BlueprintError("capture flags must be booleans")
    if not any(capture.values()):
        raise BlueprintError("capture must include at least one component")
    payload = raw["payload"]
    if not isinstance(payload, dict) or set(payload) != {"file", "bytes", "sha256"}:
        raise BlueprintError("payload must contain exactly file, bytes and sha256")
    if not isinstance(payload["file"], str) or not PAYLOAD_NAME.fullmatch(payload["file"]):
        raise BlueprintError("payload.file must be a bare archive filename")
    if not isinstance(payload["bytes"], int) or isinstance(payload["bytes"], bool) \
            or not 1 <= payload["bytes"] <= MAX_PAYLOAD_BYTES:
        raise BlueprintError("payload.bytes must be between 1 and 2 GiB")
    if not isinstance(payload["sha256"], str) or not SHA256_HEX.fullmatch(payload["sha256"]):
        raise BlueprintError("payload.sha256 must be 64 lowercase hex characters")
    wporg = raw["wporg_plugins"]
    if not isinstance(wporg, list) or len(wporg) > 64 or len(set(wporg)) != len(wporg):
        raise BlueprintError("wporg_plugins must be a unique list of at most 64 slugs")
    if any(not isinstance(slug, str) or not WPORG_SLUG.fullmatch(slug) for slug in wporg):
        raise BlueprintError("wporg_plugins may contain only WordPress.org-style slugs")
    premium = raw["premium_plugins"]
    if not isinstance(premium, list) or len(premium) > 64:
        raise BlueprintError("premium_plugins must be a list of at most 64 entries")
    for entry in premium:
        if not isinstance(entry, dict) or set(entry) != {"name", "slug", "version"}:
            raise BlueprintError("premium_plugins entries must contain exactly name, slug and version")
        if not isinstance(entry["name"], str) or not 1 <= len(entry["name"]) <= 100:
            raise BlueprintError("premium plugin name must contain 1-100 characters")
        if not isinstance(entry["slug"], str) or not DIR_SLUG.fullmatch(entry["slug"]):
            raise BlueprintError("premium plugin slug must be a plugin directory name")
        if not isinstance(entry["version"], str) or not 1 <= len(entry["version"]) <= 32:
            raise BlueprintError("premium plugin version must contain 1-32 characters")
    theme = raw["theme"]
    if theme is not None and (not isinstance(theme, str) or not DIR_SLUG.fullmatch(theme)):
        raise BlueprintError("theme must be null or a theme directory name")
    if check_payload:
        payload_path = PAYLOAD_DIR / raw["id"] / payload["file"]
        if not payload_path.is_file():
            raise BlueprintError(f"payload archive is missing: {payload_path}")
        if payload_path.stat().st_size != payload["bytes"]:
            raise BlueprintError("payload archive size does not match the manifest")
    return dict(raw)


def validate(raw: object, source: Path, check_payload: bool = True) -> dict:
    if not isinstance(raw, dict):
        raise BlueprintError("manifest must be a JSON object")
    schema = raw.get("schema_version")
    if schema == 1:
        result = _validate_v1(raw, source)
    elif schema == 2:
        result = _validate_v2(raw, source, check_payload)
    else:
        raise BlueprintError("schema_version must be 1 or 2")
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
    if item["schema_version"] == 2:
        item["payload_path"] = str(PAYLOAD_DIR / item["id"] / item["payload"]["file"])
    return item


def shell_values(item: dict) -> dict[str, str]:
    v2 = item["schema_version"] == 2
    capture = item.get("capture", {})
    return {
        "BLUEPRINT_ID": item["id"],
        "BLUEPRINT_NAME": item["name"],
        "BLUEPRINT_VERSION": item["version"],
        "BLUEPRINT_SCHEMA": str(item["schema_version"]),
        "PHP_VERSION": item["selected_php"],
        "WP_VERSION": item["wordpress"],
        "WORDPRESS_SERIES": item["wordpress_series"],
        "WP_DEBUG_VALUE": "true" if item.get("debug") else "",
        "BLUEPRINT_PLUGINS": " ".join(item["wporg_plugins"] if v2 else item["plugins"]),
        "BLUEPRINT_THEME": item["theme"] or "",
        "BLUEPRINT_DEVKIT": "1" if item.get("devkit") else "0",
        "BLUEPRINT_CONTENT": "payload" if v2 else item["content_preset"],
        "BLUEPRINT_PAYLOAD": item.get("payload_path", ""),
        "BLUEPRINT_PAYLOAD_SHA256": item["payload"]["sha256"] if v2 else "",
        "BLUEPRINT_CAPTURE_PLUGINS": "1" if capture.get("plugins") else "0",
        "BLUEPRINT_CAPTURE_THEMES": "1" if capture.get("themes") else "0",
        "BLUEPRINT_CAPTURE_UPLOADS": "1" if capture.get("uploads") else "0",
        "BLUEPRINT_CAPTURE_DATABASE": "1" if capture.get("database") else "0",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    resolve_parser = commands.add_parser("resolve")
    resolve_parser.add_argument("blueprint_id")
    resolve_parser.add_argument("--php")
    resolve_parser.add_argument("--output", type=Path)
    resolve_parser.add_argument("--shell", action="store_true")
    stdin_parser = commands.add_parser("validate-stdin")
    stdin_parser.add_argument("--filename", required=True)
    stdin_parser.add_argument("--skip-payload", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "list":
            found, errors = discover()
            print(json.dumps({"blueprints": list(found.values()), "errors": errors}, separators=(",", ":")))
            return 0
        if args.command == "validate-stdin":
            if "/" in args.filename or not args.filename.endswith(".json"):
                raise BlueprintError("filename must be a bare .json name")
            try:
                raw = json.loads(sys.stdin.read())
            except json.JSONDecodeError as exc:
                raise BlueprintError(f"invalid JSON: {exc}") from exc
            item = validate(raw, CUSTOM_DIR / args.filename, check_payload=not args.skip_payload)
            print(json.dumps(item, separators=(",", ":")))
            return 0
        item = resolve(args.blueprint_id, args.php)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")
        if args.shell:
            for key, value in shell_values(item).items():
                print(f"{key}={shlex.quote(value)}")
        elif not args.output:
            print(json.dumps(item, separators=(",", ":")))
        return 0
    except BlueprintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
