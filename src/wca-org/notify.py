"""
Email notification when watched competitors are competing at upcoming competitions.
"""

from html import escape
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

# Longer event titles for record digest rows (WCA-style naming).
EVENT_DIGEST_LABELS = {
    "333": "3x3x3 Cube",
    "222": "2x2x2 Cube",
    "444": "4x4x4 Cube",
    "555": "5x5x5 Cube",
    "666": "6x6x6 Cube",
    "777": "7x7x7 Cube",
    "333bf": "3x3x3 Blindfolded",
    "333fm": "3x3x3 Fewest Moves",
    "333oh": "3x3x3 One-Handed",
    "333ft": "3x3x3 With Feet",
    "minx": "Megaminx",
    "pyram": "Pyraminx",
    "clock": "Clock",
    "skewb": "Skewb",
    "sq1": "Square-1",
    "444bf": "4x4x4 Blindfolded",
    "555bf": "5x5x5 Blindfolded",
    "333mbf": "3x3x3 Multi-Blind",
}

# Dark email theme (matches WCA Live–style digest cards).
_EMAIL_BG = "#181818"
_EMAIL_CARD = "#242424"
_EMAIL_CARD_BORDER = "#2e2e2e"
_EMAIL_TEXT = "#ffffff"
_EMAIL_MUTED = "#9e9e9e"
_EMAIL_MUTED2 = "#757575"
_EMAIL_LINK = "#90caf9"
_EMAIL_WR_BG = "#E53935"
_EMAIL_WR_FG = "#ffffff"
_EMAIL_CR_BG = "#FDD835"
_EMAIL_CR_FG = "#111111"
_EMAIL_NR_BG = "#1E88E5"
_EMAIL_NR_FG = "#ffffff"
_EMAIL_BADGE_NEUTRAL_BG = "#424242"
_EMAIL_BADGE_NEUTRAL_FG = "#eeeeee"
_EMAIL_FONT = (
    "system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
)


def _digest_event_label(event_id: str) -> str:
    return EVENT_DIGEST_LABELS.get(event_id, EVENT_NAMES.get(event_id, event_id))


# Common ISO2 → English country name (for “from …” sublines). Fallback: ISO2 code.
_ISO2_EN = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AM": "Armenia", "AR": "Argentina",
    "AT": "Austria", "AU": "Australia", "AZ": "Azerbaijan", "BE": "Belgium", "BR": "Brazil",
    "BY": "Belarus", "CA": "Canada", "CH": "Switzerland", "CL": "Chile", "CN": "China",
    "CO": "Colombia", "CR": "Costa Rica", "CZ": "Czech Republic", "DE": "Germany",
    "DK": "Denmark", "DO": "Dominican Republic", "EC": "Ecuador", "EE": "Estonia",
    "EG": "Egypt", "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom",
    "GR": "Greece", "GT": "Guatemala", "HK": "Hong Kong", "HN": "Honduras", "HR": "Croatia",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India",
    "IQ": "Iraq", "IR": "Iran", "IS": "Iceland", "IT": "Italy", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KR": "South Korea", "KW": "Kuwait", "KZ": "Kazakhstan",
    "LB": "Lebanon", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MA": "Morocco",
    "MC": "Monaco", "MD": "Moldova", "MK": "North Macedonia", "MX": "Mexico", "MY": "Malaysia",
    "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands", "NO": "Norway", "NZ": "New Zealand",
    "PA": "Panama", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland",
    "PR": "Puerto Rico", "PT": "Portugal", "PY": "Paraguay", "QA": "Qatar", "RO": "Romania",
    "RS": "Serbia", "RU": "Russia", "SA": "Saudi Arabia", "SE": "Sweden", "SG": "Singapore",
    "SI": "Slovenia", "SK": "Slovakia", "SV": "El Salvador", "TH": "Thailand", "TN": "Tunisia",
    "TR": "Turkey", "TW": "Taiwan", "UA": "Ukraine", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VE": "Venezuela", "VN": "Vietnam", "ZA": "South Africa",
}


def _country_line(iso2: str | None) -> str:
    if not iso2 or not str(iso2).strip():
        return ""
    code = str(iso2).strip().upper()
    name = _ISO2_EN.get(code, code)
    return f" from {name}"


def _badge_colors_for_tag(tag: str) -> tuple[str, str]:
    t = (tag or "").strip().upper()
    if t in ("", "—", "NONE"):
        return _EMAIL_BADGE_NEUTRAL_BG, _EMAIL_BADGE_NEUTRAL_FG
    if t == "WR":
        return _EMAIL_WR_BG, _EMAIL_WR_FG
    if t == "NR":
        return _EMAIL_NR_BG, _EMAIL_NR_FG
    if t in ("CR", "ER", "NAR", "SAR", "ASR", "OCR", "AFR", "OcR"):
        return _EMAIL_CR_BG, _EMAIL_CR_FG
    if len(t) <= 4 and t.endswith("R"):
        return _EMAIL_CR_BG, _EMAIL_CR_FG
    return _EMAIL_BADGE_NEUTRAL_BG, _EMAIL_BADGE_NEUTRAL_FG


def _badge_markup(tag: str) -> str:
    t = escape((tag or "").strip().upper() or "·")
    bg, fg = _badge_colors_for_tag(tag)
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};font-weight:700;'
        f'font-size:11px;letter-spacing:0.06em;padding:6px 10px;border-radius:4px;">{t}</span>'
    )


def _pick_primary_regional_tag(rs: object, ra: object) -> str:
    for label in (ra, rs):
        s = (str(label).strip() if label is not None else "")
        if s and s.upper() not in ("—", "NONE", ""):
            return s.upper()
    return ""


def _email_link(href: str, text: str) -> str:
    t = escape(text)
    return (
        f'<a href="{escape(href)}" style="color:{_EMAIL_LINK};text-decoration:none;'
        f'border-bottom:1px solid {_EMAIL_MUTED2};">{t}</a>'
    )


def _email_dark_document(
    *,
    title: str,
    subtitle: str | None,
    body_html: str,
    footer_html: str | None = None,
) -> str:
    sub = (
        f'<p style="margin:0 0 20px 0;color:{_EMAIL_MUTED};font-size:14px;line-height:1.5;">'
        f"{subtitle}</p>"
        if subtitle
        else ""
    )
    foot = footer_html or ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:{_EMAIL_BG};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="background:{_EMAIL_BG};border-collapse:collapse;">
<tr><td align="center" style="padding:28px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="max-width:640px;border-collapse:collapse;font-family:{_EMAIL_FONT};">
<tr><td>
<h1 style="margin:0 0 8px 0;color:{_EMAIL_TEXT};font-size:22px;font-weight:700;
 letter-spacing:-0.02em;line-height:1.25;">{escape(title)}</h1>
{sub}
{body_html}
{foot}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _record_row_table(badge_inner: str, line1: str, line2: str) -> str:
    return f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="margin:10px 0;background:{_EMAIL_CARD};border-radius:8px;border:1px solid {_EMAIL_CARD_BORDER};
 border-collapse:separate;">
<tr><td style="padding:14px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr>
<td valign="middle" style="width:1%;padding:0 14px 0 0;white-space:nowrap;">{badge_inner}</td>
<td valign="middle" style="padding:0;">
<div style="color:{_EMAIL_TEXT};font-size:15px;line-height:1.4;font-weight:500;">{line1}</div>
<div style="color:{_EMAIL_MUTED};font-size:13px;margin-top:6px;line-height:1.45;">{line2}</div>
</td>
</tr>
</table>
</td></tr>
</table>"""


def _section_heading(text: str) -> str:
    return (
        f'<h2 style="margin:28px 0 12px 0;color:{_EMAIL_TEXT};font-size:16px;font-weight:700;'
        f'letter-spacing:0.02em;border-bottom:1px solid {_EMAIL_CARD_BORDER};padding-bottom:8px;">'
        f"{escape(text)}</h2>"
    )


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
        return (
            f'<p style="color:{_EMAIL_MUTED};margin:0;">No competitions with watched competitors.</p>'
        )

    parts: list[str] = []

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

        comp_link = _email_link(comp_url, comp_name)
        country_name = _ISO2_EN.get(str(country).strip().upper(), (country or "").strip())
        country_part = f'<span style="color:{_EMAIL_MUTED};font-weight:400;"> · {escape(country_name)}</span>' if country else ""
        date_muted = f'<span style="color:{_EMAIL_MUTED};font-weight:400;">{escape(date_str)}</span>'
        parts.append(
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px 0;'
            f'background:{_EMAIL_CARD};border-radius:8px;border:1px solid {_EMAIL_CARD_BORDER};'
            f'border-collapse:separate;">'
            f'<tr><td style="padding:16px 18px;">'
            f'<div style="font-size:16px;font-weight:700;line-height:1.35;margin-bottom:12px;color:{_EMAIL_TEXT};">'
            f"{comp_link}{country_part}</div>"
            f'<div style="color:{_EMAIL_MUTED};font-size:13px;margin-bottom:14px;">{date_muted}</div>'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">'
        )

        def event_sort_key(item: tuple) -> tuple:
            (eid, rd), (time_str, _) = item
            dt = _parse_round_time(time_str, comp_start)
            return (dt if dt else datetime.max, eid, rd)

        for i, ((eid, rd), (time_str, names)) in enumerate(
            sorted(event_rounds.items(), key=event_sort_key),
        ):
            ev = EVENT_NAMES.get(eid, eid)
            label = escape(f"{ev} {rd}".strip() if rd else ev)
            time_esc = escape(time_str) if time_str else ""
            comps = escape(", ".join(names))
            time_row = (
                f'<div style="color:{_EMAIL_MUTED};font-size:12px;margin-bottom:4px;">{time_esc}</div>'
                if time_esc
                else ""
            )
            top_rule = "" if i == 0 else f"border-top:1px solid {_EMAIL_CARD_BORDER};"
            parts.append(
                f'<tr><td style="padding:12px 0;{top_rule}">'
                f"{time_row}"
                f'<div style="color:{_EMAIL_TEXT};font-size:14px;font-weight:600;">{label}</div>'
                f'<div style="color:{_EMAIL_MUTED};font-size:13px;margin-top:4px;">{comps}</div>'
                f"</td></tr>"
            )

        parts.append("</table></td></tr></table>")

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


def _wca_person_url(wca_id: str) -> str:
    return f"https://www.worldcubeassociation.org/persons/{wca_id}"


def _format_result_value(event_id: str, raw: object) -> str:
    """Human-readable attempt/result (centiseconds, MBLD solved count, DNF/DNS)."""
    if raw is None:
        return "—"
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return str(raw).strip() or "—"
    if v == -1:
        return "DNF"
    if v == -2:
        return "DNS"
    if event_id == "333mbf":
        from .results_format import mbld_solved_count

        sc = mbld_solved_count(v)
        return f"{sc} solved" if sc is not None else str(v)
    return _format_centiseconds(event_id, str(v))


def _live_average_attempts_note(event_id: str, typ: str, attempts: object) -> str:
    if (typ or "").lower() != "average":
        return ""
    if not isinstance(attempts, list) or not attempts:
        return ""
    inner = ", ".join(_format_result_value(event_id, x) for x in attempts)
    return f' <span style="color:{_EMAIL_MUTED2};font-size:0.92em;">({inner})</span>'


def _competition_attempts_note(event_id: str, attempts: object) -> str:
    if not isinstance(attempts, list) or not attempts:
        return ""
    if event_id == "333mbf":
        vals = [a for a in attempts if a not in (None, 0)]
    else:
        vals = list(attempts)
    if not vals:
        return ""
    inner = ", ".join(_format_result_value(event_id, x) for x in vals)
    return f' <span style="color:{_EMAIL_MUTED2};font-size:0.92em;">[{inner}]</span>'


def format_records_alert(
    live_records: list[dict],
    competition_results: list[dict],
    *,
    timezone: str = "America/Vancouver",
) -> str:
    """HTML for daily ``wca records`` (WCA Live + new competition result rows)."""
    from .results_format import mbld_solved_count

    body_parts: list[str] = [
        f'<p style="margin:0 0 16px 0;color:{_EMAIL_MUTED};font-size:14px;line-height:1.55;">'
        f"Times and schedule notes use <strong>{escape(_tz_display(timezone))}</strong> where relevant. "
        f"Per event: <strong>WR</strong>, optional continental tags, <strong>sub</strong> single/average, "
        f"or <strong>sup</strong> MBLD — combined with <strong>OR</strong>. Default is WR-only.</p>"
    ]

    if live_records:
        body_parts.append(_section_heading("WCA Live"))
        for r in live_records:
            eid = r.get("event_id") or ""
            ev_title = escape(_digest_event_label(eid))
            tag_raw = (r.get("tag") or "").strip()
            kind_raw = (r.get("type") or "").lower()
            kind_phrase = "average of" if kind_raw == "average" else "single of"
            wca_id = (r.get("wca_id") or "").strip()
            name_full = escape((r.get("name") or wca_id or "").strip())
            val_disp = escape(_format_result_value(eid, r.get("attempt_result")))
            attempts_note = _live_average_attempts_note(eid, r.get("type") or "", r.get("attempts"))
            country_s = _country_line(r.get("country_iso2"))
            line1 = (
                f'{ev_title} {kind_phrase} <strong style="color:{_EMAIL_TEXT};">{val_disp}</strong>'
                f"{attempts_note}"
            )
            line2 = (
                f"{name_full}{country_s} · "
                f'{_email_link(_wca_person_url(wca_id), wca_id)}'
            )
            body_parts.append(_record_row_table(_badge_markup(tag_raw), line1, line2))

    if competition_results:
        body_parts.append(_section_heading("Competition results (published)"))
        for r in competition_results:
            eid = r.get("event_id") or ""
            ev_title = escape(_digest_event_label(eid))
            cid = r.get("competition_id") or ""
            wca_id = (r.get("wca_id") or "").strip()
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
                b_disp = _format_result_value(eid, r.get("best"))
                a_disp = _format_result_value(eid, r.get("average"))
            attempts_note = _competition_attempts_note(eid, r.get("attempts"))
            badge_tag = _pick_primary_regional_tag(rs, ra)
            comp_page = f"https://www.worldcubeassociation.org/competitions/{cid}"
            line1 = (
                f'{_email_link(comp_page, cid)} — {ev_title}: '
                f'single <strong style="color:{_EMAIL_TEXT};">{escape(str(b_disp))}</strong>'
                f' · avg <strong style="color:{_EMAIL_TEXT};">{escape(str(a_disp))}</strong>'
                f"{attempts_note}"
            )
            name_full = escape((r.get("name") or wca_id or "").strip())
            iso2 = r.get("country_iso2")
            meta = (
                f'<span style="color:{_EMAIL_MUTED2};">records · single {escape(str(rs))} · '
                f"avg {escape(str(ra))}</span>"
            )
            line2 = f"{name_full}{_country_line(iso2)} · {_email_link(_wca_person_url(wca_id), wca_id)} · {meta}"
            body_parts.append(_record_row_table(_badge_markup(badge_tag), line1, line2))

    if not live_records and not competition_results:
        body_parts.append(
            f'<p style="color:{_EMAIL_MUTED};margin:8px 0 0 0;">No new items matched your rules in this check.</p>'
        )

    return _email_dark_document(
        title="WCA daily digest",
        subtitle=f"Timezone: {_tz_display(timezone)}",
        body_html="\n".join(body_parts),
    )


def format_psych_sheet_email(
    psych_results: list[dict],
    *,
    timezone: str,
) -> str:
    """Full dark-mode HTML email for ``wca notify`` (psych sheet only)."""
    inner = format_results_by_week(psych_results, timezone=timezone)
    return _email_dark_document(
        title="WCA — watched competitors",
        subtitle=f"Round times in {_tz_display(timezone)}",
        body_html=inner,
    )


def format_weekly_digest(
    psych_results: list[dict],
    *,
    timezone: str,
) -> str:
    """Combined HTML for ``wca weekly`` (upcoming psych sheet only)."""
    psych_block = format_results_by_week(psych_results, timezone=timezone)
    footer = (
        f'<p style="margin:24px 0 0 0;color:{_EMAIL_MUTED};font-size:13px;line-height:1.5;">'
        f'Daily result digests: run <code style="background:{_EMAIL_CARD};color:{_EMAIL_TEXT};'
        f'padding:2px 6px;border-radius:4px;font-size:12px;">wca records</code> on your schedule.'
        f"</p>"
    )
    body = f'{_section_heading("Watched competitors")}\n{psych_block}\n{footer}'
    return _email_dark_document(
        title="WCA weekly — upcoming psych sheet",
        subtitle=f"Round times in {_tz_display(timezone)}",
        body_html=body,
    )


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
