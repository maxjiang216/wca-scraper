"""
Email notification when watched competitors are competing at upcoming competitions.
"""

import re
import smtplib
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


def _format_centiseconds(event_id: str, raw: str) -> str:
    """Human-readable result from TSV ``best`` / ``average`` (centiseconds for most speedsolving)."""
    s = (raw or "").strip()
    if not s or s in ("0", "-1", "-2"):
        return s or "—"
    if event_id in ("333fm", "333mbf", "magic", "mmagic"):
        return s
    try:
        cs = int(s)
    except ValueError:
        return s
    if cs < 0:
        return s
    sec = cs / 100.0
    if sec < 60:
        return f"{sec:.2f}"
    m = int(sec // 60)
    r = sec - m * 60
    return f"{m}:{r:06.3f}".rstrip("0").rstrip(".")


def format_records_alert(
    live_records: list[dict],
    competition_results: list[dict],
    *,
    timezone: str = "America/Vancouver",
) -> str:
    """HTML for daily ``wca records`` (WCA Live + new competition result rows)."""
    from .results_format import mbld_solved_count

    parts = [
        '<h1 style="margin-bottom: 0.6em;">WCA daily digest</h1>',
        f'<p style="color:#666">Timezone note: {_tz_display(timezone)}</p>',
        "<p style='color:#444;font-size:0.95em;'>Per event: <strong>WR</strong>, optional "
        "continental tags, <strong>sub</strong> single/average (all competitors), or <strong>sup</strong> "
        "MBLD points — <strong>OR</strong> together. Default is WR-only.</p>",
    ]
    if live_records:
        parts.append("<h2>WCA Live</h2>")
        for r in live_records:
            ev = EVENT_NAMES.get(r.get("event_id") or "", r.get("event_id") or "")
            tag = r.get("tag") or ""
            kind = r.get("type") or ""
            val = r.get("attempt_result")
            parts.append(
                "<p style='margin:0.4em 0 0.4em 1em;'>· <strong>{}</strong> {} <em>{}</em> — "
                "{} ({}) [{}]</p>".format(
                    tag,
                    ev,
                    kind,
                    _strip_parens(r.get("name") or ""),
                    r.get("wca_id") or "",
                    val,
                ),
            )
    if competition_results:
        parts.append("<h2>Competition results (published)</h2>")
        for r in competition_results:
            ev = EVENT_NAMES.get(r.get("event_id") or "", r.get("event_id") or "")
            eid = r.get("event_id") or ""
            cid = r.get("competition_id") or ""
            rs = (r.get("regional_single_record") or "") or "—"
            ra = (r.get("regional_average_record") or "") or "—"
            if eid == "333mbf":
                b_raw, a_raw = r.get("best"), r.get("average")
                try:
                    b_sc = mbld_solved_count(int(b_raw)) if b_raw not in (None, "") else None
                except (TypeError, ValueError):
                    b_sc = None
                try:
                    a_sc = mbld_solved_count(int(a_raw)) if a_raw not in (None, "") else None
                except (TypeError, ValueError):
                    a_sc = None
                b_disp = f"{b_sc} solved" if b_sc is not None else str(b_raw or "—")
                a_disp = f"{a_sc} solved" if a_sc is not None else str(a_raw or "—")
            else:
                b_disp = _format_centiseconds(eid, str(r.get("best") or ""))
                a_disp = _format_centiseconds(eid, str(r.get("average") or ""))
            parts.append(
                "<p style='margin:0.4em 0 0.4em 1em;'>· <strong>{}</strong> ({}) — {} @ "
                '<a href="https://www.worldcubeassociation.org/competitions/{}">{}</a> '
                "single {} avg {} "
                "<span style='color:#666'>[{} / {}]</span></p>".format(
                    _strip_parens(r.get("name") or ""),
                    r.get("wca_id") or "",
                    ev,
                    cid,
                    cid,
                    b_disp,
                    a_disp,
                    rs,
                    ra,
                ),
            )
    if not live_records and not competition_results:
        parts.append("<p>No new items matched your rules in this check.</p>")
    return "\n".join(parts)


def format_weekly_digest(
    psych_results: list[dict],
    *,
    timezone: str,
) -> str:
    """Combined HTML for ``wca weekly`` (upcoming psych sheet only)."""
    psych_block = format_results_by_week(psych_results, timezone=timezone)
    parts = [
        '<h1 style="margin-bottom:0.3em;">WCA weekly — upcoming psych sheet</h1>',
        f'<p style="color:#666">Times shown in {_tz_display(timezone)}</p>',
        "<h2 style=\"margin-top:1em;\">Watched competitors</h2>",
        psych_block,
        "<hr style=\"margin:1.5em 0;\">",
        "<p style=\"color:#666;font-size:0.9em;\">Daily result digests: <code>wca records</code>.</p>",
    ]
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
