"""Email notifications for watched competitors at upcoming competitions."""

import contextlib
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

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

# Dark email theme (matches WCA Live-style digest cards).
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
    "system-ui,-apple-system,'Segoe UI',Roboto,"
    "'Helvetica Neue',Arial,sans-serif"
)


def _digest_event_label(event_id: str) -> str:
    return EVENT_DIGEST_LABELS.get(
        event_id, EVENT_NAMES.get(event_id, event_id)
    )


# Common ISO2 → English country name (for "from …" sublines).
# Fallback: ISO2 code.
_ISO2_EN = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AM": "Armenia",
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "AZ": "Azerbaijan",
    "BE": "Belgium",
    "BR": "Brazil",
    "BY": "Belarus",
    "CA": "Canada",
    "CH": "Switzerland",
    "CL": "Chile",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "DO": "Dominican Republic",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "GT": "Guatemala",
    "HK": "Hong Kong",
    "HN": "Honduras",
    "HR": "Croatia",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KZ": "Kazakhstan",
    "LB": "Lebanon",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "MK": "North Macedonia",
    "MX": "Mexico",
    "MY": "Malaysia",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PA": "Panama",
    "PE": "Peru",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PR": "Puerto Rico",
    "PT": "Portugal",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "SA": "Saudi Arabia",
    "SE": "Sweden",
    "SG": "Singapore",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "SV": "El Salvador",
    "TH": "Thailand",
    "TN": "Tunisia",
    "TR": "Turkey",
    "TW": "Taiwan",
    "UA": "Ukraine",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "ZA": "South Africa",
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
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f"font-weight:700;font-size:11px;letter-spacing:0.06em;"
        f'padding:6px 10px;border-radius:4px;">{t}</span>'
    )


def _pick_primary_regional_tag(rs: object, ra: object) -> str:
    for label in (ra, rs):
        s = str(label).strip() if label is not None else ""
        if s and s.upper() not in ("—", "NONE", ""):
            return s.upper()
    return ""


def _email_link(href: str, text: str) -> str:
    t = escape(text)
    return (
        f'<a href="{escape(href)}" '
        f'style="color:{_EMAIL_LINK};text-decoration:none;'
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
        f'<p style="margin:0 0 20px 0;color:{_EMAIL_MUTED};'
        f'font-size:14px;line-height:1.5;">'
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
    return (
        f'<table role="presentation" width="100%" cellspacing="0"'
        f' cellpadding="0"\n'
        f' style="margin:10px 0;background:{_EMAIL_CARD};border-radius:8px;'
        f"border:1px solid {_EMAIL_CARD_BORDER};\n"
        f' border-collapse:separate;">\n'
        f'<tr><td style="padding:14px 16px;">\n'
        f'<table role="presentation" width="100%" cellspacing="0"'
        f' cellpadding="0">\n'
        f"<tr>\n"
        f'<td valign="middle"'
        f' style="width:1%;padding:0 14px 0 0;white-space:nowrap;">'
        f"{badge_inner}</td>\n"
        f'<td valign="middle" style="padding:0;">\n'
        f'<div style="color:{_EMAIL_TEXT};font-size:15px;line-height:1.4;'
        f'font-weight:500;">{line1}</div>\n'
        f'<div style="color:{_EMAIL_MUTED};font-size:13px;margin-top:6px;'
        f'line-height:1.45;">{line2}</div>\n'
        f"</td>\n"
        f"</tr>\n"
        f"</table>\n"
        f"</td></tr>\n"
        f"</table>"
    )


def _section_heading(text: str) -> str:
    return (
        f'<h2 style="margin:28px 0 12px 0;color:{_EMAIL_TEXT};'
        f"font-size:16px;font-weight:700;letter-spacing:0.02em;"
        f"border-bottom:1px solid {_EMAIL_CARD_BORDER};"
        f'padding-bottom:8px;">'
        f"{escape(text)}</h2>"
    )


def _strip_parens(s: str) -> str:
    """Remove parenthetical content e.g. 'Name (本地名)' -> 'Name'."""
    return re.sub(r"\s*\([^)]*\)", "", s).strip()


def _strip_year(name: str) -> str:
    """Remove trailing 4-digit year: 'Dallas Open 2026' -> 'Dallas Open'."""
    return re.sub(r"\s+20\d{2}\s*$", "", name.strip()).strip()


def _short_date_range(start: str, end: str) -> str:
    """'2026-05-24', '2026-05-25' -> 'May 24-25'; single day -> 'May 24'."""
    if not start:
        return ""
    try:
        d0 = datetime.strptime(start.strip(), "%Y-%m-%d")
        if not end or start == end:
            return d0.strftime("%b %-d")
        d1 = datetime.strptime(end.strip(), "%Y-%m-%d")
        if d0.month == d1.month:
            return f"{d0.strftime('%b %-d')}-{d1.day}"
        return f"{d0.strftime('%b %-d')}-{d1.strftime('%b %-d')}"
    except ValueError:
        return start if start == end else f"{start}-{end}"


def _tz_display(tz: str) -> str:
    """Human-readable timezone for header."""
    m = {
        "America/Los_Angeles": "PST",
        "America/New_York": "EST",
        "America/Chicago": "CST",
    }
    return m.get(tz, tz.split("/")[-1].replace("_", " "))


def _format_centiseconds(event_id: str, raw: str) -> str:
    """Human-readable result from TSV ``best`` / ``average``.

    Values are centiseconds for most speedsolving events.
    """
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


def _format_result_value(event_id: str, raw: Any) -> str:
    """Human-readable attempt/result (centiseconds, MBLD X/Y M:SS, DNF/DNS)."""
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
        from .results_format import mbld_format

        return mbld_format(v)
    return _format_centiseconds(event_id, str(v))


def _live_average_attempts_note(
    event_id: str, typ: str, attempts: object
) -> str:
    if (typ or "").lower() != "average":
        return ""
    if not isinstance(attempts, list) or not attempts:
        return ""
    inner = ", ".join(_format_result_value(event_id, x) for x in attempts)
    return (
        f' <span style="color:{_EMAIL_MUTED2};font-size:0.92em;">'
        f"({inner})</span>"
    )


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
    return (
        f' <span style="color:{_EMAIL_MUTED2};font-size:0.92em;">'
        f"[{inner}]</span>"
    )


def format_records_alert(
    live_records: list[dict[str, Any]],
    competition_results: list[dict[str, Any]],
    *,
    timezone: str = "America/Vancouver",
) -> str:
    """HTML for daily ``wca records`` (WCA Live + new result rows)."""
    from .results_format import mbld_format

    body_parts: list[str] = []

    if live_records:
        body_parts.append(_section_heading("WCA Live"))
        for r in live_records:
            eid = r.get("event_id") or ""
            ev_title = escape(_digest_event_label(eid))
            tag_raw = (r.get("tag") or "").strip()
            kind_raw = (r.get("type") or "").lower()
            kind_phrase = "average" if kind_raw == "average" else "single"
            wca_id = (r.get("wca_id") or "").strip()
            name_raw = (r.get("name") or wca_id or "").strip()
            val_disp = escape(
                _format_result_value(eid, r.get("attempt_result"))
            )
            attempts_note = _live_average_attempts_note(
                eid, r.get("type") or "", r.get("attempts")
            )
            iso2 = r.get("country_iso2") or ""
            country_s = escape(
                _ISO2_EN.get(str(iso2).strip().upper(), iso2.strip())
                if iso2
                else ""
            )
            line1 = (
                f'<strong style="color:{_EMAIL_TEXT};font-size:16px;">'
                f"{escape(name_raw)}</strong>"
                f" &mdash; {ev_title} {kind_phrase}"
                f' <strong style="color:{_EMAIL_TEXT};">{val_disp}</strong>'
                f"{attempts_note}"
            )
            meta = (
                country_s
                + (" · " if country_s else "")
                + _email_link(_wca_person_url(wca_id), wca_id)
            )
            body_parts.append(
                _record_row_table(_badge_markup(tag_raw), line1, meta)
            )

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
                b_raw = r.get("best")
                try:
                    b_enc = (
                        int(b_raw)
                        if b_raw is not None and b_raw != ""
                        else None
                    )
                except (TypeError, ValueError):
                    b_enc = None
                b_disp = mbld_format(b_enc) if b_enc else str(b_raw or "—")
                a_disp = None  # MBLD has no meaningful average
            else:
                b_disp = _format_result_value(eid, r.get("best"))
                a_disp = _format_result_value(eid, r.get("average"))
            attempts_note = _competition_attempts_note(eid, r.get("attempts"))
            badge_tag = _pick_primary_regional_tag(rs, ra)
            comp_page = (
                f"https://www.worldcubeassociation.org/competitions/{cid}"
            )
            name_raw = (r.get("name") or wca_id or "").strip()
            iso2 = r.get("country_iso2") or ""
            country_s = escape(
                _ISO2_EN.get(str(iso2).strip().upper(), iso2.strip())
                if iso2
                else ""
            )

            if eid == "333mbf":
                result_str = (
                    f'{ev_title}: <strong style="color:{_EMAIL_TEXT};">'
                    f"{escape(str(b_disp))}</strong>"
                    f"{attempts_note}"
                )
            else:
                result_str = (
                    f'{ev_title}: single <strong style="color:{_EMAIL_TEXT};">'
                    f"{escape(str(b_disp))}</strong>"
                    f' · avg <strong style="color:{_EMAIL_TEXT};">'
                    f"{escape(str(a_disp))}</strong>"
                    f"{attempts_note}"
                )
            line1 = (
                f'<strong style="color:{_EMAIL_TEXT};font-size:16px;">'
                f"{escape(name_raw)}</strong>"
                f" &mdash; {result_str}"
            )
            rec_meta = (
                f'<span style="color:{_EMAIL_MUTED2};">'
                f"records · single {escape(str(rs))} · "
                f"avg {escape(str(ra))}</span>"
            )
            line2_parts = [_email_link(comp_page, cid)]
            if country_s:
                line2_parts.append(country_s)
            line2_parts.append(_email_link(_wca_person_url(wca_id), wca_id))
            line2_parts.append(rec_meta)
            line2 = " · ".join(line2_parts)
            body_parts.append(
                _record_row_table(_badge_markup(badge_tag), line1, line2)
            )

    if not live_records and not competition_results:
        body_parts.append(
            f'<p style="color:{_EMAIL_MUTED};margin:8px 0 0 0;">'
            f"No new items matched your rules in this check.</p>"
        )

    return _email_dark_document(
        title="WCA daily digest",
        subtitle=None,
        body_html="\n".join(body_parts),
    )


def _parse_round_time(time_str: str, comp_start: str) -> datetime | None:
    if not time_str:
        return None
    year = (comp_start or "")[:4] or "2026"
    try:
        return datetime.strptime(f"{year} {time_str}", "%Y %a %b %d, %I:%M %p")
    except ValueError:
        return None


def _get_earliest_dt(r: dict[str, Any]) -> datetime:
    comp_start = r["comp"].get("start_date") or ""
    fallback = datetime.max
    with contextlib.suppress(ValueError, TypeError):
        fallback = datetime.strptime(comp_start or "9999-12-31", "%Y-%m-%d")
    earliest = datetime.max
    for p in r["watched_competitors"]:
        for s in p.get("schedule", []):
            time_str = s.get("end_local") or s.get("start_local") or ""
            dt = _parse_round_time(time_str, comp_start)
            if dt:
                earliest = min(earliest, dt)
    return earliest if earliest != datetime.max else fallback


def _collect_event_labels(watched: list[dict[str, Any]]) -> list[str]:
    """Ordered unique event labels across all watched competitors."""
    all_event_ids: list[str] = []
    seen_eids: set[str] = set()
    for m in watched:
        for eid in m.get("events", []):
            if eid not in seen_eids:
                seen_eids.add(eid)
                all_event_ids.append(eid)
    return [EVENT_NAMES.get(e, e) for e in sorted(all_event_ids)]


def _build_event_rounds(
    watched: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[str, list[str]]]:
    """Map (event_id, round) → (time_str, [competitor names])."""
    event_rounds: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for p in watched:
        name = _strip_parens(p.get("name", "Unknown"))
        schedule = p.get("schedule", [])
        if schedule:
            for s in schedule:
                rd = s.get("name", "")
                key = (s["event_id"], rd)
                time_str = s.get("end_local") or s.get("start_local") or ""
                if key not in event_rounds:
                    event_rounds[key] = (time_str, [])
                if name not in event_rounds[key][1]:
                    event_rounds[key][1].append(name)
        else:
            for eid in p.get("events", []):
                key = (eid, "")
                if key not in event_rounds:
                    event_rounds[key] = ("", [])
                if name not in event_rounds[key][1]:
                    event_rounds[key][1].append(name)
    return event_rounds


def _build_summary_html(
    watched: list[dict[str, Any]],
    comp_url: str,
    display_name: str,
    date_label: str,
) -> str:
    """Build the collapsed <summary> one-liner for a competition block."""
    all_names = [_strip_parens(m.get("name", "Unknown")) for m in watched]
    names_html = (
        f'<strong style="color:{_EMAIL_TEXT};font-size:14px;">'
        + escape(", ".join(all_names))
        + "</strong>"
    )
    comp_link = _email_link(comp_url, display_name)
    events_str = escape(", ".join(_collect_event_labels(watched)))
    return (
        f"{names_html}"
        f'<span style="color:{_EMAIL_MUTED};"> &nbsp;·&nbsp; {comp_link}'
        f" &nbsp;·&nbsp; {events_str}"
        + (f" &nbsp;·&nbsp; {escape(date_label)}" if date_label else "")
        + "</span>"
    )


def _build_round_row(
    eid: str,
    rd: str,
    time_str: str,
    names: list[str],
    *,
    is_first: bool,
    tz_note: str,
) -> str:
    """Build one per-round row inside the expanded detail view."""
    ev = EVENT_NAMES.get(eid, eid)
    label = escape(f"{ev} {rd}".strip() if rd else ev)
    comps = escape(", ".join(names))
    top_rule = "" if is_first else f"border-top:1px solid {_EMAIL_CARD_BORDER};"
    if time_str:
        time_html = (
            f'<div style="color:{_EMAIL_MUTED};font-size:12px;'
            f'margin-bottom:3px;">'
            f"{escape(time_str)}{tz_note}</div>"
        )
    else:
        time_html = ""
    return (
        f'<div style="padding:10px 0;{top_rule}">'
        f"{time_html}"
        f'<span style="color:{_EMAIL_TEXT};font-size:13px;font-weight:600;">'
        f"{label}</span>"
        f'<span style="color:{_EMAIL_MUTED};font-size:13px;">'
        f" &mdash; {comps}</span>"
        f"</div>"
    )


def _build_expanded_html(
    watched: list[dict[str, Any]],
    country: str,
    comp_start: str,
    event_rounds: dict[tuple[str, str], tuple[str, list[str]]],
    timezone: str,
) -> str:
    """Build the expanded detail body: country plus per-round rows."""
    exp_parts: list[str] = []

    country_name = _ISO2_EN.get(
        str(country).strip().upper(), (country or "").strip()
    )
    if country_name:
        exp_parts.append(
            f'<div style="color:{_EMAIL_MUTED};font-size:13px;'
            f'margin-bottom:10px;">'
            f"{escape(country_name)}</div>"
        )

    def _event_sort_key(item: tuple[Any, ...]) -> tuple[Any, ...]:
        (eid, rd), (time_str, _) = item
        dt = _parse_round_time(time_str, comp_start)
        return (dt if dt else datetime.max, eid, rd)

    has_schedule = any(
        time_str for (_, _), (time_str, _) in event_rounds.items()
    )
    tz_note = (
        f' <span style="color:{_EMAIL_MUTED2};font-size:12px;">'
        f"({_tz_display(timezone)})</span>"
        if has_schedule
        else ""
    )

    for i, ((eid, rd), (time_str, names)) in enumerate(
        sorted(event_rounds.items(), key=_event_sort_key)
    ):
        exp_parts.append(
            _build_round_row(
                eid,
                rd,
                time_str,
                names,
                is_first=(i == 0),
                tz_note=tz_note,
            )
        )

    return "".join(exp_parts)


def _build_comp_block(r: dict[str, Any], timezone: str) -> str:
    """Build the full <details> block for one competition."""
    comp = r["comp"]
    comp_url = r["comp_url"]
    country = comp.get("country_iso2", "")
    comp_start = comp.get("start_date") or ""
    comp_end = comp.get("end_date") or comp_start

    display_name = _strip_year(r["comp_name"])
    date_label = _short_date_range(comp_start, comp_end)
    watched = r["watched_competitors"]

    event_rounds = _build_event_rounds(watched)
    summary_html = _build_summary_html(
        watched, comp_url, display_name, date_label
    )
    expanded_html = _build_expanded_html(
        watched, country, comp_start, event_rounds, timezone
    )

    return (
        f'<details style="margin:0 0 8px 0;background:{_EMAIL_CARD};'
        f"border-radius:8px;"
        f'border:1px solid {_EMAIL_CARD_BORDER};">'
        f'<summary style="padding:12px 16px;cursor:pointer;list-style:none;'
        f"font-family:{_EMAIL_FONT};font-size:14px;line-height:1.45;"
        f'-webkit-appearance:none;outline:none;">'
        f"{summary_html}"
        f"</summary>"
        + (
            f'<div style="padding:2px 16px 14px 16px;'
            f'border-top:1px solid {_EMAIL_CARD_BORDER};">'
            f"{expanded_html}</div>"
            if expanded_html
            else ""
        )
        + "</details>"
    )


def format_results_by_week(
    results: list[dict[str, Any]],
    *,
    timezone: str = "America/Los_Angeles",
) -> str:
    """Expandable competition blocks with per-round schedule.

    Each competition renders as a compact ``<details><summary>`` one-liner that
    expands to a per-round schedule.

    Summary: competitor names (bold) · comp name (linked, year stripped) ·
    events · dates. Expanded: country, per-round times and competitor list.
    """
    if not results:
        return (
            f'<p style="color:{_EMAIL_MUTED};margin:0;">'
            f"No competitions with watched competitors.</p>"
        )

    parts = [
        _build_comp_block(r, timezone)
        for r in sorted(
            results, key=lambda r: (_get_earliest_dt(r), r["comp_name"])
        )
    ]
    return "\n".join(parts)


def format_psych_sheet_email(
    psych_results: list[dict[str, Any]],
    *,
    timezone: str,
) -> str:
    """Full dark-mode HTML email for ``wca notify`` (psych sheet only)."""
    inner = format_results_by_week(psych_results, timezone=timezone)
    return _email_dark_document(
        title="WCA — watched competitors",
        subtitle=f"Round times in {_tz_display(timezone)} (click to expand)",
        body_html=inner,
    )


def format_weekly_digest(
    psych_results: list[dict[str, Any]],
    *,
    timezone: str,
) -> str:
    """Combined HTML for ``wca weekly`` (upcoming psych sheet only)."""
    psych_block = format_results_by_week(psych_results, timezone=timezone)
    footer = (
        f'<p style="margin:24px 0 0 0;color:{_EMAIL_MUTED};'
        f'font-size:13px;line-height:1.5;">'
        f"Daily result digests: run "
        f'<code style="background:{_EMAIL_CARD};color:{_EMAIL_TEXT};'
        f'padding:2px 6px;border-radius:4px;font-size:12px;">'
        f"wca records</code> on your schedule."
        f"</p>"
    )
    body = f"{_section_heading('Watched competitors')}\n{psych_block}\n{footer}"
    return _email_dark_document(
        title="WCA weekly — upcoming psych sheet",
        subtitle=(
            f"Round times in {_tz_display(timezone)} · click each row to expand"
        ),
        body_html=body,
    )


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
) -> None:
    """Send an email notification.

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
