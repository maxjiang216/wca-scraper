#!/usr/bin/env python3
"""
Unified CLI: ``wca notify``, ``wca weekly``, ``wca records``, ``wca report``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .psych_sheet_notifier import add_notify_arguments, parse_date, run_notify_from_args
from .records_notifier import run_records_check
from .notify import format_records_alert, send_email
from .top_competitors_report import add_report_arguments, run_report_from_args
from .watch_list import load_watch_list_document
from .weekly_digest import run_weekly_digest


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wca",
        description="WCA tools: notifier, weekly digest, records alert, top-competitors report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    notify_p = sub.add_parser(
        "notify",
        help="Email when watch-listed competitors enter upcoming competitions",
    )
    add_notify_arguments(notify_p)

    weekly_p = sub.add_parser(
        "weekly",
        help="Weekly email: upcoming psych sheet only",
    )
    add_notify_arguments(weekly_p)

    records_p = sub.add_parser(
        "records",
        help="Daily email: WCA Live + competition results (OR rules per event)",
    )
    records_p.add_argument(
        "--watch-list", "-w",
        type=Path,
        default=Path("watch_list.yaml"),
        help="Path to watch list YAML",
    )
    records_p.add_argument("--email", "-e", help="Recipient email")
    records_p.add_argument("--dry-run", action="store_true", help="Do not send email or update state")
    records_p.add_argument(
        "--ended-days",
        type=int,
        default=2,
        help="Fetch results for competitions that ended in this many days (default: 2)",
    )
    records_p.add_argument(
        "--state-file",
        type=Path,
        default=Path("records_state.json"),
        help="JSON for Live dedupe + competition result ids (default: ./records_state.json)",
    )
    records_p.add_argument("--smtp-host", default="smtp.gmail.com")
    records_p.add_argument("--smtp-port", type=int, default=587)
    records_p.add_argument("--smtp-user", help="SMTP username")
    records_p.add_argument("--smtp-password", help="SMTP password")

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
    elif args.command == "weekly":
        run_weekly_digest(
            watch_list_path=args.watch_list,
            notify_email=args.email,
            start_date=parse_date(args.start) if args.start else None,
            end_date=parse_date(args.end) if args.end else None,
            country_filter=args.country,
            weeks_ahead=args.weeks,
            rate_limit_delay_s=args.rate_limit_delay,
            timezone=args.timezone,
            cubing_china=args.cubing_china,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password=args.smtp_password,
            dry_run=args.dry_run,
        )
    elif args.command == "records":
        cfg, events = load_watch_list_document(args.watch_list)
        if not events:
            logging.error("Watch list has no events: %s", args.watch_list)
            raise SystemExit(1)
        if not args.email and not args.dry_run:
            logging.error("Use --email or --dry-run")
            raise SystemExit(1)
        new_live, comp_rows = run_records_check(
            events=events,
            cfg=cfg,
            state_path=args.state_file,
            update_state=not args.dry_run,
            ended_days=args.ended_days,
        )
        if not new_live and not comp_rows:
            logging.info("No new items matched your rules.")
            return
        html_body = format_records_alert(new_live, comp_rows, timezone=cfg.timezone)
        subj_parts = []
        if new_live:
            subj_parts.append(f"{len(new_live)} Live")
        if comp_rows:
            subj_parts.append(f"{len(comp_rows)} results")
        subject = "WCA: " + ", ".join(subj_parts)
        if args.dry_run:
            logging.info("--- DRY RUN (records) ---\nSubject: %s", subject)
            Path("wca_records_dry_run.txt").write_text(html_body, encoding="utf-8")
            logging.info("Wrote wca_records_dry_run.txt")
            return
        send_email(
            to_email=args.email,
            subject=subject,
            html_body=html_body,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password=args.smtp_password,
        )
        logging.info("Records alert sent to %s", args.email)
    elif args.command == "report":
        run_report_from_args(args)
    else:
        parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
