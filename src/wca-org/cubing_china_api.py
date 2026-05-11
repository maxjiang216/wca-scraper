"""Cubing China (cubing.com) public JSON API — listings, competitors, competition detail."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

BASE_URL = "https://cubing.com/api/v0"

# cubing.com 403s requests that use the default python-requests user-agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _ts_to_date(ts: Any) -> Optional[str]:
    """Unix seconds → YYYY-MM-DD in UTC."""
    if ts is None:
        return None
    try:
        sec = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%d")


def _get_json(path: str, *, params: Optional[dict] = None) -> dict:
    url = f"{BASE_URL}{path}" if path.startswith("/") else f"{BASE_URL}/{path}"
    r = requests.get(url, params=params or {}, headers=_HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError(f"cubing.com API: expected object from {path}")
    status = data.get("status")
    if status != 0:
        msg = data.get("message") or data.get("data")
        raise RuntimeError(f"cubing.com API error {path}: status={status} message={msg}")
    return data


def list_wca_competitions(*, year: str = "current") -> list[dict]:
    """List WCA-affiliated competitions from cubing.com (summary rows)."""
    data = _get_json("/competition", params={"year": year, "type": "WCA"})
    rows = data.get("data") or []
    if not isinstance(rows, list):
        return []
    return rows


def get_competition_detail(alias: str) -> dict:
    """Full competition detail (includes ``wca_competition_id`` when known)."""
    data = _get_json(f"/competition/{alias}")
    detail = data.get("data") or {}
    return detail if isinstance(detail, dict) else {}


def get_competitors(alias: str) -> list[dict]:
    """Registered competitors with WCA IDs and event list."""
    data = _get_json(f"/competition/{alias}/competitors")
    rows = data.get("data") or []
    if not isinstance(rows, list):
        return []
    return rows


def _overlap(
    comp_start: Optional[str],
    comp_end: Optional[str],
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
) -> list[dict]:
    """
    Return synthetic person dicts matching the watch list (same shape as WCIF matches).

    CubingChina competitor list does not mark accepted vs pending; all listed registrants
    are treated as competing for notification purposes.
    """
    time.sleep(rate_limit_delay_s)
    rows = get_competitors(alias)
    matches: list[dict] = []
    for row in rows:
        comp_info = row.get("competitor") or {}
        wca_id = (comp_info.get("wcaid") or "").strip().upper()
        if not wca_id:
            continue
        raw_events = row.get("events") or []
        person_events = {normalize_event_id(x) for x in raw_events if x is not None}
        person_events.discard("")
        watched_here = sorted(
            eid for eid in person_events if wca_id in watch_list.get(eid, set())
        )
        if watched_here:
            matches.append({
                "name": comp_info.get("name") or wca_id,
                "wcaId": wca_id,
                "events": watched_here,
                "assignments": [],
            })
    return matches


def normalize_cubing_competition_for_notifier(
    alias: str,
    detail: dict,
    *,
    wca_comp_id: Optional[str] = None,
) -> dict:
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
        "name": detail.get("name") or alias,
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
    on_progress: Optional[Any] = None,
) -> list[tuple[str, dict, dict]]:
    """
    Cubing competitions overlapping [window_start, window_end].

    Returns list of tuples (alias, detail_dict, normalized_comp_dict) for comps not skipped.
    ``wca_competition_ids_to_skip`` contains WCA IDs already fetched from the WCA API.
    """
    try:
        summaries = list_wca_competitions(year="current")
    except Exception as e:
        logging.warning("Cubing China competition list failed: %s", e)
        return []

    out: list[tuple[str, dict, dict]] = []
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
            logging.info("  Skip cubing %s (already on WCA as %s)", alias, wca_id)
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
