#!/usr/bin/env python3
"""Standalone scrape entry point for launchd / cron.

Usage:
    python scripts/run_scrape.py
"""
import logging
import sys
from pathlib import Path

# Make the project root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_scrape  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = run_scrape()
    print(result)
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
