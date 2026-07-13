#!/usr/bin/env python3
"""Validate public HTML metadata, JSON-LD, assets, links and sitemap coverage."""

from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.canonical = ""
        self.description = ""
        self.robots = ""
        self.ids: list[str] = []
        self.links: list[str] = []
        self.assets: list[str] = []
        self.jsonld: list[str] = []
        self._jsonld = False
        self._json_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href") or ""
        if tag == "meta" and values.get("name") == "description":
            self.description = values.get("content") or ""
        if tag == "meta" and values.get("name") == "robots":
            self.robots = values.get("content") or ""
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag in {"img", "script", "source", "link"}:
            candidate = values.get("src") or values.get("srcset")
            if tag == "link" and values.get("rel") in {"stylesheet", "icon"}:
                candidate = values.get("href")
            if candidate:
                self.assets.append(candidate.split()[0])
        if tag == "script" and values.get("type") == "application/ld+json":
            self._jsonld = True
            self._json_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._jsonld:
            self.jsonld.append("".join(self._json_buffer))
            self._jsonld = False

    def handle_data(self, data: str) -> None:
        if self._jsonld:
            self._json_buffer.append(data)


def local_target(root: Path, value: str) -> Path | None:
    if not value.startswith("/") or value.startswith("//"):
        return None
    parsed = urlparse(value)
    path = parsed.path
    if not path or path.startswith(("/docs/", "/downloads/", "/api/")):
        return None
    if path == "/install.sh":
        return root / "install.sh"
    candidate = root / path.lstrip("/")
    if path.endswith("/"):
        candidate /= "index.html"
    return candidate


def main() -> None:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--root", type=Path, required=True)
    arguments.add_argument("--sitemap", type=Path)
    args = arguments.parse_args()
    errors: list[str] = []
    canonicals: dict[str, Path] = {}

    for page in sorted(args.root.rglob("*.html")):
        relative_parts = page.relative_to(args.root).parts
        if "mkdocs-overrides" in relative_parts or "docs" in relative_parts or page.name == "404.html":
            continue
        parsed = PageParser()
        parsed.feed(page.read_text(encoding="utf-8"))
        label = str(page)
        if parsed.h1 != 1:
            errors.append(f"{label}: expected one h1, found {parsed.h1}")
        if len(parsed.ids) != len(set(parsed.ids)):
            errors.append(f"{label}: duplicate id")
        if not parsed.description:
            errors.append(f"{label}: missing meta description")
        if "noindex" not in parsed.robots.lower():
            if not parsed.canonical:
                errors.append(f"{label}: missing canonical")
            elif parsed.canonical in canonicals:
                errors.append(f"{label}: duplicate canonical also in {canonicals[parsed.canonical]}")
            else:
                canonicals[parsed.canonical] = page
        for block in parsed.jsonld:
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                errors.append(f"{label}: invalid JSON-LD: {exc}")
        for value in parsed.assets + parsed.links:
            target = local_target(args.root, value)
            if target is not None and not target.exists():
                errors.append(f"{label}: missing local target {value}")

    if args.sitemap:
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        tree = ElementTree.parse(args.sitemap)
        sitemap_urls = {node.text or "" for node in tree.findall("s:url/s:loc", namespace)}
        if sitemap_urls != set(canonicals):
            missing = sorted(set(canonicals) - sitemap_urls)
            extra = sorted(sitemap_urls - set(canonicals))
            errors.append(f"sitemap mismatch; missing={missing}, extra={extra}")

    if errors:
        raise SystemExit("\n".join(errors))
    print(f"validated {len(canonicals)} canonical HTML pages")


if __name__ == "__main__":
    main()
