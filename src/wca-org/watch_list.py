"""Watch list file (YAML/JSON): psych ``people`` + per-event **daily** OR rules."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class WatchEventConfig:
    """
    - **people** — WCA IDs included in **psych sheet** emails only (``notify`` / ``weekly``).
    - **Daily** digest (``wca records``) uses **OR** rules (default: **world records only**):

      - **WR** always considered.
      - **continental_records** — if set non-empty, rows whose ``regional_*_record``
        matches one of these tags (e.g. **NAR**, **ER**). Omit or leave unset for no CR
        filtering (not inherited from ``config``).
      - **sub_single_cs** / **sub_average_cs** — any competitor: single or average **strictly
        under** this time (centiseconds); ignored for **333mbf**.
      - **mbf_sup_points** — for **333mbf** only: notify when decoded MBLD **solved count**
        is **≥** this value (``sup``, not sub).

    YAML: ``sub_single`` / ``sub_average`` / ``*_seconds``; ``sup_points`` / ``mbf_sup`` / ``sup_mbf``.
    Legacy: ``roundup_all`` / flat list ⇒ ``people``. ``daily:``, ``weekly_sub_pb:`` merged into thresholds.
    """

    people: set[str] = field(default_factory=set)
    continental_records: Optional[list[str]] = None
    sub_single_cs: Optional[int] = None
    sub_average_cs: Optional[int] = None
    mbf_sup_points: Optional[int] = None

    def all_watched(self) -> set[str]:
        return set(self.people)


@dataclass
class WatchListConfig:
    """Settings from ``watch_list.yaml`` ``config:`` block."""

    timezone: str = "America/Vancouver"
    continental_records: list[str] = field(
        default_factory=lambda: ["NAR", "ER", "AsR", "OcR"],
    )


def _parse_id_set(val: Any) -> set[str]:
    if val is None:
        return set()
    if isinstance(val, list):
        return {str(x).strip().upper() for x in val if str(x).strip()}
    return {str(val).strip().upper()}


def _parse_sub_centiseconds(d: dict, base_key: str) -> Optional[int]:
    for key, mult in (
        (f"{base_key}_cs", 1),
        (base_key, 1),
        (f"{base_key}_seconds", 100),
        (f"{base_key}_sec", 100),
    ):
        if key not in d or d.get(key) is None:
            continue
        v = d[key]
        try:
            if mult == 100:
                f = float(v)
                i = int(round(f * 100))
            else:
                i = int(v)
            return i if i > 0 else None
        except (TypeError, ValueError):
            continue
    return None


def _parse_continental_records(val: Any) -> Optional[list[str]]:
    if val is None:
        return None
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    return [s] if s else []


def _parse_sup_points(*dicts: dict) -> Optional[int]:
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for key in ("mbf_sup", "sup_points", "sup_mbf", "mbf_sup_points"):
            if key not in d or d.get(key) is None:
                continue
            try:
                i = int(d[key])
                return i if i >= 0 else None
            except (TypeError, ValueError):
                continue
    return None


def _merge_sub_cs(*dicts: dict) -> tuple[Optional[int], Optional[int]]:
    ss, sa = None, None
    for d in dicts:
        if not isinstance(d, dict):
            continue
        if ss is None:
            t = _parse_sub_centiseconds(d, "sub_single")
            if t is not None:
                ss = t
        if sa is None:
            t = _parse_sub_centiseconds(d, "sub_average")
            if t is not None:
                sa = t
    return ss, sa


def _parse_event_config(event_key: str, raw: Any) -> WatchEventConfig:
    if isinstance(raw, list):
        return WatchEventConfig(people=_parse_id_set(raw))

    if not isinstance(raw, dict):
        return WatchEventConfig()

    daily = raw.get("daily")
    daily_d = daily if isinstance(daily, dict) else {}
    sub_pb = raw.get("weekly_sub_pb")
    sub_pb_d = sub_pb if isinstance(sub_pb, dict) else {}

    people: set[str] = set()
    people |= _parse_id_set(raw.get("people"))
    people |= _parse_id_set(raw.get("roundup_all"))
    people |= _parse_id_set(raw.get("all_results"))

    continental_records: Optional[list[str]] = None
    for key in ("continental_records", "cr", "crs"):
        if key in raw:
            continental_records = _parse_continental_records(raw.get(key))
            break
    if continental_records is None and "continental_records" in daily_d:
        continental_records = _parse_continental_records(daily_d.get("continental_records"))

    ss, sa = _merge_sub_cs(raw, daily_d, sub_pb_d)
    mbf_sup = _parse_sup_points(raw, daily_d, sub_pb_d)

    return WatchEventConfig(
        people=people,
        continental_records=continental_records,
        sub_single_cs=ss,
        sub_average_cs=sa,
        mbf_sup_points=mbf_sup,
    )


def flat_per_event_watches(events: dict[str, WatchEventConfig]) -> dict[str, set[str]]:
    """event_id → ``people`` for psych sheet matching."""
    return {eid: ec.all_watched() for eid, ec in events.items()}


def union_all_wca_ids(events: dict[str, WatchEventConfig]) -> set[str]:
    return {w for ec in events.values() for w in ec.all_watched()}


def load_watch_list_document(path: Path) -> tuple[WatchListConfig, dict[str, WatchEventConfig]]:
    if not path.exists():
        return WatchListConfig(), {}

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(text)
    else:
        raw = yaml.safe_load(text)

    if not isinstance(raw, dict):
        logging.error("Watch list root must be a mapping (YAML object): %s", path)
        return WatchListConfig(), {}

    config_data = raw.get("config") or {}
    if not isinstance(config_data, dict):
        config_data = {}

    tz = config_data.get("timezone") or "America/Vancouver"
    cont = config_data.get("continental_records")
    if cont is None:
        cont = ["NAR", "ER", "AsR", "OcR"]
    if not isinstance(cont, list):
        cont = ["NAR", "ER", "AsR", "OcR"]

    config = WatchListConfig(
        timezone=str(tz).strip(),
        continental_records=[str(x).strip() for x in cont if str(x).strip()],
    )

    events: dict[str, WatchEventConfig] = {}
    for event_id, block in raw.items():
        if event_id == "config":
            continue
        ek = str(event_id).strip()
        events[ek] = _parse_event_config(ek, block)

    return config, events


def continental_tags_for_event(
    ec: WatchEventConfig,
    cfg: WatchListConfig,
) -> set[str]:
    """
    Continental record tags that trigger a **CR** match for this event.

    ``None`` or ``[]`` → **no** CR (default daily behavior is WR-only unless you add subs
    or explicit CR lists). Non-empty list → only those tags.
    """
    del cfg  # unused; no inheritance — explicit per-event list only
    if not ec.continental_records:
        return set()
    return {x.strip() for x in ec.continental_records if x.strip()}


continental_tags_for_daily_event = continental_tags_for_event
