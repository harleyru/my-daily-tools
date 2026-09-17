---
name: radar
description: Research radar — arXiv API + RSS + HN Algolia fetch → keyword prefilter + dedup → headless claude -p editorial scoring → reports/radar.html + notify push. Invoke when the user says "run radar".
argument-hint: '[YYYY-MM-DD] [--skip-fetch | --skip-editorial | --skip-notify]'
allowed-tools:
  - Bash
  - Read
---

# radar — research radar

## When to use

- "run radar" / "today's radar" → run it as-is
- "backfill / re-run a specific day" → pass the date
- After editing keywords or the interest profile → read the `seen.sqlite` gotcha below first
- A push failed or the run died partway → read the log, then resume from a step with `--skip-*` instead of redoing the whole round

## Usage

```bash
python3 "$SKILL_DIR/radar_daily.py" [YYYY-MM-DD] [--skip-fetch] [--skip-editorial] [--skip-notify]
```

## Decision rules

```
radar_fetch.py        fetch (arXiv API + RSS + HN Algolia, pure stdlib)
  → radar_prefilter.py  keyword recall + seen.sqlite dedup + hit-density ranking
  → radar_daily.py      editorial stage (headless claude -p --output-format json, no tools, no web)
  → frozen v1 contract validation (auto-retry once on violation)
  → reports/radar.html + notify push (via the notify skill)
```

A prefilter hit rate of ~75% is expected — the strategy is **recall-first**, and the ranking proxy score (keyword hit density + cross-track bonus) is what pushes strong signals into the top N for the editorial stage.

## Gotchas

- **The `seen` table pins entries that reached the candidate list but the editorial stage never picked.** Items are released once they fall outside the window (re-scored, clock restarted) so they are not buried forever — but **after changing keywords you must still delete `data/radar/seen.sqlite`** to reset, or old items that newly match your keywords never get in.
- **Old-post filtering is expected output, not an error.** Items published outside the fetch window are dropped by `siteDays`, and placeholder titles (e.g. "not much happened today") are dropped by the `titleBlocklist`. The summary line reports `N stale / N blocklisted / N re-scored past window` — all three are normal.
- **A missing or unparseable `publishedAt` must keep the item, not drop it.** The age filter is deliberately conservative: a datetime parsing failure should never quietly lose a candidate. Do not "tidy" that into a drop.
- **The editorial stage has no exploration layer.** v1 is pure scoring over the fetched text; it does not browse.
- **If you point `RADAR_EDITOR` at a wrapper CLI that routes to another provider, have the wrapper strip `ANTHROPIC_*` / `CLAUDE_*` from the environment it passes down** — inheriting a proxied session's env is a common cause of 401s. This collection ships no such wrapper; the script passes its own environment through unchanged.
- **`RADAR_EDITOR` is resolved with `shutil.which` first**, so a bare command name works, with a `~/.local/bin` fallback for schedulers that run with a narrow `PATH`. Checking the value with a plain `os.path.exists()` instead silently breaks the bare-name case.
- **When the editorial call fails, persist the raw output** (`logs/radar_editorial_fail_*.txt`, with the `JSONDecodeError` offset and surrounding context). The terminal log alone is not enough to tell a prompt regression from a model that started wrapping its JSON in prose.

## Architecture

The pipeline is split so only the last stage needs an LLM: fetching and prefiltration are plain stdlib and run anywhere, while the editorial stage shells out to a CLI headlessly. Scheduling lives on an always-on Linux host, so the Mac does not have to be awake for a run.

All state hangs off a project base directory (`$DAILY_BASE`, else the repo root): `data/radar/` holds config, the interest profile, the editorial brief and the dedup table, so a run is reproducible and the whole setup moves between machines by copying one directory. Credentials are not part of it — the editorial CLI uses whatever auth it already has.

If your provider prices or rate-limits by time of day, schedule the run **off the peak boundary** rather than retrying failures — that is the entire point of moving the timer.

## Files here

| File | Purpose |
|---|---|
| `radar_fetch.py` | fetch |
| `radar_prefilter.py` | prefilter + dedup + ranking |
| `radar_daily.py` | main entry: pipeline + editorial call + contract check + push |

Config template: `config-examples/radar.config.json` → copy to `data/radar/config.json`.
