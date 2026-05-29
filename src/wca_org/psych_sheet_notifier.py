#!/usr/bin/env python3
"""WCA Psych Sheet Notifier.

Runs weekly to check upcoming competitions for competitors on your watch list
and emails you when any are competing.

Usage:
    uv run wca notify --watch-list watch_list.yaml --email you@example.com
"""

import argparse
import html
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .cubing_china_api import (
    cubing_matches_for_watch_list,
    list_cubing_comps_overlapping_window,
)
from .notify import format_psych_sheet_email, send_email
from .watch_list import (
    WatchEventConfig,
    flat_per_event_watches,
    load_watch_list_document,
)
from .wca_api import (
    get_competition_wcif,
    get_competitor_schedule,
    get_person_display_name,
    get_upcoming_competitions,
    get_watched_competitors_in_psych_sheet,
)


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


def _process_wca_competition(
    comp: dict[str, Any],
    i: int,
    total: int,
    *,
    watch_list: dict[str, set[str]],
    cubing_covered_wca_ids: set[str],
    rate_limit_delay_s: float,
    timezone: str,
    start_time: float,
) -> dict[str, Any] | None:
    """Check one WCA competition; return a result item or ``None``.

    Returns ``None`` when the comp is covered by cubing.com, the WCIF
    fetch fails, or no watched competitor is registered.
    """
    comp_id = comp["id"]
    if comp_id in cubing_covered_wca_ids:
        logging.info("  Skip %s (covered by cubing.com)", comp_id)
        return None
    comp_name = comp.get("name", comp_id)
    comp_url = comp.get(
        "url",
        f"https://www.worldcubeassociation.org/competitions/{comp_id}",
    )
    comp_start = comp.get("start_date", "")
    comp_end = comp.get("end_date", "")
    date_str = (
        comp_start if comp_start == comp_end else f"{comp_start} - {comp_end}"
    )

    elapsed = time.perf_counter() - start_time
    avg_per_comp = elapsed / i if i > 0 else 0
    remaining = (total - i) * avg_per_comp if avg_per_comp > 0 else 0
    eta_str = _format_eta(remaining) if remaining > 0 else ""

    logging.info(
        "[%d/%d] Checking %s%s",
        i,
        total,
        comp_name,
        f" (ETA: {eta_str})" if eta_str else "",
    )

    try:
        wcif = get_competition_wcif(
            comp_id,
            delay_before_s=rate_limit_delay_s,
            name_for_logging=comp_name,
        )
    except Exception as e:
        logging.warning("  Skip %s: %s", comp_id, e)
        return None

    matches = get_watched_competitors_in_psych_sheet(wcif, watch_list)
    if not matches:
        return None

    for m in matches:
        watched_here = set(m.get("events", []))
        m["schedule"] = get_competitor_schedule(
            wcif,
            m,
            watched_here,
            target_tz=timezone,
            include_tz_in_times=False,
        )
    logging.info("  ✓ %s: %d watched competitor(s)", comp_name, len(matches))
    return {
        "comp": comp,
        "comp_name": comp_name,
        "comp_id": comp_id,
        "comp_url": comp_url,
        "date_str": date_str,
        "watched_competitors": matches,
    }


def _process_cubing_competition(
    alias: str,
    norm: dict[str, Any],
    *,
    watch_list: dict[str, set[str]],
    rate_limit_delay_s: float,
) -> dict[str, Any] | None:
    """Check one cubing.com competition; return a result item or ``None``.

    Returns ``None`` when the fetch fails or no watched competitor is
    registered.
    """
    try:
        matches = cubing_matches_for_watch_list(
            alias,
            watch_list,
            rate_limit_delay_s=rate_limit_delay_s,
        )
    except Exception as e:
        logging.warning("  Skip cubing %s: %s", alias, e)
        return None
    if not matches:
        return None
    comp_name = norm.get("name", alias)
    logging.info(
        "  ✓ cubing %s: %d watched competitor(s)",
        comp_name,
        len(matches),
    )
    for m in matches:
        m["schedule"] = []
        # cubing.com gives the local-script name; prefer WCA Latin name.
        latin = get_person_display_name(m.get("wcaId", ""))
        if latin:
            m["name"] = latin
    comp_start = norm.get("start_date", "")
    comp_end = norm.get("end_date", "")
    date_str = (
        comp_start if comp_start == comp_end else f"{comp_start} - {comp_end}"
    )
    wca_back_id = norm.get("id", f"cubing:{alias}")
    return {
        "comp": norm,
        "comp_name": comp_name,
        "comp_id": wca_back_id,
        "comp_url": norm.get("url", f"https://cubing.com/competition/{alias}"),
        "date_str": date_str,
        "watched_competitors": matches,
    }


def _fetch_wca_competitions(
    start: datetime,
    end: datetime,
    country_filter: str | None,
) -> list[dict[str, Any]]:
    """Fetch upcoming WCA comps for the window, applying country filter."""
    country = None
    if country_filter and country_filter.strip():
        country = country_filter.strip().split(",")[0].strip()

    def _on_page(page: int, total: int) -> None:
        logging.info("  Page %d: %d competition(s) so far", page, total)

    competitions = get_upcoming_competitions(
        start_date=start,
        end_date=end,
        country=country,
        on_progress=_on_page,
    )

    if country_filter and "," in country_filter:
        countries = {c.strip().upper() for c in country_filter.split(",")}
        competitions = [
            c
            for c in competitions
            if c.get("country_iso2", "").upper() in countries
        ]
    return competitions


def _prefetch_cubing_comps(
    start: datetime,
    end: datetime,
    rate_limit_delay_s: float,
) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any]]], set[str]]:
    """Pre-fetch cubing.com comps and the WCA ids they cover.

    These are fetched BEFORE the WCA loop so they take priority: many
    Chinese comps exist on WCA but carry no registrations there
    (competitors register on cubing.com). For any comp present on both
    systems we skip the WCA side and use cubing.com's richer data.
    """
    ws = start.date()
    we = end.date()
    logging.info("Cubing China: scanning comps overlapping %s - %s", ws, we)

    def _cc_log(_i: int, alias: str, cname: str) -> None:
        logging.info("  Cubing: %s (%s)", cname, alias)

    cubing_comps = list_cubing_comps_overlapping_window(
        window_start=ws,
        window_end=we,
        # take everything; we prioritize cubing
        wca_competition_ids_to_skip=set(),
        rate_limit_delay_s=rate_limit_delay_s,
        on_progress=_cc_log,
    )
    cubing_covered_wca_ids: set[str] = set()
    for _alias, detail, _norm in cubing_comps:
        wid = (detail.get("wca_competition_id") or "").strip()
        if wid:
            cubing_covered_wca_ids.add(wid)
    return cubing_comps, cubing_covered_wca_ids


def gather_psych_sheet_results(
    events: dict[str, WatchEventConfig],
    *,
    start: datetime,
    end: datetime,
    country_filter: str | None = None,
    rate_limit_delay_s: float = 1.0,
    timezone: str,
    cubing_china: bool = False,
) -> list[dict[str, Any]]:
    """Match watched competitors across WCA (+ optional cubing.com).

    Covers ``[start.date, end.date]``. ``events`` maps event id →
    ``WatchEventConfig`` (see ``watch_list``). Each result item matches
    the structure expected by ``format_results_by_week`` /
    ``format_psych_sheet_email``.
    """
    watch_list = flat_per_event_watches(events)
    competitions = _fetch_wca_competitions(start, end, country_filter)

    results: list[dict[str, Any]] = []
    start_time = time.perf_counter()

    cubing_comps: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    cubing_covered_wca_ids: set[str] = set()
    if cubing_china:
        cubing_comps, cubing_covered_wca_ids = _prefetch_cubing_comps(
            start, end, rate_limit_delay_s
        )

    total = len(competitions)
    logging.info("Found %d WCA competition(s) to check", total)
    for i, comp in enumerate(competitions, 1):
        item = _process_wca_competition(
            comp,
            i,
            total,
            watch_list=watch_list,
            cubing_covered_wca_ids=cubing_covered_wca_ids,
            rate_limit_delay_s=rate_limit_delay_s,
            timezone=timezone,
            start_time=start_time,
        )
        if item is not None:
            results.append(item)

    if cubing_china:
        for alias, _detail, norm in cubing_comps:
            item = _process_cubing_competition(
                alias,
                norm,
                watch_list=watch_list,
                rate_limit_delay_s=rate_limit_delay_s,
            )
            if item is not None:
                results.append(item)

    elapsed_total = time.perf_counter() - start_time
    logging.info("Psych sheet scan finished in %s", _format_eta(elapsed_total))
    return results


def run(
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
    """Fetch upcoming comps, check psych sheets, and email matches.

    Sends email if any watched competitors are competing.
    """
    cfg, events = load_watch_list_document(watch_list_path)
    if not events or not any(ec.all_watched() for ec in events.values()):
        logging.error(
            "Watch list is empty or file not found. "
            "Use --watch-list path/to/watch_list.yaml",
        )
        return
    if not notify_email and not dry_run:
        logging.error(
            "Use --email to receive notifications, or --dry-run to test."
        )
        return

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
        assert start_date is not None
        assert end_date is not None
        start = start_date
        end = end_date

    tz = timezone or cfg.timezone
    watch_list = flat_per_event_watches(events)
    total_watched = sum(len(s) for s in watch_list.values())
    date_range = f"{start.date()} to {end.date()}"
    country = None
    if country_filter and country_filter.strip():
        country = country_filter.strip().split(",")[0].strip()
    logging.info(
        "Fetching competitions %s%s "
        "(watching %d competitor(s) across %d event(s))",
        date_range,
        f" in {country}" if country else " (all countries)",
        total_watched,
        len(events),
    )

    results = gather_psych_sheet_results(
        events,
        start=start,
        end=end,
        country_filter=country_filter,
        rate_limit_delay_s=rate_limit_delay_s,
        timezone=tz,
        cubing_china=cubing_china,
    )

    if not results:
        logging.info("No competitions with watched competitors found.")
        return

    html_body = format_psych_sheet_email(results, timezone=tz)
    subject = f"WCA: {len(results)} competition(s) with watched competitors"

    if dry_run:
        logging.info("--- DRY RUN - Would send ---")
        logging.info("To: %s", notify_email)
        logging.info("Subject: %s", subject)
        logging.info(
            "%s", html_body[:500] + "..." if len(html_body) > 500 else html_body
        )
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
        to_email=notify_email or "",
        subject=subject,
        html_body=html_body,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )
    logging.info("Email sent to %s", notify_email)


def add_notify_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach notifier CLI flags to ``parser``.

    Works for the standalone parser or the ``wca notify`` subparser.
    """
    parser.add_argument(
        "--watch-list",
        "-w",
        type=Path,
        default=Path("watch_list.yaml"),
        help="Path to watch list YAML (extension .yaml/.yml) or legacy .json",
    )
    parser.add_argument(
        "--email",
        "-e",
        help="Email to send notifications to",
    )
    parser.add_argument(
        "--start",
        "-s",
        help=(
            "Start date YYYY-MM-DD (default: today). End date defaults "
            "to start + --weeks when --end omitted."
        ),
    )
    parser.add_argument(
        "--end",
        help=(
            "End date YYYY-MM-DD for competitions listing. If omitted "
            "with --start, end = start + --weeks; if both --start and "
            "--end omitted, end = today + --weeks."
        ),
    )
    parser.add_argument(
        "--country",
        "-c",
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
        default=8,
        help="Weeks ahead from start when --end omitted (default: 8)",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=1.0,
        help=(
            "Seconds to wait between WCIF API requests (default: 1.0). "
            "Increase if hitting 429."
        ),
    )
    parser.add_argument(
        "--timezone",
        "-z",
        default=None,
        help=(
            "Timezone for schedule times (default: watch list config timezone)"
        ),
    )
    parser.add_argument(
        "--cubing-china",
        action="store_true",
        help="Also scan cubing.com WCA competitions (China registrations)",
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


def run_notify_from_args(args: argparse.Namespace) -> None:
    """Run the notifier using flags from a parsed ``args`` namespace."""
    run(
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


def main() -> None:
    """Parse CLI args and run the standalone psych sheet notifier."""
    parser = argparse.ArgumentParser(
        description=(
            "Notify when watched WCA competitors are registered "
            "for upcoming competitions."
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
