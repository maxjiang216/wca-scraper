#!/usr/bin/env python3
"""
WCA Psych Sheet Notifier

Runs weekly to check upcoming competitions for competitors on your watch list
and emails you when any are competing.

Usage:
    python psych_sheet_notifier.py --watch-list watch_list.json --email you@example.com
    python psych_sheet_notifier.py --help
"""

import argparse
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .wca_api import (
    get_upcoming_competitions,
    get_competition_wcif,
    get_watched_competitors_in_psych_sheet,
    get_competitor_schedule,
)
from .notify import format_results_by_week, send_email


def load_watch_list(path: Path) -> dict[str, set[str]]:
    """Load watch list: {event_id: {wca_id, ...}}."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    watch_list = {}
    for event_id, ids in raw.items():
        if isinstance(ids, list):
            watch_list[event_id] = {str(i).strip().upper() for i in ids}
        else:
            watch_list[event_id] = {str(ids).strip().upper()}
    return watch_list


def _html_to_plain_text(html_str: str) -> str:
    """Convert simple HTML to plain text for txt dump."""
    text = re.sub(r"<br\s*/?>", "\n", html_str, flags=re.IGNORECASE)
    text = re.sub(r"</(h[12]|li|p)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _format_eta(seconds: float) -> str:
    """Format seconds as human-readable ETA (e.g. '2m 15s')."""
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def parse_date(s: str) -> datetime | None:
    """Parse YYYY-MM-DD, return None if empty/None."""
    if not s:
        return None
    return datetime.strptime(s.strip(), "%Y-%m-%d")


def run(
    *,
    watch_list_path: Path,
    notify_email: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    country_filter: str | None = None,
    weeks_ahead: int = 2,
    rate_limit_delay_s: float = 1.0,
    timezone: str = "America/Los_Angeles",
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Main logic: fetch upcoming comps, check psych sheets, send email if any
    watched competitors are competing.
    """
    watch_list = load_watch_list(watch_list_path)
    if not watch_list:
        logging.error("Watch list is empty or file not found. Use --watch-list path/to/watch_list.json")
        return
    if not notify_email and not dry_run:
        logging.error("Use --email to receive notifications, or --dry-run to test.")
        return

    today = datetime.now().date()
    # When both None: start = today, end = today + weeks_ahead
    # When end None: use start + weeks_ahead
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

    country = None
    if country_filter and country_filter.strip():
        country = country_filter.strip().split(",")[0].strip()

    total_watched = sum(len(s) for s in watch_list.values())
    date_range = f"{start.date()}" + (f" to {end.date()}" if end else " onward")
    logging.info(
        "Fetching competitions %s%s (watching %d competitor(s) across %d event(s))",
        date_range,
        f" in {country}" if country else " (all countries)",
        total_watched,
        len(watch_list),
    )

    def _on_page(page: int, total: int) -> None:
        logging.info("  Page %d: %d competition(s) so far", page, total)

    competitions = get_upcoming_competitions(
        start_date=start,
        end_date=end,
        country=country,
        on_progress=_on_page,
    )

    # If country filter has multiple, we'd need to filter client-side.
    if country_filter and "," in country_filter:
        countries = {c.strip().upper() for c in country_filter.split(",")}
        competitions = [c for c in competitions if c.get("country_iso2", "").upper() in countries]

    total = len(competitions)
    logging.info("Found %d competition(s) to check", total)
    if total == 0:
        logging.info("No competitions found.")
        return

    results = []
    start_time = time.perf_counter()
    for i, comp in enumerate(competitions, 1):
        comp_id = comp["id"]
        comp_name = comp.get("name", comp_id)
        comp_url = comp.get("url", f"https://www.worldcubeassociation.org/competitions/{comp_id}")
        start_date = comp.get("start_date", "")
        end_date = comp.get("end_date", "")
        date_str = start_date if start_date == end_date else f"{start_date} – {end_date}"

        elapsed = time.perf_counter() - start_time
        avg_per_comp = elapsed / i if i > 0 else 0
        remaining = (total - i) * avg_per_comp if avg_per_comp > 0 else 0
        eta_str = _format_eta(remaining) if remaining > 0 else ""

        logging.info("[%d/%d] Checking %s%s", i, total, comp_name, f" (ETA: {eta_str})" if eta_str else "")

        try:
            wcif = get_competition_wcif(
                comp_id,
                delay_before_s=rate_limit_delay_s,
                name_for_logging=comp_name,
            )
        except Exception as e:
            logging.warning("  Skip %s: %s", comp_id, e)
            continue

        matches = get_watched_competitors_in_psych_sheet(wcif, watch_list)

        if matches:
            watched_events = set()
            for eids in watch_list.values():
                watched_events.update(eids)
            # Enrich each match with schedule (if available)
            for m in matches:
                watched_here = set(m.get("events", []))
                schedule = get_competitor_schedule(
                    wcif, m, watched_here, target_tz=timezone, include_tz_in_times=False
                )
                m["schedule"] = schedule
            logging.info("  ✓ %s: %d watched competitor(s)", comp_name, len(matches))
            results.append({
                "comp": comp,
                "comp_name": comp_name,
                "comp_id": comp_id,
                "comp_url": comp_url,
                "date_str": date_str,
                "watched_competitors": matches,
            })

    elapsed_total = time.perf_counter() - start_time
    logging.info("Checked %d competition(s) in %s", total, _format_eta(elapsed_total))

    if not results:
        logging.info("No competitions with watched competitors found.")
        return

    # Build email
    html_body = format_results_by_week(results, timezone=timezone)
    subject = f"WCA: {len(results)} competition(s) with watched competitors"

    if dry_run:
        logging.info("--- DRY RUN - Would send ---")
        logging.info("To: %s", notify_email)
        logging.info("Subject: %s", subject)
        logging.info("%s", html_body[:500] + "..." if len(html_body) > 500 else html_body)
        # Dump to txt file (plain text)
        txt_lines = [
            f"To: {notify_email}",
            f"Subject: {subject}",
            "",
            _html_to_plain_text(html_body),
        ]
        out_path = Path("wca_notify_dry_run.txt")
        out_path.write_text("\n".join(txt_lines), encoding="utf-8")
        logging.info("Wrote %s", out_path.resolve())
        return

    send_email(
        to_email=notify_email,
        subject=subject,
        html_body=html_body,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )
    logging.info("Email sent to %s", notify_email)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Notify when watched WCA competitors are registered for upcoming competitions.",
    )
    parser.add_argument(
        "--watch-list", "-w",
        type=Path,
        default=Path("watch_list.json"),
        help="Path to watch_list.json (event_id -> list of WCA IDs)",
    )
    parser.add_argument(
        "--email", "-e",
        help="Email to send notifications to",
    )
    parser.add_argument(
        "--start", "-s",
        help="Start date (YYYY-MM-DD). Default: today. When both --start and --end omitted: today, no end (open-ended).",
    )
    parser.add_argument(
        "--end",
        help="End date (YYYY-MM-DD). Omit for no end date (fetch all from start onward).",
    )
    parser.add_argument(
        "--country", "-c",
        help="Filter by country ISO2 (e.g. US) or comma-separated list",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent, do not send email",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=2,
        help="How many weeks ahead to fetch competitions (default: 2). Used when --end not specified.",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=1.0,
        help="Seconds to wait between WCIF API requests (default: 1.0). Increase if hitting 429.",
    )
    parser.add_argument(
        "--timezone", "-z",
        default="America/Los_Angeles",
        help="Timezone for schedule times (default: America/Los_Angeles = PST). IANA name.",
    )
    parser.add_argument(
        "--smtp-host",
        default="smtp.gmail.com",
        help="SMTP server host",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=587,
        help="SMTP server port",
    )
    parser.add_argument(
        "--smtp-user",
        help="SMTP username",
    )
    parser.add_argument(
        "--smtp-password",
        help="SMTP password (use App Password for Gmail)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    run(
        watch_list_path=args.watch_list,
        notify_email=args.email,
        start_date=parse_date(args.start) if args.start else None,
        end_date=parse_date(args.end) if args.end else None,
        country_filter=args.country,
        weeks_ahead=args.weeks,
        rate_limit_delay_s=args.rate_limit_delay,
        timezone=args.timezone,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_password=args.smtp_password,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
