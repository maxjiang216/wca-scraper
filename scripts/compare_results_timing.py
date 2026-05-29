#!/usr/bin/env python3
"""Compare result availability between cubing.com and the official WCA REST API.

Purpose: find out whether cubing.com publishes a Chinese competition's results
*earlier* than the official WCA upload. Run repeatedly after a comp finishes and
watch which source lights up first.

Usage:
    uv run python scripts/compare_results_timing.py QuanzhouSummer2026
    uv run python scripts/compare_results_timing.py \
        QuanzhouSummer2026 --event 333
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime

import requests  # type: ignore[import-untyped]

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def cubing_results(wca_id: str) -> tuple[int, list[str]]:
    """(row_count, sample_lines) from cubing.com's per-round results page.

    cubing.com soft-rate-limits the first hit with a 429 that sets the
    ``CubingRateLimit`` cookie via JS, then reloads — we set it up front.
    The page is keyed by the WCA competition id, not the cubing alias.
    """
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    s.cookies.set("CubingRateLimit", "1", domain="cubing.com")
    url = f"https://cubing.com/results/competition/{wca_id}/all"
    r = s.get(url, params={"lang": "en"}, timeout=60)
    if r.status_code == 404:
        return 0, []
    r.raise_for_status()
    txt = re.sub(r"<[^>]+>", " ", r.text)
    txt = re.sub(r"\s+", " ", txt)
    # A result row reads as name, local name, best, average,
    # region, then attempt times.
    rows = re.findall(r"[A-Z][a-zA-Z\- ]+ \([^)]+\) +\d", txt)
    sample = [m.strip()[:70] for m in rows[:5]]
    return len(rows), sample


def wca_results(wca_id: str) -> tuple[int, list[str]]:
    """(row_count, sample_lines) from the official WCA REST results endpoint."""
    url = f"https://www.worldcubeassociation.org/api/v0/competitions/{wca_id}/results"
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=60)
    if r.status_code == 404:
        return 0, []
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else []
    sample = [
        f"{x.get('name')} {x.get('event_id')} "
        f"best={x.get('best')} avg={x.get('average')}"
        for x in rows[:5]
    ]
    return len(rows), sample


def main() -> int:
    """Compare cubing.com and WCA result availability for a competition."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("wca_id", help="WCA competition id, e.g. QuanzhouSummer2026")
    args = p.parse_args()

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== {args.wca_id} @ {now} ===")

    try:
        c_n, c_s = cubing_results(args.wca_id)
    except Exception as e:
        c_n, c_s = -1, [f"ERROR: {e}"]
    try:
        w_n, w_s = wca_results(args.wca_id)
    except Exception as e:
        w_n, w_s = -1, [f"ERROR: {e}"]

    print(f"cubing.com : {c_n:>5} result rows")
    for s in c_s:
        print(f"             {s}")
    print(f"WCA REST   : {w_n:>5} result rows")
    for s in w_s:
        print(f"             {s}")

    if c_n > 0 and w_n <= 0:
        print(">>> cubing.com has results, WCA does NOT — cubing is EARLIER.")
    elif w_n > 0 and c_n <= 0:
        print(">>> WCA has results, cubing.com does NOT.")
    elif c_n > 0 and w_n > 0:
        print(">>> Both have results.")
    else:
        print(">>> Neither source has results yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
