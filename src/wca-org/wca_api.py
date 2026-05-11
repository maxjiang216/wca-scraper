"""
WCA API client for competitions and psych sheet (WCIF) data.

Uses the public WCA v0 API - no authentication required.
- Competitions: https://www.worldcubeassociation.org/api/v0/competitions
- WCIF (registrations): /api/v0/competitions/{id}/wcif/public
"""

import logging
import time
import requests
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

BASE_URL = "https://www.worldcubeassociation.org/api/v0"

# WCA API returns 25 competitions per page
COMPETITIONS_PAGE_SIZE = 25


def _get_with_429_retry(
    url: str,
    params: Optional[dict] = None,
    *,
    label: str = "request",
) -> requests.Response:
    """GET with retry on 429 Too Many Requests (5s, 10s, 20s backoff)."""
    max_retries = 3
    backoffs = (5, 10, 20)
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 429 and attempt < max_retries - 1:
            backoff = backoffs[attempt]
            logging.info("  Retry after 429: %s (waiting %ds)", label, backoff)
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp  # unreachable


def get_upcoming_competitions(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    country: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """
    Fetch upcoming competitions from the WCA API. Paginates until all pages are fetched.

    Args:
        start_date: Competitions starting on or after this date
        end_date: Competitions starting on or before this date
        country: Filter by country ISO2 code (e.g. 'US')
        on_progress: Optional callback(page_num, total_so_far) after each page

    Returns:
        List of competition dicts with id, name, start_date, etc.
    """
    params = {"sort": "start_date"}
    if start_date:
        params["start"] = start_date.strftime("%Y-%m-%d")
    if end_date:
        params["end"] = end_date.strftime("%Y-%m-%d")
    if country:
        params["country_iso2"] = country

    all_competitions = []
    page = 1
    while True:
        params["page"] = page
        resp = _get_with_429_retry(
            f"{BASE_URL}/competitions",
            params=params,
            label=f"competitions page {page}",
        )
        competitions = resp.json()
        if not competitions:
            break
        for c in competitions:
            if c.get("cancelled_at"):
                continue
            all_competitions.append(c)
        if on_progress:
            on_progress(page, len(all_competitions))
        if len(competitions) < COMPETITIONS_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)  # Avoid 429 when fetching multiple pages

    return all_competitions


def get_competition_wcif(
    competition_id: str,
    *,
    delay_before_s: float = 1.0,
    retry_on_429: bool = True,
    name_for_logging: Optional[str] = None,
) -> dict:
    """
    Fetch WCIF (Competition Interchange Format) for a competition.
    Contains registrations, psych sheet data, personal bests with world rankings.

    Args:
        competition_id: WCA competition ID (e.g. 'DFWWeeknights12026')
        delay_before_s: Seconds to wait before request (rate limit friendly)
        retry_on_429: Retry with exponential backoff on 429 Too Many Requests
        name_for_logging: Competition name for retry log messages

    Returns:
        WCIF dict with persons (each has personalBests with worldRanking)
    """
    time.sleep(delay_before_s)
    url = f"{BASE_URL}/competitions/{competition_id}/wcif/public"
    max_retries = 3 if retry_on_429 else 1
    backoffs = (5, 10, 20)
    label = name_for_logging or competition_id
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429 and retry_on_429 and attempt < max_retries - 1:
            backoff = backoffs[attempt]
            logging.info("  Retry after 429: %s (waiting %ds)", label, backoff)
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()  # unreachable; raises on final 429


def get_competing_registrants(wcif: dict) -> list[dict]:
    """
    Extract registered competitors who are actually competing from WCIF.

    Filters for:
    - registration.status == 'accepted'
    - registration.isCompeting == True
    """
    persons = wcif.get("persons", [])
    competing = []
    for p in persons:
        reg = p.get("registration")
        if not reg:
            continue
        if reg.get("status") == "accepted" and reg.get("isCompeting"):
            competing.append(p)
    return competing


def get_watched_competitors_in_psych_sheet(
    wcif: dict,
    watch_list: dict[str, set[str]],
) -> list[dict]:
    """
    Find competitors in the psych sheet who are in the watch list for an event
    they're competing in.

    Args:
        wcif: WCIF data from get_competition_wcif
        watch_list: Dict mapping event_id -> set of WCA IDs to watch.
                    e.g. {"333": {"2010LEAR01", "2017PARK03"}, "444": {"2010LEAR01"}}

    Returns:
        List of person dicts with added 'events' key: list of event IDs they're
        competing in where they're on the watch list.
    """
    competing = get_competing_registrants(wcif)
    matches = []

    for p in competing:
        wca_id = p.get("wcaId")
        if not wca_id:
            continue
        reg = p.get("registration", {})
        person_events = set(reg.get("eventIds", []))
        watched_events = [eid for eid in person_events if wca_id in watch_list.get(eid, set())]
        if watched_events:
            entry = {**p, "events": sorted(watched_events)}
            matches.append(entry)

    return matches


def _collect_activities(schedule: dict) -> tuple[dict[int, dict], dict[str, dict]]:
    """Build activityId -> activity map and activityCode -> activity map for lookups."""
    by_id: dict[int, dict] = {}
    by_code: dict[str, dict] = {}

    def add_activity(a: dict) -> None:
        aid = a.get("id")
        if aid is not None:
            by_id[aid] = a
        code = a.get("activityCode")
        if code:
            by_code[code] = a
        for child in a.get("childActivities", []):
            add_activity(child)

    for venue in schedule.get("venues", []):
        for room in venue.get("rooms", []):
            for act in room.get("activities", []):
                add_activity(act)

    return by_id, by_code


def get_competitor_schedule(
    wcif: dict,
    person: dict,
    watched_event_ids: set[str],
    *,
    target_tz: str = "America/Los_Angeles",
    include_tz_in_times: bool = False,
) -> list[dict]:
    """
    Get schedule for a competitor's watched events from WCIF assignments.

    Args:
        wcif: WCIF data
        person: Person dict (must have assignments)
        watched_event_ids: Event IDs we care about (e.g. from watch list)
        target_tz: IANA timezone for output (e.g. America/Los_Angeles for PST)
        include_tz_in_times: If False, times omit timezone (use global note instead)

    Returns:
        List of {event_id, activity_code, name, start_local, end_local} for
        competitor assignments in watched events. Times as formatted strings in target_tz.
    """
    schedule = wcif.get("schedule") or {}
    activities_by_id, activities_by_code = _collect_activities(schedule)
    if not activities_by_id:
        return []

    tz = ZoneInfo(target_tz)
    out: list[dict] = []

    for assign in person.get("assignments", []):
        if assign.get("assignmentCode") != "competitor":
            continue
        act = activities_by_id.get(assign.get("activityId") or -1)
        if not act:
            continue
        code = act.get("activityCode") or ""
        # activityCode: "333-r1", "444-f", "333-r1-g3" - extract eventId (prefix before -r or -f)
        if "-" in code:
            event_id = code.split("-")[0]
        else:
            event_id = code
        if event_id not in watched_event_ids:
            continue

        # Use round-level time (not group) - groups have auto-calculated times with odd minutes
        parts = code.split("-")
        round_code = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else code
        round_act = activities_by_code.get(round_code)
        if round_act:
            act = round_act

        start_iso = act.get("startTime")
        end_iso = act.get("endTime")
        start_local = ""
        end_local = ""

        time_fmt = "%a %b %d, %I:%M %p" + (" %Z" if include_tz_in_times else "")
        if start_iso:
            try:
                # ISO format may have Z or +00:00 for UTC
                dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                start_local = dt.astimezone(tz).strftime(time_fmt)
            except (ValueError, TypeError):
                start_local = start_iso

        if end_iso:
            try:
                dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                end_local = dt.astimezone(tz).strftime(time_fmt)
            except (ValueError, TypeError):
                end_local = end_iso

        # Round label: "3x3 Round 1", "4x4 Final" - no group info
        round_part = parts[1] if len(parts) >= 2 else ""
        if round_part == "f":
            round_label = "Final"
        elif round_part.startswith("r") and round_part[1:].isdigit():
            round_label = f"Round {round_part[1:]}"
        else:
            round_label = round_part or ""

        out.append({
            "event_id": event_id,
            "activity_code": round_code,
            "name": round_label,  # "Round 1", "Final" - notify adds event name
            "start_local": start_local,
            "end_local": end_local,
        })

    # Deduplicate by (event_id, round) - one entry per round
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for x in out:
        key = (x["event_id"], x["activity_code"])
        if key not in seen:
            seen.add(key)
            unique.append(x)

    def _sort_key(x: dict) -> str:
        return x.get("end_local") or x.get("start_local", "")

    unique.sort(key=_sort_key)
    return unique
