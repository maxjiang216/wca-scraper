# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**wca_scraper** is a Python toolkit for World Cube Association (WCA) competition monitoring and notifications. It provides four main features:

1. **Psych sheet notifier** (`wca notify`) — Email when watch-listed competitors register for upcoming competitions
2. **Weekly digest** (`wca weekly`) — Weekly email with upcoming psych sheets (WCA + optional Cubing China)
3. **Records alert** (`wca records`) — Daily digest of WCA Live records and recent competition results (filtered by configurable OR rules per event)
4. **Top competitors report** (`wca report`) — Markdown/plain report of world-ranked registrants at upcoming competitions

## Setup & Common Commands

### Initial Setup

```bash
# Install uv (package manager): https://docs.astral.sh/uv/
uv sync                           # Install dependencies into .venv

# View available commands
uv run wca --help
```

### Running Commands Locally

```bash
# Psych sheet notification (test with --dry-run)
uv run wca notify -w watch_list.yaml -e user@example.com --dry-run

# Weekly digest (test mode) — default 8 weeks ahead
uv run wca weekly -w watch_list.yaml -e user@example.com --dry-run --cubing-china

# Records alert (test mode, no state update)
uv run wca records -w watch_list.yaml -e user@example.com --dry-run

# Top competitors report
uv run wca report -w watch_list.yaml

# Help for any command
uv run wca notify --help
uv run wca records --help
```

### Local Testing with Dry-Run

All commands support `--dry-run` which:
- **`notify`/`weekly`** — Writes `wca_notify_dry_run.txt` or `wca_weekly_dry_run.txt` instead of sending email
- **`records`** — Writes `wca_records_dry_run.txt` and does NOT update `records_state.json`; only writes the file if there are new matching items (nothing to write if state is current)

Use dry-run to verify watch list rules and email formatting before deploying to GitHub Actions.

### Environment Variables (Optional)

For local development, copy `.env.example` to `.env`:
```bash
cp .env.example .env
# Edit .env with your credentials
```

GitHub Actions use repository secrets instead (see README.md).

## Architecture

### High-Level Data Flow

```
Watch List (YAML) → Event Filters
                     ↓
API Clients ← WCA REST API v0
            ← WCA Live GraphQL
            ← Cubing China JSON API
                     ↓
Match Competitors ← Rule Engine (OR logic per event)
                     ↓
Format Email → Send via SMTP
```

### Module Organization

| Module | Purpose |
|--------|---------|
| **cli.py** | Unified CLI dispatcher; routes `notify`, `weekly`, `records`, `report` commands |
| **wca_api.py** | WCA v0 REST API client — competitions, WCIF (psych sheet), person results, continental records mapping |
| **wca_live_api.py** | WCA Live GraphQL client — recent records with attempt details |
| **cubing_china_api.py** | Cubing China (cubing.com) JSON API — competition listings and registrant data |
| **watch_list.py** | YAML/JSON parsing — `people` (psych sheet) and per-event OR rules (records alert) |
| **psych_sheet_notifier.py** | Gather upcoming competitors; coordinate WCA + optional Cubing China data |
| **records_notifier.py** | Daily digest — WCA Live + recent competition results; state management for deduplication |
| **weekly_digest.py** | Wrapper around psych sheet gathering for weekly emails |
| **top_competitors_report.py** | Generate markdown/plain reports of top-ranked registrants |
| **notify.py** | Email HTML/plain text generation; SMTP sending |
| **wca_export.py** | WCA public results database export (ZIP/TSV) — download, parse persons and results |
| **results_format.py** | MBLD decoding and result formatting utilities |

### Key Design Patterns

#### 1. Watch List Configuration (YAML)
```yaml
config:
  timezone: America/Vancouver
  continental_records: [NAR, ER, AsR]  # Optional default CR tags

333:
  people: ["2023GENG02"]  # IDs to watch in psych sheet (notify/weekly)
  continental_records: [NAR]  # For daily records: match regional_*_record tags
  sub_single: 400  # For daily records: notify on singles < 4.00s
  sub_average: 500

444:
  people: ["2010LEAR01"]
  # If no daily rules: defaults to world records only
```

**Key distinction:**
- `people` → Used only by `notify` and `weekly` (psych sheets)
- Daily rules (`continental_records`, `sub_single`, `sub_average`, `sup_points`) → Used only by `records`

#### 2. OR-Logic Filtering (Records)
For each event, a row matches if ANY of these conditions is true:
- World record (always)
- Continental record tag matches config
- Time is strictly under `sub_*` threshold
- MBLD solved count ≥ `sup_points`

This logic applies to both WCA Live records and competition `/results` rows.

#### 3. State Management (Records Alert)
`records_state.json` deduplicates rows across runs:
```json
{
  "seen_live_ids": ["id1", "id2"],
  "seen_rest_result_ids": ["comp-id_person_event_round"]
}
```

**First run:** Bootstraps state; no email sent. Subsequent runs email new matches only.

#### 4. Rate Limiting
- WCA API: 0.5s delay between page fetches; 429 retry with backoff (5s, 10s, 20s)
- Cubing China API: 1.5s-2s delay per competition detail request (configurable via `--rate-limit-delay`)
- WCIF per competition: ~1s delay per request

### WCIF (Psych Sheet) Structure

WCIF contains:
- **persons[]** — Registrants with `registration` and `personalBests[]` (includes `worldRanking`)
- **schedule** — Venues → rooms → activities (start/end times, assignments)
- **events** — Competition event details

Key filtering:
- `registration.status == "accepted"` + `registration.isCompeting == true` → active competitors
- `registration.eventIds[]` → events they're competing in
- `personalBests[].worldRanking` → used for top-competitors report

## Testing Strategy

**No automated test suite exists yet.** Manual testing approach:

1. **Dry-run before deploy:**
   ```bash
   uv run wca notify -w watch_list.yaml -e user@example.com --dry-run
   cat wca_notify_dry_run.txt  # Inspect HTML output
   ```

2. **Verify watch list parsing:**
   - Check `python -m wca_org` with a test `watch_list.yaml` that has known people/rules
   - Confirm email subject and body reflect matched competitors/records

3. **GitHub Actions testing:**
   - Use `workflow_dispatch` on weekly-notify.yml / daily-records.yml for on-demand runs
   - Check job logs for parsing errors, API failures, rate-limit retries

4. **State testing (records):**
   - Run `records` twice; second run should have fewer/no matches (dedup works)
   - Delete `records_state.json` to reset bootstrap

## Deployment

### GitHub Actions

Set repository secrets:
- `WATCH_LIST` — Full YAML file contents (including `config:` block)
- `NOTIFY_EMAIL` — Recipient address
- `SMTP_USER`, `SMTP_PASSWORD` — Gmail App Password or SMTP credentials

Workflows:
- **weekly-notify.yml** — Thursday 10:00 UTC (≈ 5am PDT after typical GitHub Actions delay); `timeout-minutes: 25` because 8-week window checks ~300+ competitions
- **daily-records.yml** — Daily 10:00 UTC (≈ 5am PDT after typical delay); uses `actions/cache` for `records_state.json` persistence

### Local / VPS Cron

Example cron job:
```bash
0 15 * * * cd /path/to/wca_scraper && uv run wca records -w watch_list.yaml -e you@example.com --smtp-user ... --smtp-password ...
```

Note: Keep `records_state.json` on disk for state persistence (unlike GitHub Actions cache).

## API Reference

| Source | Endpoint | Use |
|--------|----------|-----|
| WCA REST v0 | `GET /competitions` | Upcoming/ended competitions (paginated) |
| WCA REST v0 | `GET /competitions/{id}/wcif/public` | Registrations & schedule |
| WCA REST v0 | `GET /persons/{wcaid}/results` | All results for one person |
| WCA REST v0 | `GET /competitions/{id}/results` | All published results for one comp |
| WCA REST v0 | `GET /countries` | Country → continent mapping (for CR tags) |
| WCA Live GraphQL | `POST /api` (`recentRecords` query) | Recent records with attempts |
| Cubing China JSON | `GET /api/v0/competition` | WCA comp listings (China-side) |
| Cubing China JSON | `GET /api/v0/competition/{alias}/competitors` | Registrants + events |

## Dependency Notes

- **requests** ≥ 2.28.0 — HTTP client (WCA + Cubing China APIs)
- **pyyaml** ≥ 6.0 — YAML watch list parsing
- **Python** ≥ 3.10 — Type hints, `zoneinfo` for timezone handling

No external testing frameworks; no database; no async code.

## Gotchas & Caveats

1. **WCIF world rankings:** Only available in the public WCIF, not via person results API. Top-competitors report needs full WCIF per competition.

2. **Cubing China User-Agent:** Requires custom User-Agent header (Chrome-like); default python-requests UA gets 403.

3. **Cubing China competition window:** The API `year=current` returns all current-season comps. They only appear in the weekly email if they overlap the `--weeks` window (default 8 weeks). Competitions are deduped against WCA IDs already fetched — a China-only comp (no WCA ID yet) will be fetched separately. The cubing.com competitor events list mixes int event IDs (e.g. `333` as int) with string IDs (e.g. `"333bf"`); `normalize_event_id()` handles the conversion.

4. **Continental records mapping:** Built client-side from WCA `/countries` endpoint. Not inherited from config for daily digest rules.

5. **State file format:** `records_state.json` uses simple JSON (not atomic writes). Rare concurrent runs could cause data loss.

6. **Timezone handling:** All times converted to target timezone (default: `America/Vancouver`). WCIF schedule times are ISO 8601 UTC. In psych sheet emails, timezone is shown only in the expanded row view.

7. **Multiblind (333mbf) encoding:** Old format (< 1e9): `(99 - (solved - missed)) * 10_000_000 + time_seconds * 100 + missed`. Displayed as `X/Y M:SS`. Values ≥ 1e9 (newer format) fall back to raw integer.

8. **Smoke tests:** All three API modules log `[smoke]` warnings when expected fields are missing from API responses. Check GitHub Actions logs for these to diagnose schema changes.

9. **Email expandable blocks:** Psych sheet uses `<details>/<summary>` for collapsible rows. Gmail web supports this; Outlook desktop does not (renders expanded). The `<summary>` shows: bold competitor names · competition link (year stripped from name) · events · dates.
