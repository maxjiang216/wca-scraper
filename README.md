# WCA tools

Utilities for the [World Cube Association](https://www.worldcubeassociation.org/) public API: email when **`people`** register for upcoming comps (psych sheet), a **daily** digest (**WCA Live** + published **competition results**) with per-event **OR** rules (default **WR-only**), a **weekly** psych email, plus a **report** of highly world-ranked registrants.

## Project layout

```
src/wca-org/
├── wca_api.py              # Competitions, WCIF, person & competition results
├── wca_live_api.py         # WCA Live GraphQL (recent records)
├── wca_export.py           # Official export TSV helpers (optional)
├── results_format.py       # e.g. multiblind decode
├── cubing_china_api.py     # cubing.com JSON API
├── watch_list.py           # YAML: psych people + daily OR rules
├── notify.py               # Email HTML + SMTP
├── psych_sheet_notifier.py # Upcoming psych sheet (WCA ± cubing.com)
├── records_notifier.py     # Daily Live + competition `/results` scan
├── weekly_digest.py        # Weekly psych sheet email
├── top_competitors_report.py
├── cli.py                  # wca notify | weekly | records | report
└── __main__.py             # python -m wca_org
```

Uses the WCA **v0 REST API**, **WCA Live** GraphQL, and **cubing.com** `/api/v0` — no OAuth for read-only features.

---

## Watch list (`watch_list.yaml`)

Copy [`watch_list.example.yaml`](watch_list.example.yaml). Top-level **`config:`**:

- **`timezone`** — IANA name for schedules and emails.
- **`continental_records`** — optional reference in file; **daily CR matching does not inherit this** (set tags explicitly per event).

### Per event — psych sheet

| Key | Meaning |
|-----|---------|
| **`people`** | WCA IDs matched in **upcoming** WCIF ( **`notify`**, **`weekly`** only). |

Legacy **`roundup_all`** / flat YAML list ⇒ **`people`**. **`weekly_sub_pb.watch`**, **`roundup_pbs`**, etc. are **not** merged into **`people`** anymore.

### Per event — **`wca records`** (daily)

**OR**’d conditions (if **any** match → include). **Default** for an event: **world records only** (until you add more keys).

| Key | Meaning |
|-----|---------|
| **`continental_records`** / **`cr`** / **`crs`** | Notify on results whose `regional_*_record` is one of these tags (**NAR**, **ER**, …). **Omit or `[]`** ⇒ no CR rule (not inherited from `config`). |
| **`sub_single`** / **`sub_average`** | Time events: **any competitor**, any published round — single/average **strictly under** cap (centiseconds; or `*_seconds`). Unrelated to PB. **Not** used for **333mbf**. |
| **`sup_points`** / **`mbf_sup`** / **`sup_mbf`** | **333mbf** only: decoded MBLD **solved count ≥** this value (**sup**, not sub). Encoding: classic WCA value below 1e9 supported. |

**WCA Live** rows use the same OR logic (WR always; CR via country→continent; sub/sup on values).

**Competition results**: for comps that **ended** in the last **`--ended-days`** (default 2), `GET /competitions/{id}/results` — sub/sup/WR/CR on each row. First run bootstraps seen result ids (no email).

Nested **`daily:`** / **`weekly_sub_pb:`** still merged into thresholds. **`top_world_rank`** removed.

---

## CLI

After [`uv sync`](https://docs.astral.sh/uv/):

```bash
uv run wca notify --help
uv run wca weekly --help
uv run wca records --help
uv run wca report --help
```

**Compatibility:** repo-root [`psych_sheet_notifier.py`](psych_sheet_notifier.py) still calls the same notifier flags as `wca notify`.

### `wca notify` — upcoming psych sheet email

1. List upcoming competitions (`GET /api/v0/competitions`).
2. Load WCIF per competition, match accepted competitors on your watch list.
3. Optional **`--cubing-china`**: adds [cubing.com](https://cubing.com/) WCA comps in the same date window (skips comps already returned by WCA).

| Flag | Description |
|------|-------------|
| `-w/--watch-list` | Path (default `./watch_list.yaml`) |
| `-e/--email` | Recipient *(required unless `--dry-run`)* |
| `-z/--timezone` | Overrides `config.timezone` if set |
| `--cubing-china` | Include cubing.com listings |
| `--dry-run` | Writes `wca_notify_dry_run.txt` |

### `wca weekly` — upcoming psych sheet

Same scan as **`notify`** (optionally **`--cubing-china`**). Results / export recap are **not** included; use **`wca records`** daily for that.

### `wca records` — daily digest

**WCA Live** plus **`GET /competitions/{id}/results`** for recent ended comps. **`records_state.json`**: Live id dedupe + seen REST result ids (bootstrap first run).

| Flag | Description |
|------|-------------|
| `--ended-days` | Comps whose end date falls in this window (default **2**) |
| `--state-file` | JSON state (default `./records_state.json`) |
| `--dry-run` | No email and **no** state update |

### `wca report` — top-ranked registrants

Markdown/plain report of highly ranked registrants at upcoming comps (unchanged). See `--help` for `--max-competitions` and date bounds.

---

## GitHub Actions

Set repository **secrets** (Settings → Secrets and variables → Actions):

| Secret | Purpose |
|--------|---------|
| `WATCH_LIST` | **Full file contents** of `watch_list.yaml` (including `config:`) |
| `NOTIFY_EMAIL` | Recipient |
| `SMTP_USER` | Gmail or SMTP username |
| `SMTP_PASSWORD` | Gmail [App password](https://support.google.com/accounts/answer/185833) or SMTP password |

Workflows:

| File | Schedule | Command |
|------|----------|---------|
| [`.github/workflows/weekly-notify.yml`](.github/workflows/weekly-notify.yml) | Thursday **15:00 UTC** | `wca weekly --cubing-china …` |
| [`.github/workflows/daily-records.yml`](.github/workflows/daily-records.yml) | Daily **15:00 UTC** | `wca records …` + `actions/cache` for `records_state.json` |

**15:00 UTC** is **7:00** US/Pacific in standard time; **8:00** during daylight saving. Adjust `cron` if you want Vancouver **exactly** year-round.

Non-Gmail SMTP: add `--smtp-host` / `--smtp-port` to the workflow `run:` lines.

---

## Local / ops

- **`.env.example`** — optional reference for local env vars (Actions use secrets instead).
- **Cron (example):**  
  `0 15 * * * cd /path/to/wca_scraper && uv run wca records -w watch_list.yaml -e you@example.com --smtp-user … --smtp-password …`

---

## API reference

| Source | Use |
|--------|-----|
| `GET https://www.worldcubeassociation.org/api/v0/competitions` | Upcoming / date-filtered listings |
| `GET …/competitions/{id}/wcif/public` | Registrations & schedule |
| `GET …/persons/{wcaid}/results` | Result rows for **`people`** tracking |
| `GET …/export/public` | Latest **tsv_url** for the results ZIP |
| `POST https://live.worldcubeassociation.org/api` | GraphQL `recentRecords` |
| `GET https://cubing.com/api/v0/competition?year=current&type=WCA` | China-side listings |
| `GET https://cubing.com/api/v0/competition/{alias}/competitors` | Registrants + events |

WCIF persons use `registration.status`, `registration.isCompeting`, `registration.eventIds`, and `personalBests[]` with `worldRanking`.

---

## Hosting recap

| Where | Fits |
|-------|------|
| GitHub Actions | Scheduled **weekly** psych sheet and **daily** digest |
| Laptop / VPS | Same CLIs; keep `records_state.json` on disk instead of Actions cache |
| AWS | **Lambda** is a poor fit for long jobs; use **Actions** or a small VPS |
