#!/usr/bin/env python3
"""
Compatibility entrypoint for the watch-list notifier (same flags as ``wca notify``).

Prefer: ``uv run wca notify --help``

Usage:
    python psych_sheet_notifier.py --watch-list watch_list.json --email you@example.com --dry-run
"""

import argparse
import logging

from wca_org.psych_sheet_notifier import add_notify_arguments, run_notify_from_args


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Notify when watched WCA competitors are registered for upcoming competitions.",
    )
    add_notify_arguments(parser)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    run_notify_from_args(args)


if __name__ == "__main__":
    main()
