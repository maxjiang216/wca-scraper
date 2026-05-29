#!/usr/bin/env python3
"""Compatibility entrypoint for the watch-list notifier.

Same flags as ``wca notify``. Prefer ``uv run wca notify --help``.

Usage:
    python psych_sheet_notifier.py --watch-list watch_list.yaml \
        --email you@example.com --dry-run
"""

import argparse
import logging

from wca_org.psych_sheet_notifier import (  # type: ignore[import-not-found]
    add_notify_arguments,
    run_notify_from_args,
)


def main() -> None:
    """Parse args and run the notifier (compatibility shim)."""
    parser = argparse.ArgumentParser(
        description=(
            "Notify when watched WCA competitors are registered for "
            "upcoming competitions."
        ),
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
