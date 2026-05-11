#!/usr/bin/env python3
"""
List registered competitors with strong world rankings at upcoming WCA competitions.

Fetches competitions from the public WCA API (optionally with no end date), loads
WCIF per competition, and reports people with worldRanking ≤ --max-rank in events
they are registered for.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .psych_sheet_notifier import parse_date
from .wca_api import (
    get_competing_registrants,
    get_competition_wcif,
    get_top_ranked_registration_events,
    get_upcoming_competitions,
)


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _comp_date_key(comp: dict) -> str:
    return comp.get("start_date") or ""


def _person_sort_key(entry: dict) -> tuple[int, str]:
    ranks = [e["rank"] for e in entry["events"]]
    return (min(ranks), entry.get("name") or "")


def _scan_competition(
    wcif: dict,
    *,
    max_rank: int,
) -> list[dict]:
    """Return list of {name, wca_id, events} for top-ranked competing registrants."""
    out: list[dict] = []
    for p in get_competing_registrants(wcif):
        wca_id = p.get("wcaId")
        if not wca_id:
            continue
        reg = p.get("registration", {})
        event_ids = list(reg.get("eventIds", []))
        events = get_top_ranked_registration_events(p, event_ids, max_rank=max_rank)
        if not events:
            continue
        out.append({
            "name": p.get("name", wca_id),
            "wca_id": wca_id,
            "events": events,
        })
    out.sort(key=_person_sort_key)
    return out


def _render_markdown(
    *,
    results: list[dict],
    timezone: str,
    start_label: str,
    end_label: str,
    max_rank: int,
    max_competitions_note: str | None,
) -> str:
    lines: list[str] = [
        "# WCA top competitors report",
        "",
        f"- **Timezone (for start date):** {timezone}",
        f"- **Competitions from:** {start_label}",
        f"- **Competitions to:** {end_label}",
        f"- **World rank threshold:** ≤ {max_rank}",
    ]
    if max_competitions_note:
        lines.append(f"- **Competition cap:** {max_competitions_note}")
    lines.extend(["", "---", ""])

    if not results:
        lines.append("_No competitions in this run had registrants meeting the rank threshold._")
        return "\n".join(lines)

    for block in results:
        comp = block["comp"]
        comp_name = block["comp_name"]
        comp_url = block["comp_url"]
        date_str = block["date_str"]
        country = comp.get("country_iso2", "") or "?"
        top_people: list[dict] = block["top_people"]

        lines.append(f"## {comp_name} ({country})")
        lines.append("")
        lines.append(f"- **Dates:** {date_str}")
        lines.append(f"- **URL:** {comp_url}")
        lines.append("")
        lines.append("### Top registrants")
        lines.append("")
        for person in top_people:
            wca_id = person["wca_id"]
            profile = f"https://www.worldcubeassociation.org/persons/{wca_id}"
            ev_parts = [
                f"`{e['event_id']}` #{e['rank']} ({e['pb_type']})"
                for e in person["events"]
            ]
            lines.append(f"- **[{person['name']}]({profile})** ({wca_id}): {', '.join(ev_parts)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_plain(
    *,
    results: list[dict],
    timezone: str,
    start_label: str,
    end_label: str,
    max_rank: int,
    max_competitions_note: str | None,
) -> str:
    lines: list[str] = [
        "WCA top competitors report",
        f"Timezone (start date): {timezone}",
        f"Competitions from: {start_label}",
        f"Competitions to: {end_label}",
        f"World rank threshold: <= {max_rank}",
    ]
    if max_competitions_note:
        lines.append(f"Competition cap: {max_competitions_note}")
    lines.extend(["", "---", ""])

    if not results:
        lines.append("No competitions in this run had registrants meeting the rank threshold.")
        return "\n".join(lines)

    for block in results:
        comp = block["comp"]
        comp_name = block["comp_name"]
        comp_url = block["comp_url"]
        date_str = block["date_str"]
        country = comp.get("country_iso2", "") or "?"
        top_people: list[dict] = block["top_people"]

        lines.append(f"{comp_name} ({country})")
        lines.append(f"  Dates: {date_str}")
        lines.append(f"  URL: {comp_url}")
        lines.append("  Top registrants:")
        for person in top_people:
            wca_id = person["wca_id"]
            profile = f"https://www.worldcubeassociation.org/persons/{wca_id}"
            ev_parts = [
                f"{e['event_id']} #{e['rank']} ({e['pb_type']})"
                for e in person["events"]
            ]
            lines.append(f"    - {person['name']} ({wca_id}) {profile}")
            lines.append(f"      {', '.join(ev_parts)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run(
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    timezone: str = "America/Los_Angeles",
    max_rank: int = 100,
    max_competitions: int | None = None,
    country_filter: str | None = None,
    rate_limit_delay_s: float = 1.0,
    output_path: Path | None = None,
    output_format: str = "markdown",
) -> None:
    tz = ZoneInfo(timezone)
    local_today = datetime.now(tz).date()
    if start_date is None:
        start = datetime(local_today.year, local_today.month, local_today.day)
    else:
        start = start_date

    start_label = str(start.date())
    end_label = str(end_date.date()) if end_date else "no end (all upcoming per API)"

    max_comp_note = str(max_competitions) if max_competitions is not None else None

    country = None
    if country_filter and country_filter.strip():
        country = country_filter.strip().split(",")[0].strip()

    logging.info(
        "Fetching competitions from %s%s%s",
        start_label,
        f" through {end_label}" if end_date else "",
        f" in {country}" if country else " (all countries)",
    )

    def _on_page(page: int, total_so_far: int) -> None:
        logging.info("  Page %d: %d competition(s) so far", page, total_so_far)

    fetch_limit = None
    if max_competitions is not None and not (country_filter and "," in country_filter):
        fetch_limit = max_competitions

    competitions = get_upcoming_competitions(
        start_date=start,
        end_date=end_date,
        country=country,
        on_progress=_on_page,
        max_results=fetch_limit,
    )

    if country_filter and "," in country_filter:
        countries = {c.strip().upper() for c in country_filter.split(",")}
        competitions = [c for c in competitions if c.get("country_iso2", "").upper() in countries]

    competitions = sorted(competitions, key=_comp_date_key)
    total_available = len(competitions)

    if max_competitions is not None:
        competitions = competitions[:max_competitions]

    total = len(competitions)
    logging.info(
        "Scanning WCIF for %d competition(s)%s",
        total,
        f" (of {total_available} fetched)" if max_competitions is not None else "",
    )
    if total == 0:
        logging.info("No competitions to scan.")
        body = (
            _render_markdown(
                results=[],
                timezone=timezone,
                start_label=start_label,
                end_label=end_label,
                max_rank=max_rank,
                max_competitions_note=max_comp_note,
            )
            if output_format == "markdown"
            else _render_plain(
                results=[],
                timezone=timezone,
                start_label=start_label,
                end_label=end_label,
                max_rank=max_rank,
                max_competitions_note=max_comp_note,
            )
        )
        if output_path:
            output_path.write_text(body, encoding="utf-8")
            logging.info("Wrote %s", output_path.resolve())
        else:
            print(body, end="" if body.endswith("\n") else "")
        return

    results: list[dict] = []
    start_time = time.perf_counter()
    for i, comp in enumerate(competitions, 1):
        comp_id = comp["id"]
        comp_name = comp.get("name", comp_id)
        comp_url = comp.get("url", f"https://www.worldcubeassociation.org/competitions/{comp_id}")
        sd = comp.get("start_date", "")
        ed = comp.get("end_date", "")
        date_str = sd if sd == ed else f"{sd} – {ed}"

        elapsed = time.perf_counter() - start_time
        avg_per = elapsed / i if i > 0 else 0.0
        remaining = (total - i) * avg_per if avg_per > 0 else 0.0
        eta_str = _format_eta(remaining) if remaining > 0 else ""

        logging.info("[%d/%d] %s%s", i, total, comp_name, f" (ETA: {eta_str})" if eta_str else "")

        try:
            wcif = get_competition_wcif(
                comp_id,
                delay_before_s=rate_limit_delay_s,
                name_for_logging=comp_name,
            )
        except Exception as e:
            logging.warning("  Skip %s: %s", comp_id, e)
            continue

        top_people = _scan_competition(wcif, max_rank=max_rank)
        if top_people:
            logging.info("  Found %d top-ranked registrant(s)", len(top_people))
            results.append({
                "comp": comp,
                "comp_name": comp_name,
                "comp_id": comp_id,
                "comp_url": comp_url,
                "date_str": date_str,
                "top_people": top_people,
            })

    elapsed_total = time.perf_counter() - start_time
    logging.info("Finished in %s", _format_eta(elapsed_total))

    if output_format == "markdown":
        body = _render_markdown(
            results=results,
            timezone=timezone,
            start_label=start_label,
            end_label=end_label,
            max_rank=max_rank,
            max_competitions_note=max_comp_note,
        )
    else:
        body = _render_plain(
            results=results,
            timezone=timezone,
            start_label=start_label,
            end_label=end_label,
            max_rank=max_rank,
            max_competitions_note=max_comp_note,
        )

    if output_path:
        output_path.write_text(body, encoding="utf-8")
        logging.info("Wrote %s", output_path.resolve())
    else:
        print(body, end="" if body.endswith("\n") else "")


def add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start", "-s",
        help="Start date YYYY-MM-DD (default: today in --timezone)",
    )
    parser.add_argument(
        "--end",
        help=(
            "End date YYYY-MM-DD for the competitions listing API. "
            "Omitted = no upper bound (all upcoming the API returns; can be slow)."
        ),
    )
    parser.add_argument(
        "--timezone", "-z",
        default="America/Los_Angeles",
        help='IANA timezone for default "today" (default: America/Los_Angeles)',
    )
    parser.add_argument(
        "--max-rank",
        type=int,
        default=100,
        help="Include registrants whose best world ranking in an event is at most this (default: 100)",
    )
    parser.add_argument(
        "--max-competitions",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N competitions (after sort by start date). Useful for testing or CI timeouts.",
    )
    parser.add_argument(
        "--country", "-c",
        help="Filter by country ISO2 (e.g. US) or comma-separated list",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=1.0,
        help="Seconds to wait before each WCIF request (default: 1.0)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Write report to this file instead of stdout",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "plain"),
        default="markdown",
        help="Output format (default: markdown)",
    )


def run_report_from_args(args: argparse.Namespace) -> None:
    start_d = parse_date(args.start) if args.start else None
    end_d = parse_date(args.end) if args.end else None
    run(
        start_date=start_d,
        end_date=end_d,
        timezone=args.timezone,
        max_rank=args.max_rank,
        max_competitions=args.max_competitions,
        country_filter=args.country,
        rate_limit_delay_s=args.rate_limit_delay,
        output_path=args.output,
        output_format=args.format,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report highly world-ranked registrants at upcoming WCA competitions.",
    )
    add_report_arguments(parser)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_report_from_args(args)


if __name__ == "__main__":
    main()
