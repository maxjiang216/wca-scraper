# WCA Psych Sheet Notifier

Email notifications when watched competitors are registered for upcoming WCA competitions.

## Project structure

```
src/wca-org/
├── wca_api.py         # WCA v0 API client (competitions, WCIF)
├── notify.py          # Email formatting and sending
└── psych_sheet_notifier.py
```

## How it works

Scrapes the WCA website via its public API to get psych sheets for upcoming competitions. Runs weekly and sends an email when any **watched competitors** (by WCA ID) are registered and competing.

1. **Fetch upcoming competitions** from the [WCA v0 API](https://www.worldcubeassociation.org/api/v0/competitions)
2. **Get psych sheet (WCIF)** for each competition from `/api/v0/competitions/{id}/wcif/public`
3. **Check watch list** – for each competitor on your list (by WCA ID per event), if they’re competing in that event at a competition, add it to the notification
4. **Send email** when any watched competitors are found

No scraping and no auth: the WCA public API is used as intended.

## Setup

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
cp watch_list.example.json watch_list.json
# Edit watch_list.json with WCA IDs per event
```

Or with pip:

```bash
pip install -e .
cp watch_list.example.json watch_list.json
```

### Watch list (watch_list.json)

WCA IDs to watch per event:

```json
{
  "333": ["2010LEAR01", "2017PARK03"],
  "444": ["2010LEAR01"],
  "333oh": ["2017PARK03"]
}
```

If a competitor on the list is registered and competing in that event at an upcoming competition, you get notified.

### CLI flags

| Flag | Description |
|------|-------------|
| `-w, --watch-list` | Path to watch_list.json (default: watch_list.json) |
| `-e, --email` | Email to receive notifications |
| `-s, --start` | Start date (YYYY-MM-DD). Default: today |
| `--end` | End date (YYYY-MM-DD). Omit to use start + --weeks (default 2 weeks). |
| `-c, --country` | Filter by country ISO2 (e.g. US) or comma-separated list |
| `--weeks` | How many weeks ahead to fetch (default: 2). Used when --end not specified. |
| `--rate-limit-delay` | Seconds between WCIF API requests (default: 1.0). Increase if hitting 429. |
| `-z, --timezone` | Timezone for schedule times (default: America/Los_Angeles = PST). IANA name. |
| `--dry-run` | Print what would be sent, do not email |
| `--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-password` | SMTP settings |

### Gmail Setup

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833), not your regular password:

1. Enable 2FA on your Google account  
2. Create an App Password for "Mail"  
3. Pass it via `--smtp-user` and `--smtp-password`

## Usage

```bash
# Dry run (no email)
uv run python psych_sheet_notifier.py -w watch_list.json --dry-run

# Send email
uv run python psych_sheet_notifier.py -w watch_list.json -e you@example.com --smtp-user ... --smtp-password ...

# Date range: next 6 weeks (or use --end for explicit range)
uv run python psych_sheet_notifier.py -w watch_list.json -e you@example.com --weeks 6

# Rate limiting: if you hit 429 errors, increase delay between API requests
uv run python psych_sheet_notifier.py -w watch_list.json --dry-run --rate-limit-delay 1.5

# Custom weeks ahead (default 2)
uv run python psych_sheet_notifier.py -w watch_list.json --dry-run --weeks 6

# Full help
uv run python psych_sheet_notifier.py --help
```

## GitHub Actions (Weekly on Tuesdays)

1. **Push the repo to GitHub** (or create a new repo and push).

2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `WATCH_LIST` — Your watch list as a single-line JSON string, e.g.  
     `{"333":["2010LEAR01"],"444":["2010LEAR01"]}`
   - `NOTIFY_EMAIL` — Email address to receive notifications
   - `SMTP_USER` — SMTP username (e.g. your Gmail address)
   - `SMTP_PASSWORD` — SMTP password (Gmail: use an [App Password](https://support.google.com/accounts/answer/185833))

3. **Run**: The workflow in `.github/workflows/weekly-notify.yml` runs every **Wednesday at 00:00 UTC**. You can also trigger it manually from the Actions tab.

**Optional:** For non-Gmail SMTP, add `SMTP_HOST` and `SMTP_PORT` secrets and update the workflow's `Run notifier` step to pass `--smtp-host` and `--smtp-port`.

## Weekly Schedule (Local)

### Cron (Linux/macOS)

```bash
crontab -e
```

Add a line to run every Sunday at 9am:

```
0 9 * * 0 cd /path/to/wca_scraper && uv run python psych_sheet_notifier.py -w watch_list.json -e you@example.com >> /tmp/wca-notifier.log 2>&1
```

### systemd timer (Linux)

```ini
# ~/.config/systemd/user/wca-psych-sheet.service
[Unit]
Description=WCA Psych Sheet Watched Competitor Notifier

[Service]
Type=oneshot
WorkingDirectory=/path/to/wca_scraper
ExecStart=/usr/bin/uv run python psych_sheet_notifier.py -w watch_list.json -e you@example.com
```

```ini
# ~/.config/systemd/user/wca-psych-sheet.timer
[Unit]
Description=Run WCA Psych Sheet Notifier weekly

[Timer]
OnCalendar=Sun *-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now wca-psych-sheet.timer
```

## API Reference (WCA)

- **Competitions**: `GET https://www.worldcubeassociation.org/api/v0/competitions?start=YYYY-MM-DD&end=YYYY-MM-DD`
- **WCIF (psych sheet)**: `GET https://www.worldcubeassociation.org/api/v0/competitions/{id}/wcif/public`

WCIF includes:
- `persons` – each with `registration` (status, eventIds) and `personalBests` (worldRanking per event)

## Example output

When watched competitors are found:

```
Subject: WCA: 2 competition(s) with watched competitors

Watched Competitors — Upcoming Competitions

Week of Feb 12, 2026 (Wed Feb 12 – Tue Feb 18)
• Max Park — UCLA Spring 2026 (US) (2026-02-15): 4x4
• Yiheng Wang — Singapore Championship 2026 (SG) (2026-02-20 – 2026-02-22): 3x3, 4x4

Week of Feb 19, 2026 (Wed Feb 19 – Tue Feb 25)
• ...
```
