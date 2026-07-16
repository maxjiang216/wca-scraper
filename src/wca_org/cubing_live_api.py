"""cubing.com live results via the WebSocket feed (``wss://cubing.com/ws``).

Chinese competitions publish structured, per-attempt results on cubing.com's
live page days before the official WCA REST upload. cubing.com exposes **no**
results REST endpoint (``/results`` etc. 404), but the live page is a Vue SPA
backed by a raw-JSON WebSocket. We connect read-only and pull the same data
the page renders.

Protocol (read path only)::

    connect wss://cubing.com/ws
    send {"type": "competition", "competitionId": <internal id>}
    send {"type": "result", "action": "fetch",
          "params": {"event": "333", "round": "1", "filter": "all"}}
    recv {"code": 200, "type": "users",       "data": {<num>: {...}}}
    recv {"code": 200, "type": "result.all",  "data": [<rows>]}

The internal numeric competition id and the per-event round structure are
embedded in the live page HTML (``data-c`` / ``data-events`` on
``#live-container``). Result rows carry best/average/attempts in
**centiseconds** (identical units to the WCA API), a competitor ``number``
that joins to the ``users`` roster (which carries the WCA Latin name and WCA
id), and ``sr``/``ar`` regional-record tags. Rows are normalized to the same
shape as :func:`wca_org.wca_api.get_competition_results` so they flow through
the existing daily-records matching and dedup unchanged.

Only **finished** rounds are read: in-progress rows mutate as attempts are
entered, and the records dedup keys on the result id, so reading a partial
row would mark it seen before its average completes.
"""

from __future__ import annotations

import contextlib
import html
import json
import logging
import re
import time
from datetime import date
from typing import Any

import requests  # type: ignore[import-untyped]
from websocket import (  # type: ignore[import-not-found]
    create_connection,
)

from .cubing_china_api import (
    _HEADERS,
    _overlap,
    _ts_to_date,
    get_competition_detail,
    list_wca_competitions,
    normalize_event_id,
)

LIVE_WS_URL = "wss://cubing.com/ws"
_LIVE_PAGE_URL = "https://cubing.com/live/{alias}"
_WS_ORIGIN = "https://cubing.com"
_WS_HEADER = [f"User-Agent: {_HEADERS['User-Agent']}"]

# cubing.com round status: index into ``allStatus`` (Open / finished / live).
_STATUS_FINISHED = 1

# cubing.com user "region" label -> ISO2 (display only; matching uses tags).
_REGION_TO_ISO2 = {"China": "CN"}

_DATA_C_RE = re.compile(r'id="live-container"[^>]*\bdata-c="(\d+)"')
_DATA_EVENTS_RE = re.compile(r'\bdata-events="([^"]*)"')


def _fetch_live_structure(
    alias: str,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Return ``(internal_comp_id, events)`` parsed from the live page HTML.

    ``events`` mirrors the page's ``data-events``: a list of
    ``{"i": event_id, "rs": [{"i": round_id, "s": status, "rn": count}, ...]}``.
    """
    url = _LIVE_PAGE_URL.format(alias=alias)
    resp = requests.get(
        url, headers=_HEADERS, cookies={"CubingRateLimit": "1"}, timeout=60
    )
    resp.raise_for_status()
    page = resp.text

    cid: int | None = None
    m_c = _DATA_C_RE.search(page)
    if m_c:
        cid = int(m_c.group(1))

    events: list[dict[str, Any]] = []
    m_e = _DATA_EVENTS_RE.search(page)
    if m_e:
        try:
            parsed = json.loads(html.unescape(m_e.group(1)))
            if isinstance(parsed, list):
                events = parsed
        except (ValueError, TypeError):
            logging.warning("[smoke] cubing live %s: bad data-events", alias)

    if cid is None or not events:
        logging.warning(
            "[smoke] cubing live %s: missing data-c (%s) or data-events (%d)",
            alias,
            cid,
            len(events),
        )
    return cid, events


def _finished_rounds(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Return ``(event_id, round_id)`` pairs for finished, non-empty rounds."""
    out: list[tuple[str, str]] = []
    for ev in events:
        eid = normalize_event_id(ev.get("i"))
        if not eid:
            continue
        for rnd in ev.get("rs") or []:
            rid = str(rnd.get("i") or "").strip()
            status = rnd.get("s")
            count = rnd.get("rn") or 0
            if rid and status == _STATUS_FINISHED and count:
                out.append((eid, rid))
    return out


def _fetch_round(
    ws: Any, *, event_id: str, round_id: str, recv_budget: int = 16
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch one round over an open WebSocket; return ``(users, rows)``.

    ``users`` may be empty if the roster was already delivered earlier on the
    connection; the caller carries it forward.
    """
    ws.send(
        json.dumps(
            {
                "type": "result",
                "action": "fetch",
                "params": {
                    "event": event_id,
                    "round": round_id,
                    "filter": "all",
                },
            }
        )
    )
    users: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for _ in range(recv_budget):
        try:
            raw = ws.recv()
        except Exception as e:  # network error; treat as end of data
            logging.warning("cubing live recv %s/%s: %s", event_id, round_id, e)
            break
        if raw == "pong" or not raw:
            continue
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        mtype = msg.get("type") or ""
        data = msg.get("data")
        if mtype == "users" and isinstance(data, dict):
            users = data
        elif mtype.startswith("result") and isinstance(data, list):
            rows = data
            break
    return users, rows


def _latin_name(full: str, wca_id: str) -> str:
    """``"Yiheng Wang (王艺衡)"`` -> ``"Yiheng Wang"`` (drop local script)."""
    return (full.split(" (", 1)[0].strip() or full.strip()) or wca_id


def _normalize_row(
    row: dict[str, Any],
    users: dict[str, Any],
    *,
    competition_id: str,
) -> dict[str, Any] | None:
    """Map a cubing live result row to the WCA result-row shape."""
    rid = row.get("i")
    if rid is None:
        return None
    user = users.get(str(row.get("n"))) or {}
    wca_id = (user.get("wcaid") or "").strip().upper()
    return {
        "id": rid,
        "wca_id": wca_id,
        "name": _latin_name(user.get("name") or "", wca_id),
        "country_iso2": _REGION_TO_ISO2.get(user.get("region") or ""),
        "event_id": normalize_event_id(row.get("e")),
        "competition_id": competition_id,
        "round_type_id": str(row.get("r") or ""),
        "best": row.get("b"),
        "average": row.get("a"),
        "attempts": list(row.get("v") or []),
        "pos": None,
        "regional_single_record": (row.get("sr") or "").strip().upper(),
        "regional_average_record": (row.get("ar") or "").strip().upper(),
    }


def fetch_competition_live_rows(
    alias: str,
    *,
    competition_id: str,
    rate_limit_delay_s: float = 0.4,
) -> list[dict[str, Any]]:
    """Return WCA-shaped result rows for all finished rounds of a comp.

    Args:
        alias: cubing.com competition alias (e.g. ``Quanzhou-Summer-2026``).
        competition_id: id to stamp on rows (WCA comp id when known, else a
            ``cubing:<alias>`` sentinel).
        rate_limit_delay_s: pause between round fetches.

    Returns:
        Normalized result rows; empty if the page/feed yields nothing.
    """
    cid, events = _fetch_live_structure(alias)
    if cid is None:
        return []
    rounds = _finished_rounds(events)
    if not rounds:
        return []

    rows: list[dict[str, Any]] = []
    try:
        ws = create_connection(
            LIVE_WS_URL, header=_WS_HEADER, origin=_WS_ORIGIN, timeout=20
        )
    except Exception as e:  # network failure
        logging.warning("cubing live %s: connect failed: %s", alias, e)
        return []
    try:
        ws.send(json.dumps({"type": "competition", "competitionId": cid}))
        users: dict[str, Any] = {}
        for event_id, round_id in rounds:
            new_users, raw_rows = _fetch_round(
                ws, event_id=event_id, round_id=round_id
            )
            if new_users:
                users = new_users
            for raw in raw_rows:
                norm = _normalize_row(raw, users, competition_id=competition_id)
                if norm:
                    rows.append(norm)
            time.sleep(rate_limit_delay_s)
    finally:
        with contextlib.suppress(Exception):
            ws.close()
    return rows


def discover_recent_cubing_comps(
    *,
    window_start: date,
    window_end: date,
    rate_limit_delay_s: float = 0.5,
) -> list[dict[str, Any]]:
    """Return cubing comps overlapping the window, with ids for live fetch.

    Each entry is ``{"alias", "wca_competition_id", "competition_id"}`` where
    ``competition_id`` is the WCA id when known (used to dedup against the WCA
    REST scan) or a ``cubing:<alias>`` sentinel otherwise.
    """
    try:
        summaries = list_wca_competitions(year="current")
    except Exception as e:  # network failure
        logging.warning("cubing live: competition list failed: %s", e)
        return []

    out: list[dict[str, Any]] = []
    for s in summaries:
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
        except Exception as e:  # network failure
            logging.warning("  cubing live skip %s (detail): %s", alias, e)
            continue
        wca_id = (detail.get("wca_competition_id") or "").strip()
        out.append(
            {
                "alias": alias,
                "wca_competition_id": wca_id or None,
                "competition_id": wca_id or f"cubing:{alias}",
            }
        )
    return out
