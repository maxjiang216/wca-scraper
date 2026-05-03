#!/usr/bin/env python3
"""
Unified CLI: ``wca notify`` (watch list email) and ``wca report`` (top-ranked registrants).
"""

from __future__ import annotations

import argparse
import logging

from .psych_sheet_notifier import add_notify_arguments, run_notify_from_args
from .top_competitors_report import add_report_arguments, run_report_from_args


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wca",
        description="WCA tools: notifier and top-competitors report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    notify_p = sub.add_parser(
        "notify",
        help="Email when watch-listed competitors enter upcoming competitions",
    )
    add_notify_arguments(notify_p)

    report_p = sub.add_parser(
        "report",
        help="Markdown/plain report of world-ranked registrants at upcoming comps",
    )
    add_report_arguments(report_p)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    if args.command == "notify":
        run_notify_from_args(args)
    elif args.command == "report":
        run_report_from_args(args)
    else:
        parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
