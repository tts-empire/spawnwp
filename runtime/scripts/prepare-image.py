#!/usr/bin/env python3
"""Prepare an image without changing the selected runtime of any site."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_images import prepare, write_atomic

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--wordpress", default="latest")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output-ref", type=Path)
    args = parser.parse_args()
    try:
        reference = prepare(args.project.resolve(), args.version, args.wordpress, refresh=args.refresh)
        if args.output_ref:
            write_atomic(args.output_ref, reference + "\n")
        print("Prepared image: " + reference)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
