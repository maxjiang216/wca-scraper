"""WCA Live GraphQL API (recent records, no auth)."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import requests  # type: ignore[import-untyped]

LIVE_API_URL = "https://live.worldcubeassociation.org/api"

RECENT_RECORDS_QUERY = """
query RecentRecords {
  recentRecords {
    id
    tag
    type
    attemptResult
    result {
      best
      average
      singleRecordTag
      averageRecordTag
      attempts {
        result
      }
      person {
        wcaId
        name
        country { iso2 }
      }
      round {
        name
        competitionEvent {
          event { id name }
        }
      }
    }
  }
}
"""


def _graphql(query: str) -> dict[str, Any]:
    r = requests.post(
        LIVE_API_URL,
        json={"query": query},
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"WCA Live GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def _smoke_check_live_records(rows: list[dict[str, Any]]) -> None:
    """Log warnings if WCA Live record schema looks unexpected."""
    if not rows:
        return
    row = rows[0]
    expected = ("id", "tag", "type", "attemptResult", "result")
    missing = [k for k in expected if k not in row]
    if missing:
        logging.warning(
            "[smoke] WCA Live record missing fields: %s; sample keys: %s",
            missing,
            sorted(row.keys()),
        )
    res = row.get("result") or {}
    if not isinstance(res, dict):
        logging.warning(
            "[smoke] WCA Live record.result is not a dict: %s",
            type(res).__name__,
        )
        return
    res_expected = ("person", "best", "average", "attempts", "round")
    res_missing = [k for k in res_expected if k not in res]
    if res_missing:
        logging.warning(
            "[smoke] WCA Live record.result missing fields: %s; got keys: %s",
            res_missing,
            sorted(res.keys()),
        )
    person = res.get("person") or {}
    if not isinstance(person, dict) or not person.get("wcaId"):
        logging.warning(
            "[smoke] WCA Live record.result.person missing wcaId; "
            "person keys: %s",
            sorted(person.keys())
            if isinstance(person, dict)
            else type(person).__name__,
        )


def get_recent_records_raw() -> list[dict[str, Any]]:
    """Raw ``recentRecords`` entries from WCA Live."""
    data = _graphql(RECENT_RECORDS_QUERY)
    rows = data.get("recentRecords") or []
    if not isinstance(rows, list):
        logging.warning(
            "[smoke] WCA Live recentRecords is not a list: %s",
            type(rows).__name__,
        )
        return []
    _smoke_check_live_records(rows)
    return rows


def normalize_live_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten a recentRecords row for filtering / email."""
    res = row.get("result") or {}
    if not isinstance(res, dict):
        return None
    person = res.get("person") or {}
    rd = res.get("round") or {}
    ev_wrap = rd.get("competitionEvent") or {}
    ev = ev_wrap.get("event") or {}
    wca_id = (person.get("wcaId") or "").strip().upper()
    event_id = (ev.get("id") or "").strip()
    if not wca_id:
        return None
    attempts_raw: list[int] = []
    for att in res.get("attempts") or []:
        if isinstance(att, dict) and att.get("result") is not None:
            with contextlib.suppress(TypeError, ValueError):
                attempts_raw.append(int(att["result"]))

    return {
        "live_id": row.get("id"),
        "tag": (row.get("tag") or "").strip().upper(),
        "type": (row.get("type") or "").strip().lower(),
        "attempt_result": row.get("attemptResult"),
        "best": res.get("best"),
        "average": res.get("average"),
        "attempts": attempts_raw,
        "single_record_tag": res.get("singleRecordTag"),
        "average_record_tag": res.get("averageRecordTag"),
        "wca_id": wca_id,
        "name": person.get("name") or wca_id,
        "country_iso2": ((person.get("country") or {}).get("iso2") or "")
        or None,
        "event_id": event_id,
        "event_name": ev.get("name") or event_id,
        "round_name": rd.get("name") or "",
    }


def continental_alerts_enabled(continental_config: list[str]) -> bool:
    """Whether the user opted into continental record alerts.

    YAML uses NAR/ER/…; WCA Live uses the generic ``CR`` tag.
    """
    return bool(continental_config)
