"""WCA API client for competitions and psych sheet (WCIF) data.

Uses the public WCA v0 API - no authentication required.
- Competitions: https://www.worldcubeassociation.org/api/v0/competitions
- WCIF (registrations): /api/v0/competitions/{id}/wcif/public
"""

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests  # type: ignore[import-untyped]

BASE_URL = "https://www.worldcubeassociation.org/api/v0"

# WCA API returns 25 competitions per page
COMPETITIONS_PAGE_SIZE = 25


def _get_with_429_retry(
    url: str,
    params: dict[str, Any] | None = None,
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


def _smoke_check_competition(
    c: dict[str, Any], source: str = "WCA API"
) -> None:
    """Log a warning if expected competition fields are absent."""
    expected = ("id", "name", "start_date", "end_date", "url")
    missing = [k for k in expected if c.get(k) is None]
    if missing:
        logging.warning(
            "[smoke] %s competition missing fields %s; got keys %s",
            source,
            missing,
            sorted(c.keys()),
        )


def _smoke_check_wcif(wcif: dict[str, Any], label: str) -> None:
    """Log warnings if WCIF is missing expected top-level/person fields."""
    expected = ("persons", "schedule", "events")
    missing = [k for k in expected if k not in wcif]
    if missing:
        logging.warning(
            "[smoke] WCIF %s missing sections: %s; got keys: %s",
            label,
            missing,
            sorted(wcif.keys()),
        )
        return
    persons = wcif.get("persons") or []
    if persons:
        p = persons[0]
        p_expected = (
            "wcaId",
            "name",
            "registration",
            "personalBests",
            "assignments",
        )
        p_missing = [k for k in p_expected if k not in p]
        if p_missing:
            logging.warning(
                "[smoke] WCIF %s person missing fields: %s; got keys: %s",
                label,
                p_missing,
                sorted(p.keys()),
            )


def _upcoming_competitions_params(
    start_date: datetime | None,
    end_date: datetime | None,
    country: str | None,
) -> dict[str, Any]:
    """Build query params for the upcoming-competitions endpoint."""
    params: dict[str, Any] = {"sort": "start_date"}
    if start_date:
        params["start"] = start_date.strftime("%Y-%m-%d")
    if end_date:
        params["end"] = end_date.strftime("%Y-%m-%d")
    if country:
        params["country_iso2"] = country
    return params


def get_upcoming_competitions(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    country: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch upcoming competitions from the WCA API.

    Paginates until all pages are fetched.

    Args:
        start_date: Competitions starting on or after this date
        end_date: Competitions starting on or before this date
        country: Filter by country ISO2 code (e.g. 'US')
        on_progress: Optional callback(page_num, total_so_far) after each page
        max_results: Stop once at least this many non-cancelled
            competitions are collected. Useful with API
            ``sort=start_date`` (default) when only the earliest comps
            are needed. Omit to fetch every page.

    Returns:
        List of competition dicts with id, name, start_date, etc.
    """
    params = _upcoming_competitions_params(start_date, end_date, country)

    all_competitions: list[dict[str, Any]] = []
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
        all_competitions.extend(
            c for c in competitions if not c.get("cancelled_at")
        )
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


def _ended_in_window_id(
    c: dict[str, Any],
    *,
    seen: set[str],
    cutoff: date,
    today_d: date,
) -> str | None:
    """Return the competition id if it ended within the window, else None.

    Skips cancelled comps, already-seen ids, and unparseable dates.
    """
    if c.get("cancelled_at"):
        return None
    cid: str | None = c.get("id")
    if not cid or cid in seen:
        return None
    end_s = c.get("end_date") or c.get("start_date")
    if not end_s:
        return None
    try:
        end_d = datetime.strptime(end_s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if cutoff <= end_d <= today_d:
        return cid
    return None


def get_competitions_ended_within_days(
    *,
    days: int = 7,
    lookback_start_days: int = 45,
    country: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Competitions whose ``end_date`` is within the last ``days`` days.

    Inclusive of ``today``. The WCA competitions API filters by
    competition **start** date, so we fetch a window of start dates from
    ``today - lookback_start_days`` through ``today`` and filter
    client-side.
    """
    today_d = today or date.today()
    cutoff = today_d - timedelta(days=days - 1) if days > 0 else today_d
    start_fetch = datetime(
        today_d.year, today_d.month, today_d.day
    ) - timedelta(
        days=lookback_start_days,
    )
    end_fetch = datetime(today_d.year, today_d.month, today_d.day)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    page = 1
    params_base: dict[str, Any] = {"sort": "start_date"}
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
            cid = _ended_in_window_id(
                c, seen=seen, cutoff=cutoff, today_d=today_d
            )
            if cid is not None:
                seen.add(cid)
                out.append(c)
        if on_progress:
            on_progress(page, len(out))
        if len(competitions) < COMPETITIONS_PAGE_SIZE:
            break
        page += 1
        time.sleep(0.5)

    return out


def get_person_results(wca_id: str) -> list[dict[str, Any]]:
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


def get_competition_results(competition_id: str) -> list[dict[str, Any]]:
    """All published results for one competition.

    Same row shape as ``get_person_results``.
    """
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
    """Map country ISO2 to continental record code (NAR, ER, ...).

    Built from the WCA ``/countries`` endpoint.
    """
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


def fetch_person_bundle(wca_id: str) -> dict[str, Any]:
    """JSON object from ``GET /persons/{id}``.

    Includes top-level ``personal_records``.
    """
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


_PERSON_NAME_CACHE: dict[str, str] = {}


def get_person_display_name(wca_id: str) -> str | None:
    """Latin display name for a WCA ID.

    E.g. ``Yiheng Wang`` from the WCA-stored ``Yiheng Wang (王艺衡)``.
    Returns None if unknown. Cached per process.
    """
    wid = (wca_id or "").strip().upper()
    if not wid:
        return None
    if wid in _PERSON_NAME_CACHE:
        return _PERSON_NAME_CACHE[wid] or None
    try:
        bundle = fetch_person_bundle(wid)
    except Exception:
        bundle = {}
    person = bundle.get("person") or {}
    raw = (person.get("name") or "").strip()
    # WCA stores non-Latin names as "Latin Name (本地名)"; keep the Latin part.
    latin = raw.split("(", 1)[0].strip() if raw else ""
    _PERSON_NAME_CACHE[wid] = latin
    return latin or None


def get_competition_wcif(
    competition_id: str,
    *,
    delay_before_s: float = 1.0,
    retry_on_429: bool = True,
    name_for_logging: str | None = None,
) -> dict[str, Any]:
    """Fetch WCIF (Competition Interchange Format) for a competition.

    Contains registrations, psych sheet data, and personal bests with
    world rankings.

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
        if (
            resp.status_code == 429
            and retry_on_429
            and attempt < max_retries - 1
        ):
            backoff = backoffs[attempt]
            logging.info("  Retry after 429: %s (waiting %ds)", label, backoff)
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        wcif: dict[str, Any] = resp.json()
        _smoke_check_wcif(wcif, label)
        return wcif
    # Loop always returns or raises (final 429 falls through to raise above);
    # this guards mypy's missing-return.
    msg = f"Failed to fetch WCIF for {label}"
    raise RuntimeError(msg)


def personal_best_world_rank(
    person: dict[str, Any],
    event_id: str,
    *,
    prefer: tuple[str, ...] = ("single", "average"),
) -> tuple[int, str] | None:
    """Best (minimum) worldRanking for ``event_id`` from personalBests.

    Prefers PB type order in ``prefer`` (e.g. single before average).
    Rows without a usable positive ``worldRanking`` are skipped.

    Returns:
        (world_ranking, pb_type_used) or None if unranked / no PB for event.
    """
    pbs = person.get("personalBests") or []
    preferred = tuple(prefer)

    # Prefer earlier types in ``prefer``: use single PB if ranked, else
    # fall back to average, etc.
    for pb_type in preferred:
        type_best: int | None = None
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
    person: dict[str, Any],
    event_ids: list[str],
    *,
    max_rank: int,
) -> list[dict[str, Any]]:
    """Return events where the person's world rank is within max_rank.

    Considers the subset of ``event_ids``; keeps those whose world rank
    is ``<= max_rank``. Each item: ``{"event_id", "rank", "pb_type"}``.
    Sorted by rank ascending, then event_id.
    """
    out: list[dict[str, Any]] = []
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


def get_competing_registrants(wcif: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract registered competitors who are actually competing from WCIF.

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
    wcif: dict[str, Any],
    watch_list: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Find watch-listed competitors in the psych sheet.

    Matches competitors who are on the watch list for an event they're
    competing in.

    Args:
        wcif: WCIF data from get_competition_wcif
        watch_list: Dict mapping event_id -> set of WCA IDs to watch.
                    e.g. {"333": {"2010LEAR01", "2017PARK03"},
                          "444": {"2010LEAR01"}}

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
        watched_events = [
            eid for eid in person_events if wca_id in watch_list.get(eid, set())
        ]
        if watched_events:
            entry = {**p, "events": sorted(watched_events)}
            matches.append(entry)

    return matches


def _collect_activities(
    schedule: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build activityId and activityCode lookup maps for activities."""
    by_id: dict[int, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}

    def add_activity(a: dict[str, Any]) -> None:
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


def _format_activity_time(iso: str | None, tz: ZoneInfo, time_fmt: str) -> str:
    """Format an ISO 8601 time string in ``tz``; empty if missing.

    Returns the raw input on parse failure, matching prior behavior.
    """
    if not iso:
        return ""
    try:
        # ISO format may have Z or +00:00 for UTC
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(tz).strftime(time_fmt)
    except (ValueError, TypeError):
        return iso


def _round_label(round_part: str) -> str:
    """Human label for a round code part: ``f`` -> Final, ``r1`` -> Round 1."""
    if round_part == "f":
        return "Final"
    if round_part.startswith("r") and round_part[1:].isdigit():
        return f"Round {round_part[1:]}"
    return round_part or ""


def _schedule_row_for_assignment(
    assign: dict[str, Any],
    *,
    activities_by_id: dict[int, dict[str, Any]],
    activities_by_code: dict[str, dict[str, Any]],
    watched_event_ids: set[str],
    tz: ZoneInfo,
    time_fmt: str,
) -> dict[str, Any] | None:
    """Build a schedule row for one competitor assignment, or None to skip."""
    if assign.get("assignmentCode") != "competitor":
        return None
    act = activities_by_id.get(assign.get("activityId") or -1)
    if not act:
        return None
    code = act.get("activityCode") or ""
    # activityCode: "333-r1", "444-f", "333-r1-g3" - extract eventId
    # (prefix before -r or -f).
    event_id = code.split("-")[0] if "-" in code else code
    if event_id not in watched_event_ids:
        return None

    # Use round-level time (not group): groups have auto-calculated times
    # with odd minutes.
    parts = code.split("-")
    round_code = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else code
    round_act = activities_by_code.get(round_code)
    if round_act:
        act = round_act

    start_local = _format_activity_time(act.get("startTime"), tz, time_fmt)
    end_local = _format_activity_time(act.get("endTime"), tz, time_fmt)

    # Round label: "3x3 Round 1", "4x4 Final" - no group info
    round_part = parts[1] if len(parts) >= 2 else ""
    return {
        "event_id": event_id,
        "activity_code": round_code,
        "name": _round_label(round_part),  # notify adds event name
        "start_local": start_local,
        "end_local": end_local,
    }


def get_competitor_schedule(
    wcif: dict[str, Any],
    person: dict[str, Any],
    watched_event_ids: set[str],
    *,
    target_tz: str = "America/Los_Angeles",
    include_tz_in_times: bool = False,
) -> list[dict[str, Any]]:
    """Get a competitor's watched-event schedule from WCIF assignments.

    Args:
        wcif: WCIF data
        person: Person dict (must have assignments)
        watched_event_ids: Event IDs we care about (e.g. from watch list)
        target_tz: IANA timezone for output (e.g. America/Los_Angeles
            for PST)
        include_tz_in_times: If False, times omit timezone (use global
            note instead)

    Returns:
        List of {event_id, activity_code, name, start_local, end_local}
        for competitor assignments in watched events. Times as formatted
        strings in target_tz.
    """
    schedule = wcif.get("schedule") or {}
    activities_by_id, activities_by_code = _collect_activities(schedule)
    if not activities_by_id:
        return []

    tz = ZoneInfo(target_tz)
    time_fmt = "%a %b %d, %I:%M %p" + (" %Z" if include_tz_in_times else "")

    out: list[dict[str, Any]] = []
    for assign in person.get("assignments", []):
        row = _schedule_row_for_assignment(
            assign,
            activities_by_id=activities_by_id,
            activities_by_code=activities_by_code,
            watched_event_ids=watched_event_ids,
            tz=tz,
            time_fmt=time_fmt,
        )
        if row is not None:
            out.append(row)

    # Deduplicate by (event_id, round) - one entry per round
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for x in out:
        key = (x["event_id"], x["activity_code"])
        if key not in seen:
            seen.add(key)
            unique.append(x)

    def _sort_key(x: dict[str, Any]) -> str:
        return str(x.get("end_local") or x.get("start_local") or "")

    unique.sort(key=_sort_key)
    return unique
