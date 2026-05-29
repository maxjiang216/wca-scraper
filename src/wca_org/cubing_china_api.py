"""Cubing China (cubing.com) public JSON API client.

Listings, competitors, and competition detail.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

import requests  # type: ignore[import-untyped]

BASE_URL = "https://cubing.com/api/v0"

# cubing.com 403s requests that use the default python-requests user-agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _ts_to_date(ts: Any) -> str | None:
    """Unix seconds → YYYY-MM-DD in UTC."""
    if ts is None:
        return None
    try:
        sec = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%d")


def _get_json(
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}" if path.startswith("/") else f"{BASE_URL}/{path}"
    r = requests.get(url, params=params or {}, headers=_HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError(f"cubing.com API: expected object from {path}")
    status = data.get("status")
    if status != 0:
        msg = data.get("message") or data.get("data")
        raise RuntimeError(
            f"cubing.com API error {path}: status={status} message={msg}"
        )
    return data


def _smoke_check_comp_list(rows: list[dict[str, Any]]) -> None:
    """Log warnings if cubing.com competition list schema looks unexpected."""
    if not rows:
        return
    row = rows[0]
    expected = ("alias", "date", "id", "name", "type")
    missing = [k for k in expected if k not in row]
    if missing:
        logging.warning(
            "[smoke] cubing.com comp list missing fields %s; sample keys: %s",
            missing,
            sorted(row.keys()),
        )
    dt = row.get("date") or {}
    if "from" not in dt or "to" not in dt:
        logging.warning(
            "[smoke] cubing.com comp date missing from/to; date keys: %s",
            sorted(dt.keys()) if isinstance(dt, dict) else type(dt).__name__,
        )


def _smoke_check_competitors(rows: list[dict[str, Any]], alias: str) -> None:
    """Log warnings if cubing.com competitor list schema looks unexpected."""
    if not rows:
        return
    row = rows[0]
    expected = ("competitor", "events")
    missing = [k for k in expected if k not in row]
    if missing:
        logging.warning(
            "[smoke] cubing.com %s competitor missing fields %s; "
            "sample keys: %s",
            alias,
            missing,
            sorted(row.keys()),
        )
    comp_info = row.get("competitor") or {}
    if not isinstance(comp_info, dict) or "wcaid" not in comp_info:
        logging.warning(
            "[smoke] cubing.com %s competitor.wcaid missing; "
            "competitor keys: %s",
            alias,
            sorted(comp_info.keys())
            if isinstance(comp_info, dict)
            else type(comp_info).__name__,
        )


def list_wca_competitions(*, year: str = "current") -> list[dict[str, Any]]:
    """List WCA-affiliated competitions from cubing.com (summary rows)."""
    data = _get_json("/competition", params={"year": year, "type": "WCA"})
    rows = data.get("data") or []
    if not isinstance(rows, list):
        logging.warning(
            "[smoke] cubing.com competition list data is not a list: %s",
            type(rows).__name__,
        )
        return []
    _smoke_check_comp_list(rows)
    return rows


def get_competition_detail(alias: str) -> dict[str, Any]:
    """Full competition detail (includes ``wca_competition_id`` when known)."""
    data = _get_json(f"/competition/{alias}")
    detail = data.get("data") or {}
    return detail if isinstance(detail, dict) else {}


def get_competitors(alias: str) -> list[dict[str, Any]]:
    """Registered competitors with WCA IDs and event list."""
    data = _get_json(f"/competition/{alias}/competitors")
    rows = data.get("data") or []
    if not isinstance(rows, list):
        logging.warning(
            "[smoke] cubing.com %s competitors data is not a list: %s",
            alias,
            type(rows).__name__,
        )
        return []
    _smoke_check_competitors(rows, alias)
    return rows


def _overlap(
    comp_start: str | None,
    comp_end: str | None,
    window_start: date,
    window_end: date,
) -> bool:
    if not comp_start or not comp_end:
        return False
    try:
        d0 = datetime.strptime(comp_start, "%Y-%m-%d").date()
        d1 = datetime.strptime(comp_end, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    # overlap if ranges intersect
    return d0 <= window_end and d1 >= window_start


def _english_comp_name(api_name: str | None, alias: str) -> str:
    """Prefer an English name, falling back to the alias.

    cubing.com returns Chinese names with no English variant, so fall back
    to the alias (e.g. ``Quanzhou-Summer-2026`` → ``Quanzhou Summer 2026``)
    when the API name has non-ASCII characters.
    """
    if api_name and api_name.isascii() and api_name.strip():
        return api_name.strip()
    if alias:
        return alias.replace("-", " ").strip()
    return api_name or alias


def normalize_event_id(e: Any) -> str:
    """Cubing API mixes int and str event ids (333 vs '333bf')."""
    if e is None:
        return ""
    return str(e).strip()


def cubing_matches_for_watch_list(
    alias: str,
    watch_list: dict[str, set[str]],
    *,
    rate_limit_delay_s: float = 0.5,
) -> list[dict[str, Any]]:
    """Return synthetic person dicts matching the watch list.

    Output uses the same shape as WCIF matches. The CubingChina competitor
    list does not mark accepted vs pending; all listed registrants are
    treated as competing for notification purposes.
    """
    time.sleep(rate_limit_delay_s)
    rows = get_competitors(alias)
    matches: list[dict[str, Any]] = []
    for row in rows:
        comp_info = row.get("competitor") or {}
        wca_id = (comp_info.get("wcaid") or "").strip().upper()
        if not wca_id:
            continue
        raw_events = row.get("events") or []
        person_events = {
            normalize_event_id(x) for x in raw_events if x is not None
        }
        person_events.discard("")
        watched_here = sorted(
            eid for eid in person_events if wca_id in watch_list.get(eid, set())
        )
        if watched_here:
            matches.append(
                {
                    "name": comp_info.get("name") or wca_id,
                    "wcaId": wca_id,
                    "events": watched_here,
                    "assignments": [],
                }
            )
    return matches


def normalize_cubing_competition_for_notifier(
    alias: str,
    detail: dict[str, Any],
    *,
    wca_comp_id: str | None = None,
) -> dict[str, Any]:
    """Build a WCA-shaped competition dict for HTML formatting."""
    url_path = detail.get("url") or f"/competition/{alias}"
    if isinstance(url_path, str) and url_path.startswith("/"):
        comp_url = f"https://cubing.com{url_path}"
    else:
        comp_url = f"https://cubing.com/competition/{alias}"

    dt = detail.get("date") or {}
    start_d = _ts_to_date(dt.get("from")) or ""
    end_d = _ts_to_date(dt.get("to")) or start_d

    return {
        "id": wca_comp_id or f"cubing:{alias}",
        "name": _english_comp_name(detail.get("name"), alias),
        "start_date": start_d,
        "end_date": end_d,
        "country_iso2": "CN",
        "url": comp_url,
        "_cubing_alias": alias,
        "_source": "cubing.com",
    }


def list_cubing_comps_overlapping_window(
    *,
    window_start: date,
    window_end: date,
    wca_competition_ids_to_skip: set[str],
    rate_limit_delay_s: float = 0.5,
    on_progress: Any | None = None,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Cubing competitions overlapping [window_start, window_end].

    Returns a list of tuples (alias, detail_dict, normalized_comp_dict) for
    comps not skipped. ``wca_competition_ids_to_skip`` contains WCA comp IDs
    already covered on the WCA side (i.e. that yielded watched competitors
    there). It must NOT contain every WCA comp seen: many Chinese comps
    exist on WCA with no registrations (competitors register on cubing.com),
    so skipping by mere existence would drop them.
    """
    try:
        summaries = list_wca_competitions(year="current")
    except Exception as e:
        logging.warning("Cubing China competition list failed: %s", e)
        return []

    out: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for i, s in enumerate(summaries, 1):
        alias = s.get("alias")
        if not alias:
            continue
        dt = s.get("date") or {}
        start_d = _ts_to_date(dt.get("from")) or ""
        end_d = _ts_to_date(dt.get("to")) or start_d
        if not _overlap(start_d, end_d, window_start, window_end):
            continue

        time.sleep(rate_limit_delay_s)
        try:
            detail = get_competition_detail(alias)
        except Exception as e:
            logging.warning("  Skip cubing %s (detail): %s", alias, e)
            continue

        wca_id = (detail.get("wca_competition_id") or "").strip()
        if wca_id and wca_id in wca_competition_ids_to_skip:
            logging.info(
                "  Skip cubing %s (already on WCA as %s)", alias, wca_id
            )
            continue

        norm = normalize_cubing_competition_for_notifier(
            alias,
            detail,
            wca_comp_id=wca_id or None,
        )
        if on_progress:
            on_progress(i, alias, norm.get("name", alias))
        out.append((alias, detail, norm))
    return out
