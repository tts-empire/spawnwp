#!/usr/bin/env python3
"""Generate the public landing-page sitemap from canonical HTML pages."""

from __future__ import annotations

import argparse
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical = ""
        self.robots = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical = values.get("href") or ""
        if tag == "meta" and values.get("name") == "robots":
            self.robots = values.get("content") or ""


def git_date(repo: Path, page: Path) -> str:
    try:
        relative = page.resolve().relative_to(repo.resolve())
    except ValueError:
        return date.today().isoformat()
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(relative)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or date.today().isoformat()


def canonical_pages(root: Path, repo: Path) -> list[tuple[str, str]]:
    pages: list[tuple[str, str]] = []
    for page in sorted(root.rglob("index.html")):
        parser = HeadParser()
        parser.feed(page.read_text(encoding="utf-8"))
        if not parser.canonical or "noindex" in parser.robots.lower():
            continue
        parsed = urlparse(parser.canonical)
        if parsed.scheme != "https" or parsed.netloc != "spawnwp.com":
            raise SystemExit(f"{page}: invalid public canonical {parser.canonical!r}")
        pages.append((parser.canonical, git_date(repo, page)))
    return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pages = canonical_pages(args.root, args.repo)
    if not pages:
        raise SystemExit("no canonical pages found")
    urls = "\n".join(
        f"  <url><loc>{escape(url)}</loc><lastmod>{modified}</lastmod></url>"
        for url, modified in pages
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"generated {args.output} with {len(pages)} URLs")


if __name__ == "__main__":
    main()
