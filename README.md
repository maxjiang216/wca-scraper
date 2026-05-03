# WCA tools

Utilities for the [World Cube Association](https://www.worldcubeassociation.org/) public API: email when competitors on **your watch list** register for upcoming comps, plus a **report** that highlights highly world-ranked registrants across upcoming competitions.

## Project layout

```
src/wca-org/
├── wca_api.py              # Competitions listing, WCIF, ranking helpers
├── notify.py               # Email formatting and SMTP
├── psych_sheet_notifier.py # Watch-list notifier (shared argument parser)
├── top_competitors_report.py # World-ranked registrants report
├── cli.py                  # ``wca notify`` / ``wca report``
└── __main__.py             # ``python -m wca_org``
```

No scraping and no authentication: uses the documented WCA v0 endpoints.

---

## CLI

Prefer the unified launcher (after [`uv sync`](https://docs.astral.sh/uv/)):

```bash
uv run wca notify --help
uv run wca report --help
```

Equivalent: `python -m wca_org notify …`, `python -m wca_org report …`.

**Compatibility:** the repo-root `psych_sheet_notifier.py` still exposes the notifier with the **same flags** as `wca notify` (no `notify` keyword).

---

### `wca notify` — watch-list email

Runs weekly via GitHub Actions (or cron) when any **watched competitor** is accepted and competing in an event present in your [`watch_list.json`](watch_list.example.json).

1. List upcoming competitions (`GET …/competitions`, paginated).
2. For each competition, load WCIF (`…/wcif/public`).
3. Match WCA IDs in your JSON (per-event keys like `333`, `444`).
4. Send HTML email with schedules when matched.

Setup:

```bash
uv sync
cp watch_list.example.json watch_list.json
# Edit IDs per event id
```

| Flag | Description |
|------|-------------|
| `-w/--watch-list` | Path (default `./watch_list.json`) |
| `-e/--email` | Recipient *(required unless `--dry-run`)* |
| `-s/--start` | Start date YYYY-MM-DD (default today) |
| `--end` | End date for listing; if omitted → `start + --weeks` |
| `-c/--country` | ISO2 or comma-separated list |
| `--weeks` | When `--end` omitted, window length (default 2 weeks) |
| `--rate-limit-delay` | Delay before each WCIF request (seconds) |
| `-z/--timezone` | Schedule display timezone |
| `--dry-run` | Log + write `wca_notify_dry_run.txt` |
| SMTP flags | `--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-password` |

Gmail uses an [App Password](https://support.google.com/accounts/answer/185833).

---

### `wca report` — top-ranked registrants

Builds Markdown (or plain text) listing accepted **competing** registrants whose **single** PB world rank (fallback: **average** if singles unranked) is **`≤ --max-rank`** in events they entered.

- By default **`--start` is today** in `--timezone` and **`--end` is omitted** so the competitions API returns **every upcoming listing** until pagination ends (respect WCA limits; jobs can take a long time and may hit HTTP 429).
- **`--max-competitions N`**: scans only the first *N* competitions by start date. When `--country` does **not** contain a comma (single-country filter), paging stops early instead of prefetching everything.
- **`--end`**: bounded window for quicker runs.

```bash
# Short dry-style run (single soonest upcoming comp)
uv run wca report --max-competitions 3 --rate-limit-delay 1.0

# Full open-ended crawl (omit --end — may run for a very long time)
uv run wca report --max-rank 50 -o report.md

# Bounded date range + US only (API-filtered → early paging still applies with --max-competitions)
uv run wca report -c US --end 2030-12-31 -o us.md

# Plain text
uv run wca report --format plain --max-competitions 5
```

WCIF shapes are documented briefly under **API Reference** below.

---

## GitHub Actions (weekly notifier)

Workflow: [`.github/workflows/weekly-notify.yml`](.github/workflows/weekly-notify.yml).

- Schedule: **Wednesday 00:00 UTC** (`cron`), plus manual dispatch.
- Step runs `uv run wca notify …` with secrets: `WATCH_LIST`, `NOTIFY_EMAIL`, `SMTP_USER`, `SMTP_PASSWORD`.

For non-Gmail SMTP, extend the step with `--smtp-host` / `--smtp-port`.

**Optional CI report:** duplicate the workflow pattern with `uv run wca report --max-competitions … --end …` and upload `-o report.md` as an artifact—GitHub Actions has a finite job lifetime, so bounded flags are prudent.

---

## Local scheduling

### Cron (example)

```
0 9 * * 0 cd /path/to/wca_scraper && uv run wca notify -w watch_list.json -e you@example.com >> /tmp/wca-notifier.log 2>&1
```

### systemd

Point `ExecStart` at `/usr/bin/uv run wca notify -w …` (same argv as notifier).

---

## Where to host

This repo is intentionally **standalone**: run locally, on a small VPS, or from **GitHub Actions** without AWS.

- **Lambda** fits poorly unless you rework into short steps (large scans exceed typical limits).
- **Batch/Fargate/EC2** is optional if policies require AWS; not needed for hobby use.

---

## API reference (WCA)

- **Comp**: `GET https://www.worldcubeassociation.org/api/v0/competitions?start=&end=&country_iso2=&sort=start_date`
- **WCIF:** `GET https://www.worldcubeassociation.org/api/v0/competitions/{id}/wcif/public`

WCIF persons include:

- `registration.status`, `registration.isCompeting`, `registration.eventIds`
- `personalBests[]` → `eventId`, `type` (`single` / `average`), `worldRanking`

---

## Hosting / ops recap

| Where | Fits |
|-------|------|
| GitHub Actions | Scheduled notifier; bounded `report` with `--max-competitions`/`--end` |
| Laptop / VPS / cron | Unlimited-length `report` runs |
| AWS | Rarely justified unless mandated; Lambda not ideal for exhaustive scans |
