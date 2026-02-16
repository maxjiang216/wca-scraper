"""
Email notification when watched competitors are competing at upcoming competitions.
"""

import re
import smtplib
from collections import defaultdict
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

# Event ID to human-readable name
EVENT_NAMES = {
    "333": "3x3",
    "222": "2x2",
    "444": "4x4",
    "555": "5x5",
    "666": "6x6",
    "777": "7x7",
    "333bf": "3BLD",
    "333fm": "FMC",
    "333oh": "OH",
    "333ft": "Feet",
    "minx": "Megaminx",
    "pyram": "Pyraminx",
    "clock": "Clock",
    "skewb": "Skewb",
    "sq1": "Square-1",
    "444bf": "4BLD",
    "555bf": "5BLD",
    "333mbf": "MBLD",
}


def _strip_parens(s: str) -> str:
    """Remove parenthetical content e.g. 'Name (本地名)' -> 'Name'."""
    return re.sub(r"\s*\([^)]*\)", "", s).strip()


def _tz_display(tz: str) -> str:
    """Human-readable timezone for header."""
    m = {"America/Los_Angeles": "PST", "America/New_York": "EST", "America/Chicago": "CST"}
    return m.get(tz, tz.split("/")[-1].replace("_", " "))


def format_results_by_week(
    results: list[dict],
    *,
    timezone: str = "America/Los_Angeles",
) -> str:
    """
    Build HTML report: competition-first, then events with times, then competitors.

    Format: All times in PST
      Competition 1 (Country) (dates)
       - Event 1 Round 1 (time): competitor1, competitor2
       - Event 2 Final (time): competitor3
      Competition 2 ...
    """
    if not results:
        return "<p>No results.</p>"

    parts = [
        '<h1 style="margin-bottom: 0.8em;">Watched Competitors — Upcoming Competitions</h1>',
        f'<p style="color: #666; margin-bottom: 1em;">All times in {_tz_display(timezone)}</p>',
    ]

    def _parse_round_time(time_str: str, comp_start: str) -> datetime | None:
        """Parse 'Fri Feb 20, 05:50 PM' to datetime. Uses comp start for year."""
        if not time_str:
            return None
        year = (comp_start or "")[:4] or "2026"
        try:
            return datetime.strptime(f"{year} {time_str}", "%Y %a %b %d, %I:%M %p")
        except ValueError:
            return None

    def get_earliest_round_time(r: dict) -> datetime:
        """Earliest end time (full date+time) of any round. Comps with no times use comp start date."""
        comp = r["comp"]
        comp_start = comp.get("start_date") or ""
        fallback = datetime.max
        try:
            fallback = datetime.strptime(comp_start or "9999-12-31", "%Y-%m-%d")
        except (ValueError, TypeError):
            pass
        earliest = datetime.max
        for p in r["watched_competitors"]:
            for s in p.get("schedule", []):
                time_str = s.get("end_local") or s.get("start_local") or ""
                dt = _parse_round_time(time_str, comp_start)
                if dt:
                    earliest = min(earliest, dt)
        return earliest if earliest != datetime.max else fallback

    # Sort competitions by earliest round end time
    for r in sorted(results, key=lambda r: (get_earliest_round_time(r), r["comp_name"])):
        comp = r["comp"]
        comp_name = r["comp_name"]
        comp_url = r["comp_url"]
        country = comp.get("country_iso2", "")
        comp_start = comp.get("start_date") or ""

        # Build event_round -> (time, [competitors])
        event_rounds: dict[tuple[str, str], tuple[str, list[str]]] = {}
        for p in r["watched_competitors"]:
            name = _strip_parens(p.get("name", "Unknown"))
            schedule = p.get("schedule", [])
            if schedule:
                for s in schedule:
                    ev = EVENT_NAMES.get(s["event_id"], s["event_id"])
                    rd = s.get("name", "")
                    label = f"{ev} {rd}".strip() if rd else ev
                    key = (s["event_id"], rd)
                    time_str = s.get("end_local") or s.get("start_local") or ""
                    if key not in event_rounds:
                        event_rounds[key] = (time_str, [])
                    if name not in event_rounds[key][1]:
                        event_rounds[key][1].append(name)
            else:
                # No schedule: group by event
                for eid in p.get("events", []):
                    ev = EVENT_NAMES.get(eid, eid)
                    key = (eid, "")
                    if key not in event_rounds:
                        event_rounds[key] = ("", [])
                    if name not in event_rounds[key][1]:
                        event_rounds[key][1].append(name)

        # Date range from rounds we show (PST/target tz times)
        round_dates: list[datetime] = []
        for (_, _), (time_str, _) in event_rounds.items():
            dt = _parse_round_time(time_str, comp_start)
            if dt:
                round_dates.append(dt)
        if round_dates:
            d_min, d_max = min(round_dates).date(), max(round_dates).date()
            date_str = d_min.strftime("%a %b %d") + (f" – {d_max.strftime('%a %b %d')}" if d_max != d_min else "")
        else:
            date_str = r["date_str"]  # fallback to comp dates

        comp_link = f'<a href="{comp_url}">{comp_name}</a>'
        country_part = f" ({country})" if country else ""
        comp_header = f"{comp_link}{country_part} ({date_str})"
        parts.append(f'<h2 style="margin-top: 1.2em; margin-bottom: 0.4em;">{comp_header}</h2>')

        # Sort events earliest to latest (full date+time), then by event for ties
        def event_sort_key(item: tuple) -> tuple:
            (eid, rd), (time_str, _) = item
            dt = _parse_round_time(time_str, comp_start)
            return (dt if dt else datetime.max, eid, rd)

        for (eid, rd), (time_str, names) in sorted(event_rounds.items(), key=event_sort_key):
            ev = EVENT_NAMES.get(eid, eid)
            label = f"{ev} {rd}".strip() if rd else ev
            time_part = f" ({time_str})" if time_str else ""
            comps = ", ".join(names)
            parts.append(f'<p style="margin: 0.3em 0 0.3em 1.2em;">• <strong>{label}</strong>{time_part}: {comps}</p>')

    return "\n".join(parts)


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> None:
    """
    Send an email notification.

    For Gmail: use an app password, not your regular password.
    See: https://support.google.com/accounts/answer/185833
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user or "noreply@local"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user or "noreply@local", to_email, msg.as_string())
