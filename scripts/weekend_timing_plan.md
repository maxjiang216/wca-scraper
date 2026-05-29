# Weekend plan: does cubing.com publish results before WCA?

**Question:** For Chinese comps, does `cubing.com/results/competition/{WCA_ID}/all`
show results *earlier* than the official WCA REST upload — and by how much? The
answer decides whether building the cubing.com results scraper (deferred) is worth
the HTML-parsing fragility, or whether the shipped `records --ended-days 10` path
(which depends on the WCA upload) is good enough.

## Test subjects (this weekend, all in Quanzhou, Fujian = UTC+8)

| Comp (China-local date) | WCA id | Watched competitor signal to watch for |
|---|---|---|
| Quanzhou Summer (Sat May 30) | `QuanzhouSummer2026` | **Yiheng Wang** 2019WANY36 — 3x3 (expect sub-4 single / sub-5 avg) |
| Quanzhou Blindfolded (Sun May 31) | `QuanzhouBlindfolded2026` | **Yifan Wang** 2017WANY29 — 3BLD |
| Hefei Cubing League 3x3 II (Wed Jun 3) | `HefeiCubingLeague3x3II2026` | Yiheng / Xuanyi — 3x3 (follow-up data point) |

A one-day Quanzhou comp runs ~09:00–18:00 local = **01:00–10:00 UTC**; results
finalize around **~10:00 UTC**, uploads happen sometime after.

## Tool

```bash
uv run python scripts/compare_results_timing.py QuanzhouSummer2026
```

Prints, for each source, the row count + a sample, and a verdict line
(`cubing is EARLIER` / `WCA only` / `both` / `neither`). Validated May 29:
on a finished comp (Hangzhou) both sources matched (~1060 rows); on the
not-yet-run comp (Quanzhou Summer) both were empty.

## Procedure

For each comp, starting **right after its ~10:00 UTC finish**, run the tool and
log the timestamp + verdict. Cadence: every ~3–4h until **both** sources have
results, then stop for that comp.

- **Sat May 30:** first check ~11:00 UTC (`QuanzhouSummer2026`), then ~15:00,
  ~19:00, ~23:00 UTC; continue Sun if WCA still empty.
- **Sun May 31:** same cadence for `QuanzhouBlindfolded2026`.
- **Wed Jun 3:** one or two checks for `HefeiCubingLeague3x3II2026` as a tiebreaker.

Record per comp:
- `t_cubing` = first UTC time cubing.com shows results
- `t_wca` = first UTC time WCA REST shows results
- `delta = t_wca − t_cubing`

## Log (fill in)

| Comp | t_cubing (UTC) | t_wca (UTC) | delta | notes |
|---|---|---|---|---|
| QuanzhouSummer2026 | | | | |
| QuanzhouBlindfolded2026 | | | | |
| HefeiCubingLeague3x3II2026 | | | | |

## Decision criteria

- **delta ≥ ~12h on multiple comps** → cubing.com is meaningfully earlier. Build
  the scraper as a *fallback source* (see [[reference-cubing-results-scraping]] in
  memory): scrape only comps whose WCA results aren't up yet, dedup against WCA,
  degrade gracefully on HTML changes.
- **delta small (a few hours) or inconsistent** → not worth the fragility; keep
  the `--ended-days 10` WCA path. The daily records job already tolerates a
  multi-day WCA upload lag.

## Notes / gotchas

- cubing.com soft-rate-limits: the script sets the `CubingRateLimit=1` cookie to
  get past the 429-then-reload. Space out runs; don't hammer.
- Results page is keyed by **WCA id**, not the cubing alias.
- If cubing.com shows results but WCA is empty for a watched competitor, that is
  exactly the gap the scraper would close — note the competitor + time.
