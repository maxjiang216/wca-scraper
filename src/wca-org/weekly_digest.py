#!/usr/bin/env python3
"""Weekly email: upcoming psych sheet only (WCA + optional Cubing China)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from .notify import format_weekly_digest, send_email
from .psych_sheet_notifier import _html_to_plain_text, gather_psych_sheet_results
from .watch_list import load_watch_list_document


def run_weekly_digest(
    *,
    watch_list_path: Path,
    notify_email: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    country_filter: str | None = None,
    weeks_ahead: int = 8,
    rate_limit_delay_s: float = 1.0,
    timezone: str | None = None,
    cubing_china: bool = False,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    dry_run: bool = False,
) -> None:
    cfg, events = load_watch_list_document(watch_list_path)
    if not events:
        logging.error(
            "Watch list is empty or file not found. Use --watch-list path/to/watch_list.yaml",
        )
        return
    if not notify_email and not dry_run:
        logging.error("Use --email to receive notifications, or --dry-run to test.")
        return

    tz = timezone or cfg.timezone
    today = datetime.now().date()
    if start_date is None and end_date is None:
        start = datetime(today.year, today.month, today.day)
        end = start + timedelta(weeks=weeks_ahead)
    elif start_date is not None and end_date is None:
        start = start_date
        end = start + timedelta(weeks=weeks_ahead)
    elif start_date is None and end_date is not None:
        start = datetime(today.year, today.month, today.day)
        end = end_date
    else:
        start = start_date
        end = end_date

    psych_results = gather_psych_sheet_results(
        events,
        start=start,
        end=end,
        country_filter=country_filter,
        rate_limit_delay_s=rate_limit_delay_s,
        timezone=tz,
        cubing_china=cubing_china,
    )

    html_body = format_weekly_digest(psych_results, timezone=tz)
    subject = "WCA weekly: psych sheet"
    if dry_run:
        logging.info("--- DRY RUN (weekly) ---")
        logging.info("To: %s", notify_email)
        logging.info("Subject: %s", subject)
        out_path = Path("wca_weekly_dry_run.txt")
        out_path.write_text(
            "\n".join([
                f"To: {notify_email}",
                f"Subject: {subject}",
                "",
                _html_to_plain_text(html_body),
            ]),
            encoding="utf-8",
        )
        logging.info("Wrote %s", out_path.resolve())
        return

    send_email(
        to_email=notify_email or "",
        subject=subject,
        html_body=html_body,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )
    logging.info("Weekly digest sent to %s", notify_email)
