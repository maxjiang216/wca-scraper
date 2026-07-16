"""Daily digest: WCA Live + recent competition results (per-event OR rules)."""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .cubing_live_api import (
    discover_recent_cubing_comps,
    fetch_competition_live_rows,
)
from .results_format import mbld_solved_count
from .watch_list import (
    WatchEventConfig,
    WatchListConfig,
    continental_tags_for_event,
)
from .wca_api import (
    fetch_person_bundle,
    get_competition_results,
    get_competitions_ended_within_days,
    get_iso2_to_continental_record_tag,
)
from .wca_live_api import get_recent_records_raw, normalize_live_record

# wca_id -> personal_records dict from GET /persons/{id} (empty on failure).
_PERSON_RECORDS_CACHE: dict[str, dict[str, Any]] = {}


def load_records_state(path: Path) -> dict[str, Any]:
    """Load deduplication state from disk, returning empty state if absent."""
    if not path.exists():
        return {
            "seen_live_ids": [],
            "seen_rest_result_ids": [],
            "seen_cubing_result_ids": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "seen_live_ids": [],
            "seen_rest_result_ids": [],
            "seen_cubing_result_ids": [],
        }
    if not isinstance(data, dict):
        return {
            "seen_live_ids": [],
            "seen_rest_result_ids": [],
            "seen_cubing_result_ids": [],
        }
    data.setdefault("seen_live_ids", [])
    for key in ("seen_rest_result_ids", "seen_cubing_result_ids"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def save_records_state(path: Path, state: dict[str, Any]) -> None:
    """Write deduplication state to disk as JSON."""
    path.write_text(
        json.dumps(state, indent=0, sort_keys=True), encoding="utf-8"
    )


def _parse_iso_date(val: Any) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date, returning None on bad input."""
    if not val:
        return None
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _safe_pos_int(val: Any) -> int | None:
    try:
        i = int(val)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _allowed_cr_tags(ec: WatchEventConfig, cfg: WatchListConfig) -> set[str]:
    return continental_tags_for_event(ec, cfg)


def _person_personal_records(wca_id: str) -> dict[str, Any]:
    """Return ``personal_records`` for ``wca_id``, cached per process."""
    wid = (wca_id or "").strip().upper()
    if not wid:
        return {}
    if wid in _PERSON_RECORDS_CACHE:
        return _PERSON_RECORDS_CACHE[wid]
    try:
        bundle = fetch_person_bundle(wid)
        pr = bundle.get("personal_records") or {}
        if not isinstance(pr, dict):
            pr = {}
    except Exception as e:
        logging.warning("personal_records %s: %s", wid, e)
        pr = {}
    _PERSON_RECORDS_CACHE[wid] = pr
    return pr


def _personal_best_cs(wca_id: str, event_id: str, typ: str) -> int | None:
    """Published PB in centiseconds for ``event_id`` / ``typ``, or None."""
    pr = _person_personal_records(wca_id)
    ev = pr.get(event_id) or {}
    if not isinstance(ev, dict):
        return None
    side = ev.get(typ) or {}
    if not isinstance(side, dict):
        return None
    return _safe_pos_int(side.get("best"))


def _is_pb(value: int | None, prior: int | None, *, published: bool) -> bool:
    """Whether ``value`` is a personal best vs published ``prior``.

    Live / cubing results are not in ranks yet → strict ``<``.
    Published REST results already update ranks when they *are* the new
    PB → ``<=``. No prior PB counts as a PB. Invalid times do not.
    """
    if value is None:
        return False
    if prior is None:
        return True
    if published:
        return value <= prior
    return value < prior


def _add_record_tag_sides(
    sides: set[str],
    *,
    rs: str,
    ra: str,
    allowed: set[str],
) -> None:
    """Add single/average sides for WR or allowed continental tags."""
    if rs == "WR" or (allowed and rs in allowed):
        sides.add("single")
    if ra == "WR" or (allowed and ra in allowed):
        sides.add("average")


def _add_mbf_sup_sides(
    sides: set[str],
    *,
    best: int | None,
    avg: int | None,
    sup_points: int,
) -> None:
    """Add sides where MBLD solved count meets ``sup_points``."""
    for typ, raw in (("single", best), ("average", avg)):
        if raw is None:
            continue
        sc = mbld_solved_count(int(raw))
        if sc is not None and sc >= sup_points:
            sides.add(typ)


def _add_sub_time_sides(
    sides: set[str],
    *,
    best: int | None,
    avg: int | None,
    ec: WatchEventConfig,
) -> None:
    """Add sides under configured ``sub_single`` / ``sub_average`` caps."""
    if (
        ec.sub_single_cs is not None
        and best is not None
        and best < ec.sub_single_cs
    ):
        sides.add("single")
    if (
        ec.sub_average_cs is not None
        and avg is not None
        and avg < ec.sub_average_cs
    ):
        sides.add("average")


def _rest_matching_sides(
    row: dict[str, Any],
    *,
    ec: WatchEventConfig,
    cfg: WatchListConfig,
) -> set[str]:
    """Return which of ``single`` / ``average`` triggered the OR rules."""
    sides: set[str] = set()
    eid = row.get("event_id") or ""
    rs = (row.get("regional_single_record") or "").strip().upper()
    ra = (row.get("regional_average_record") or "").strip().upper()
    _add_record_tag_sides(
        sides,
        rs=rs,
        ra=ra,
        allowed=_allowed_cr_tags(ec, cfg),
    )

    best = _safe_pos_int(row.get("best"))
    avg = _safe_pos_int(row.get("average"))

    if eid == "333mbf" and ec.mbf_sup_points is not None:
        _add_mbf_sup_sides(
            sides, best=best, avg=avg, sup_points=ec.mbf_sup_points
        )
        return sides

    if eid != "333mbf":
        _add_sub_time_sides(sides, best=best, avg=avg, ec=ec)

    return sides


def _rest_row_is_pb(
    row: dict[str, Any],
    *,
    ec: WatchEventConfig,
    cfg: WatchListConfig,
    published: bool,
) -> bool:
    """True if any OR-matching side on the row is a personal best."""
    wca_id = (row.get("wca_id") or "").strip().upper()
    if not wca_id:
        return False
    eid = row.get("event_id") or ""
    sides = _rest_matching_sides(row, ec=ec, cfg=cfg)
    if not sides:
        return False
    for typ in sides:
        key = "best" if typ == "single" else "average"
        val = _safe_pos_int(row.get(key))
        prior = _personal_best_cs(wca_id, eid, typ)
        if _is_pb(val, prior, published=published):
            return True
    return False


def _live_row_is_pb(norm: dict[str, Any]) -> bool:
    """True if the live row's typed result is a personal best."""
    wca_id = (norm.get("wca_id") or "").strip().upper()
    if not wca_id:
        return False
    eid = norm.get("event_id") or ""
    typ = (norm.get("type") or "single").lower()
    if typ not in ("single", "average"):
        typ = "single"
    val = _live_result_centiseconds(norm, typ)
    prior = _personal_best_cs(wca_id, eid, typ)
    return _is_pb(val, prior, published=False)


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
    return bool(_rest_matching_sides(row, ec=ec, cfg=cfg))


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
    ended_days: int = 10,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return new, unseen WCA Live record rows matching configured rules.

    WCA Live ``recentRecords`` is a count-based rolling window with no date
    bound, so records can linger for weeks. Unlike late-uploaded REST
    results, Live records appear in real time, so anything from a comp that
    ended more than ``ended_days`` ago is stale (we have already had the
    chance to see it) and is dropped. A cold-start ``seen_live_ids`` (lost
    cache, first run) bootstraps silently — recording current ids without
    emailing — so a reset cannot flood the digest with old records.
    """
    iso2_to_cr = get_iso2_to_continental_record_tag()
    seen = set(state.get("seen_live_ids") or [])
    bootstrap = len(seen) == 0
    cutoff = (today or date.today()) - timedelta(days=ended_days)
    rows = (
        normalized_rows
        if normalized_rows is not None
        else _normalized_live_rows_for_configured_events(events)
    )
    matched_ids: list[str] = []
    out: list[dict[str, Any]] = []
    for norm in rows:
        ec = events.get(norm.get("event_id") or "")
        if not ec:
            continue
        if not live_row_matches_or(norm, ec=ec, cfg=cfg, iso2_to_cr=iso2_to_cr):
            continue
        lid = norm.get("live_id")
        if not lid:
            continue
        end = _parse_iso_date(norm.get("comp_end_date"))
        if end is not None and end < cutoff:
            continue
        matched_ids.append(lid)
        if bootstrap or lid in seen:
            continue
        if not _live_row_is_pb(norm):
            continue
        out.append(norm)

    if bootstrap:
        state["seen_live_ids"] = sorted(seen | set(matched_ids))[-8000:]
        logging.info(
            "Bootstrap: stored %d WCA Live record id(s); "
            "no live-email this run",
            len(matched_ids),
        )
        return []
    return out


def _seen_int_ids(state: dict[str, Any], key: str) -> set[int]:
    """Return the set of previously seen integer result ids under ``key``."""
    ids_list = state.setdefault(key, [])
    if not isinstance(ids_list, list):
        state[key] = []
        ids_list = state[key]
    seen: set[int] = set()
    for x in ids_list:
        try:
            seen.add(int(x))
        except (TypeError, ValueError):
            continue
    return seen


def _rest_alert_from_row(
    row: dict[str, Any],
    *,
    eid: str,
    rid_i: int,
    cid: str,
    source: str = "competition",
) -> dict[str, Any]:
    """Build an alert dict from a matching competition results row."""
    return {
        "source": source,
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
    source: str = "competition",
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
        published = source != "cubing_live"
        if not _rest_row_is_pb(row, ec=ec, cfg=cfg, published=published):
            continue
        alerts.append(
            _rest_alert_from_row(
                row, eid=eid, rid_i=rid_i, cid=cid, source=source
            )
        )


def collect_new_competition_result_rows(
    *,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    state: dict[str, Any],
    ended_days: int = 10,
    delay_s: float = 0.4,
    skip_competition_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return new, unseen competition result rows matching configured rules.

    ``skip_competition_ids`` are WCA competition ids already covered by the
    cubing.com live feed this run; they are excluded so a Chinese comp's
    results are alerted from a single source (cubing leads WCA by days).
    """
    seen = _seen_int_ids(state, "seen_rest_result_ids")
    skip = skip_competition_ids or set()

    today_d = date.today()
    ended = get_competitions_ended_within_days(
        days=ended_days,
        today=today_d,
        on_progress=lambda p, n: logging.info(
            "  Past-comps page %d: %d match(es)", p, n
        ),
    )
    comp_ids = [c["id"] for c in ended if c.get("id") and c["id"] not in skip]
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


def collect_new_cubing_result_rows(
    *,
    events: dict[str, WatchEventConfig],
    cfg: WatchListConfig,
    state: dict[str, Any],
    comps: list[dict[str, Any]],
    rate_limit_delay_s: float = 0.4,
) -> list[dict[str, Any]]:
    """Return new, unseen cubing.com live result rows matching the rules.

    ``comps`` come from
    :func:`wca_org.cubing_live_api.discover_recent_cubing_comps`. Result ids
    live in their own ``seen_cubing_result_ids`` namespace (cubing ids are
    unrelated to WCA result ids), with the same cold-start bootstrap as the
    REST path.
    """
    seen = _seen_int_ids(state, "seen_cubing_result_ids")
    bootstrap = len(seen) == 0
    alerts: list[dict[str, Any]] = []
    new_ids: list[int] = []

    for comp in comps:
        alias = comp.get("alias") or ""
        cid = comp.get("competition_id") or f"cubing:{alias}"
        try:
            rows = fetch_competition_live_rows(
                alias,
                competition_id=cid,
                rate_limit_delay_s=rate_limit_delay_s,
            )
        except Exception as e:  # network failure; skip this comp
            logging.warning("cubing live %s: %s", alias, e)
            continue
        logging.info(
            "  cubing live %s: %d finished-round row(s)", alias, len(rows)
        )
        _process_competition_result_rows(
            rows,
            cid=cid,
            events=events,
            cfg=cfg,
            seen=seen,
            bootstrap=bootstrap,
            new_ids=new_ids,
            alerts=alerts,
            source="cubing_live",
        )

    merged = seen | set(new_ids)
    state["seen_cubing_result_ids"] = sorted(merged)[-120_000:]

    if bootstrap:
        logging.info(
            "Bootstrap: stored %d cubing live result id(s); "
            "no cubing-email this run",
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
    cubing_live: bool = True,
    cubing_rate_limit_s: float = 0.4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the records check, returning new live and competition rows.

    Competition rows merge two sources: cubing.com's live feed (Chinese
    comps, available days early) and the WCA REST ``/results`` scan. Comps
    covered by cubing this run are skipped in the WCA scan so each result is
    alerted once.
    """
    state_disk = load_records_state(state_path)
    work = copy.deepcopy(state_disk) if not update_state else state_disk

    norm = _normalized_live_rows_for_configured_events(events)
    new_live = collect_new_live_records(
        events=events,
        cfg=cfg,
        state=work,
        normalized_rows=norm,
        ended_days=ended_days,
    )

    cubing_rows: list[dict[str, Any]] = []
    cubing_covered: set[str] = set()
    if cubing_live:
        window_start = date.today() - timedelta(days=ended_days)
        comps = discover_recent_cubing_comps(
            window_start=window_start,
            window_end=date.today(),
            rate_limit_delay_s=cubing_rate_limit_s,
        )
        cubing_covered = {
            c["wca_competition_id"]
            for c in comps
            if c.get("wca_competition_id")
        }
        cubing_rows = collect_new_cubing_result_rows(
            events=events,
            cfg=cfg,
            state=work,
            comps=comps,
            rate_limit_delay_s=cubing_rate_limit_s,
        )

    rest_rows = collect_new_competition_result_rows(
        events=events,
        cfg=cfg,
        state=work,
        ended_days=ended_days,
        skip_competition_ids=cubing_covered,
    )

    if update_state:
        seen_set = set(work.get("seen_live_ids") or [])
        for r in new_live:
            lid = r.get("live_id")
            if lid:
                seen_set.add(lid)
        work["seen_live_ids"] = sorted(seen_set)[-8000:]
        save_records_state(state_path, work)

    return new_live, cubing_rows + rest_rows
