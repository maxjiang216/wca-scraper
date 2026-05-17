"""
WCA API client for competitions and psych sheet (WCIF) data.

Uses the public WCA v0 API - no authentication required.
- Competitions: https://www.worldcubeassociation.org/api/v0/competitions
- WCIF (registrations): /api/v0/competitions/{id}/wcif/public
"""

import logging
import time
import requests
from datetime import date, datetime, timedelta
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


def _smoke_check_competition(c: dict, source: str = "WCA API") -> None:
    """Log a warning if expected competition fields are absent."""
    expected = ("id", "name", "start_date", "end_date", "url")
    missing = [k for k in expected if c.get(k) is None]
    if missing:
        logging.warning(
            "[smoke] %s competition missing fields %s; got keys %s",
            source, missing, sorted(c.keys()),
        )


def _smoke_check_wcif(wcif: dict, label: str) -> None:
    """Log warnings if WCIF is missing expected top-level or person-level fields."""
    expected = ("persons", "schedule", "events")
    missing = [k for k in expected if k not in wcif]
    if missing:
        logging.warning("[smoke] WCIF %s missing sections: %s; got keys: %s", label, missing, sorted(wcif.keys()))
        return
    persons = wcif.get("persons") or []
    if persons:
        p = persons[0]
        p_expected = ("wcaId", "name", "registration", "personalBests", "assignments")
        p_missing = [k for k in p_expected if k not in p]
        if p_missing:
            logging.warning(
                "[smoke] WCIF %s person missing fields: %s; got keys: %s",
                label, p_missing, sorted(p.keys()),
            )


def get_upcoming_competitions(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    country: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    max_results: Optional[int] = None,
) -> list[dict]:
    """
    Fetch upcoming competitions from the WCA API. Paginates until all pages are fetched.

    Args:
        start_date: Competitions starting on or after this date
        end_date: Competitions starting on or before this date
        country: Filter by country ISO2 code (e.g. 'US')
        on_progress: Optional callback(page_num, total_so_far) after each page
        max_results: Stop once at least this many non-cancelled competitions are collected.
            Useful with API ``sort=start_date`` (default) when only the earliest comps are needed.
            Omit to fetch every page.

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
        if max_results is not None and len(all_competitions) > max_results:
            del all_competitions[max_results:]
        if on_progress:
            on_progress(page, len(all_competitions))
        if max_results is not None and len(all_competitions) >= max_results:
            break
        if len(competitions) < COMPETITIONS_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)  # Avoid 429 when fetching multiple pages

    if all_competitions:
        _smoke_check_competition(all_competitions[0])
    return all_competitions


def get_competitions_ended_within_days(
    *,
    days: int = 7,
    lookback_start_days: int = 45,
    country: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    today: Optional[date] = None,
) -> list[dict]:
    """
    Competitions whose ``end_date`` falls in the last ``days`` days (inclusive of ``today``).

    The WCA competitions API filters by competition **start** date, so we fetch a window of
    start dates from ``today - lookback_start_days`` through ``today`` and filter client-side.
    """
    today_d = today or date.today()
    cutoff = today_d - timedelta(days=days - 1) if days > 0 else today_d
    start_fetch = datetime(today_d.year, today_d.month, today_d.day) - timedelta(
        days=lookback_start_days,
    )
    end_fetch = datetime(today_d.year, today_d.month, today_d.day)

    seen: set[str] = set()
    out: list[dict] = []
    page = 1
    params_base: dict = {"sort": "start_date"}
    params_base["start"] = start_fetch.strftime("%Y-%m-%d")
    params_base["end"] = end_fetch.strftime("%Y-%m-%d")
    if country:
        params_base["country_iso2"] = country

    while True:
        params = {**params_base, "page": page}
        resp = _get_with_429_retry(
            f"{BASE_URL}/competitions",
            params=params,
            label=f"competitions (past window) page {page}",
        )
        competitions = resp.json()
        if not competitions:
            break
        for c in competitions:
            if c.get("cancelled_at"):
                continue
            cid = c.get("id")
            if not cid or cid in seen:
                continue
            end_s = c.get("end_date") or c.get("start_date")
            if not end_s:
                continue
            try:
                end_d = datetime.strptime(end_s, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if end_d >= cutoff and end_d <= today_d:
                seen.add(cid)
                out.append(c)
        if on_progress:
            on_progress(page, len(out))
        if len(competitions) < COMPETITIONS_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)

    return out


def get_person_results(wca_id: str) -> list[dict]:
    """All competition results for a WCA person ID (centiseconds; DNF = -1)."""
    url = f"{BASE_URL}/persons/{wca_id}/results"
    max_retries = 3
    backoffs = (5, 10, 20)
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return []
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(backoffs[attempt])
            continue
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    return []


def get_competition_results(competition_id: str) -> list[dict]:
    """All published results for one competition (same shape as ``get_person_results`` rows)."""
    url = f"{BASE_URL}/competitions/{competition_id}/results"
    max_retries = 3
    backoffs = (5, 10, 20)
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=120)
        if resp.status_code == 404:
            return []
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(backoffs[attempt])
            continue
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    return []


_CONTINENT_TO_CR_TAG = {
    "_North America": "NAR",
    "_Europe": "ER",
    "_Asia": "AsR",
    "_Oceania": "OcR",
    "_Africa": "AfR",
    "_South America": "SAR",
}

_ISO2_TO_CR_TAG: dict[str, str] | None = None


def get_iso2_to_continental_record_tag() -> dict[str, str]:
    """Map country ISO2 → continental record code (NAR, ER, …) using WCA /countries."""
    global _ISO2_TO_CR_TAG
    if _ISO2_TO_CR_TAG is not None:
        return _ISO2_TO_CR_TAG
    resp = _get_with_429_retry(f"{BASE_URL}/countries", label="countries list")
    rows = resp.json()
    m: dict[str, str] = {}
    if isinstance(rows, list):
        for c in rows:
            iso2 = (c.get("iso2") or "").strip().upper()
            cont = c.get("continent_id")
            if iso2 and cont in _CONTINENT_TO_CR_TAG:
                m[iso2] = _CONTINENT_TO_CR_TAG[cont]
    _ISO2_TO_CR_TAG = m
    return m


def fetch_person_bundle(wca_id: str) -> dict:
    """JSON object from ``GET /persons/{id}`` (includes top-level ``personal_records``)."""
    url = f"{BASE_URL}/persons/{wca_id}"
    max_retries = 3
    backoffs = (5, 10, 20)
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return {}
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(backoffs[attempt])
            continue
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    return {}


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
        wcif = resp.json()
        _smoke_check_wcif(wcif, label)
        return wcif
    resp.raise_for_status()  # unreachable; raises on final 429


def personal_best_world_rank(
    person: dict,
    event_id: str,
    *,
    prefer: tuple[str, ...] = ("single", "average"),
) -> Optional[tuple[int, str]]:
    """
    Best (minimum) single-event worldRanking from person's personalBests for event_id.

    Prefers PB type order in ``prefer`` (e.g. single before average).
    Rows without a usable positive ``worldRanking`` are skipped.

    Returns:
        (world_ranking, pb_type_used) or None if unranked / no PB for event.
    """
    pbs = person.get("personalBests") or []
    preferred = tuple(prefer)

    # Prefer earlier types in ``prefer``: use single PB if ranked, else fall back to average, etc.
    for pb_type in preferred:
        type_best: Optional[int] = None
        for pb in pbs:
            if pb.get("eventId") != event_id or pb.get("type") != pb_type:
                continue
            rank = pb.get("worldRanking")
            if rank is None:
                continue
            try:
                r = int(rank)
            except (TypeError, ValueError):
                continue
            if r <= 0:
                continue
            if type_best is None or r < type_best:
                type_best = r
        if type_best is not None:
            return (type_best, pb_type)

    return None


def get_top_ranked_registration_events(
    person: dict,
    event_ids: list[str],
    *,
    max_rank: int,
) -> list[dict]:
    """
    For a competing person, return events (subset of ``event_ids``) where world rank ≤ max_rank.

    Each item: ``{"event_id", "rank", "pb_type"}``.
    Sorted by rank ascending, then event_id.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for eid in event_ids:
        if eid in seen:
            continue
        pair = personal_best_world_rank(person, eid)
        if pair is None:
            continue
        rank, pb_type = pair
        if rank <= max_rank:
            seen.add(eid)
            out.append({"event_id": eid, "rank": rank, "pb_type": pb_type})
    out.sort(key=lambda x: (x["rank"], x["event_id"]))
    return out


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
