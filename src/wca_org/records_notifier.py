"""Daily digest: WCA Live + recent competition results (per-event OR rules)."""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

from .results_format import mbld_solved_count
from .watch_list import (
    WatchEventConfig,
    WatchListConfig,
    continental_tags_for_event,
)
from .wca_api import (
    get_competition_results,
    get_competitions_ended_within_days,
    get_iso2_to_continental_record_tag,
)
from .wca_live_api import get_recent_records_raw, normalize_live_record


def load_records_state(path: Path) -> dict[str, Any]:
    """Load deduplication state from disk, returning empty state if absent."""
    if not path.exists():
        return {"seen_live_ids": [], "seen_rest_result_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_live_ids": [], "seen_rest_result_ids": []}
    if not isinstance(data, dict):
        return {"seen_live_ids": [], "seen_rest_result_ids": []}
    data.setdefault("seen_live_ids", [])
    sr = data.get("seen_rest_result_ids")
    if not isinstance(sr, list):
        data["seen_rest_result_ids"] = []
    return data


def save_records_state(path: Path, state: dict[str, Any]) -> None:
    """Write deduplication state to disk as JSON."""
    path.write_text(
        json.dumps(state, indent=0, sort_keys=True), encoding="utf-8"
    )


def _safe_pos_int(val: Any) -> int | None:
    try:
        i = int(val)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _allowed_cr_tags(ec: WatchEventConfig, cfg: WatchListConfig) -> set[str]:
    return continental_tags_for_event(ec, cfg)


def _rest_row_matches_or(
    row: dict[str, Any],
    *,
    ec: WatchEventConfig,
    cfg: WatchListConfig,
) -> bool:
    """Return True if a results row matches any configured OR rule.

    Checks WR, configured continental tags on the row, sub time
    (non-mbf), and MBLD sup points (333mbf).
    """
    eid = row.get("event_id") or ""
    rs = (row.get("regional_single_record") or "").strip().upper()
    ra = (row.get("regional_average_record") or "").strip().upper()
    if rs == "WR" or ra == "WR":
        return True

    allowed = _allowed_cr_tags(ec, cfg)
    if allowed and (rs in allowed or ra in allowed):
        return True

    best = _safe_pos_int(row.get("best"))
    avg = _safe_pos_int(row.get("average"))

    if eid == "333mbf" and ec.mbf_sup_points is not None:
        for raw in (best, avg):
            if raw is None:
                continue
            sc = mbld_solved_count(int(raw))
            if sc is not None and sc >= ec.mbf_sup_points:
                return True
        return False

    if eid != "333mbf":
        if (
            ec.sub_single_cs is not None
            and best is not None
            and best < ec.sub_single_cs
        ):
            return True
        if (
            ec.sub_average_cs is not None
            and avg is not None
            and avg < ec.sub_average_cs
        ):
            return True

    return False


def _live_result_centiseconds(norm: dict[str, Any], typ: str) -> int | None:
    if typ == "average":
        v = norm.get("average")
    else:
        v = norm.get("best")
        if v is None or v == "":
            v = norm.get("attempt_result")
    if v is None or v == "":
        return None
    try:
        i = int(v)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _live_cr_tag_matches(
    norm: dict[str, Any],
    *,
    tag: str,
    allowed: set[str],
    iso2_to_cr: dict[str, str],
) -> bool:
    """Return True if a CR-tagged live row maps to an allowed CR tag."""
    if tag != "CR" or not allowed:
        return False
    iso2 = (norm.get("country_iso2") or "").strip().upper()
    cr_tag = iso2_to_cr.get(iso2)
    return bool(cr_tag and cr_tag in allowed)


def _live_mbld_matches(
    norm: dict[str, Any], typ: str, ec: WatchEventConfig
) -> bool:
    """Return True if a 333mbf live row meets the MBLD sup-points rule."""
    if ec.mbf_sup_points is None:
        return False
    raw = _live_result_centiseconds(norm, typ)
    if raw is None:
        return False
    sc = mbld_solved_count(raw)
    return sc is not None and sc >= ec.mbf_sup_points


def _live_sub_time_matches(
    norm: dict[str, Any], typ: str, ec: WatchEventConfig
) -> bool:
    """Return True if a non-mbf live row is under the sub-time cap."""
    cap = ec.sub_average_cs if typ == "average" else ec.sub_single_cs
    if cap is None:
        return False
    val = _live_result_centiseconds(norm, typ)
    return val is not None and val < cap


def live_row_matches_or(
    norm: dict[str, Any],
    *,
    ec: WatchEventConfig,
    cfg: WatchListConfig,
    iso2_to_cr: dict[str, str],
) -> bool:
    """Return True if a WCA Live row matches any configured OR rule."""
    eid = norm.get("event_id") or ""
    tag = (norm.get("tag") or "").upper()
    typ = (norm.get("type") or "single").lower()
    if typ not in ("single", "average"):
        typ = "single"

    if tag == "WR":
        return True

    allowed = _allowed_cr_tags(ec, cfg)
    if _live_cr_tag_matches(
        norm, tag=tag, allowed=allowed, iso2_to_cr=iso2_to_cr
    ):
        return True

    if eid == "333mbf":
        return _live_mbld_matches(norm, typ, ec)

    return _live_sub_time_matches(norm, typ, ec)


def _normalized_live_rows_for_configured_events(
    events: dict[str, WatchEventConfig],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in get_recent_records_raw():
        norm = normalize_live_record(row)
        if not norm:
            continue
        if norm.get("event_id") not in events:
            continue
        out.append(norm)
    return out


def collect_new_live_records(
    *,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    state: dict[str, Any],
    normalized_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return new, unseen WCA Live record rows matching configured rules."""
    iso2_to_cr = get_iso2_to_continental_record_tag()
    seen = set(state.get("seen_live_ids") or [])
    rows = (
        normalized_rows
        if normalized_rows is not None
        else _normalized_live_rows_for_configured_events(events)
    )
    out: list[dict[str, Any]] = []
    for norm in rows:
        ec = events.get(norm.get("event_id") or "")
        if not ec:
            continue
        if not live_row_matches_or(norm, ec=ec, cfg=cfg, iso2_to_cr=iso2_to_cr):
            continue
        lid = norm.get("live_id")
        if not lid or lid in seen:
            continue
        out.append(norm)
    return out


def _seen_rest_result_ids(state: dict[str, Any]) -> set[int]:
    """Return the set of previously seen competition result ids from state."""
    ids_list = state.setdefault("seen_rest_result_ids", [])
    if not isinstance(ids_list, list):
        state["seen_rest_result_ids"] = []
        ids_list = state["seen_rest_result_ids"]
    seen: set[int] = set()
    for x in ids_list:
        try:
            seen.add(int(x))
        except (TypeError, ValueError):
            continue
    return seen


def _rest_alert_from_row(
    row: dict[str, Any], *, eid: str, rid_i: int, cid: str
) -> dict[str, Any]:
    """Build an alert dict from a matching competition results row."""
    return {
        "source": "competition",
        "result_id": rid_i,
        "wca_id": (row.get("wca_id") or "").strip().upper(),
        "name": row.get("name") or "",
        "country_iso2": row.get("country_iso2"),
        "event_id": eid,
        "competition_id": row.get("competition_id") or cid,
        "round_type_id": row.get("round_type_id"),
        "best": row.get("best"),
        "average": row.get("average"),
        "attempts": row.get("attempts"),
        "pos": row.get("pos"),
        "regional_single_record": row.get("regional_single_record"),
        "regional_average_record": row.get("regional_average_record"),
    }


def _process_competition_result_rows(
    rows: list[dict[str, Any]],
    *,
    cid: str,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    seen: set[int],
    bootstrap: bool,
    new_ids: list[int],
    alerts: list[dict[str, Any]],
) -> None:
    """Scan one competition's rows, recording ids and matching alerts."""
    for row in rows:
        rid = row.get("id")
        if rid is None:
            continue
        try:
            rid_i = int(rid)
        except (TypeError, ValueError):
            continue
        eid = row.get("event_id") or ""
        if eid not in events:
            continue
        new_ids.append(rid_i)
        ec = events[eid]
        if not _rest_row_matches_or(row, ec=ec, cfg=cfg):
            continue
        if bootstrap:
            continue
        if rid_i in seen:
            continue
        alerts.append(_rest_alert_from_row(row, eid=eid, rid_i=rid_i, cid=cid))


def collect_new_competition_result_rows(
    *,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    state: dict[str, Any],
    ended_days: int = 10,
    delay_s: float = 0.4,
) -> list[dict[str, Any]]:
    """Return new, unseen competition result rows matching configured rules."""
    seen = _seen_rest_result_ids(state)

    today_d = date.today()
    ended = get_competitions_ended_within_days(
        days=ended_days,
        today=today_d,
        on_progress=lambda p, n: logging.info(
            "  Past-comps page %d: %d match(es)", p, n
        ),
    )
    comp_ids = [c["id"] for c in ended if c.get("id")]
    logging.info(
        "%d competition(s) in last %d day(s) for results scan",
        len(comp_ids),
        ended_days,
    )

    bootstrap = len(seen) == 0
    alerts: list[dict[str, Any]] = []
    new_ids: list[int] = []

    for cid in comp_ids:
        try:
            rows = get_competition_results(cid)
        except Exception as e:
            logging.warning("Competition results %s: %s", cid, e)
            time.sleep(delay_s)
            continue
        time.sleep(delay_s)
        _process_competition_result_rows(
            rows,
            cid=cid,
            events=events,
            cfg=cfg,
            seen=seen,
            bootstrap=bootstrap,
            new_ids=new_ids,
            alerts=alerts,
        )

    merged = seen | set(new_ids)
    state["seen_rest_result_ids"] = sorted(merged)[-120_000:]

    if bootstrap:
        logging.info(
            "Bootstrap: stored %d competition result id(s); "
            "no rest-email this run",
            len(merged),
        )
        return []

    return alerts


def run_records_check(
    *,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    state_path: Path,
    update_state: bool = True,
    ended_days: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the records check, returning new live and competition rows."""
    state_disk = load_records_state(state_path)
    work = copy.deepcopy(state_disk) if not update_state else state_disk

    norm = _normalized_live_rows_for_configured_events(events)
    new_live = collect_new_live_records(
        events=events, cfg=cfg, state=work, normalized_rows=norm
    )
    rest_rows = collect_new_competition_result_rows(
        events=events,
        cfg=cfg,
        state=work,
        ended_days=ended_days,
    )

    if update_state:
        seen_set = set(work.get("seen_live_ids") or [])
        for r in new_live:
            lid = r.get("live_id")
            if lid:
                seen_set.add(lid)
        work["seen_live_ids"] = sorted(seen_set)[-8000:]
        save_records_state(state_path, work)

    return new_live, rest_rows
