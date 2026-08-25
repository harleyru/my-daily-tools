---
name: radar
description: Research radar: arXiv API + RSS + HN Algolia fetch → keyword prefilter + dedup → headless claude -p editorial scoring → reports/radar.html + notify push. Invoke when the user says "run radar".
---

# radar — research radar

## Pipeline

```
radar_fetch.py (fetch) → radar_prefilter.py (keyword recall + seen.sqlite dedup + hit-density ranking)
→ radar_daily.py editorial stage (claude -p --output-format json, headless, no tools)
→ frozen v1 contract validation (auto-retry once on violation) → reports/radar.html + notify.sh push
```

## Usage

```bash
python3 "$SKILL_DIR/radar_daily.py" [YYYY-MM-DD] [--skip-fetch] [--skip-editorial] [--skip-notify]
```

## Config & profile (data/radar/)

| File | Purpose |
|---|---|
| `config.json` | sources / keywords / budget |
| `interest-profile.md` | scoring anchors (revisions logged in-file) |
| `daily-prompt.md` | editorial task brief |
| `seen.sqlite` | dedup table (**drop it after changing keywords** to re-evaluate old items) |

Data: `data/radar/raw|editorial|public/YYYY-MM-DD/`. A prefilter hit rate of ~75% is expected (recall-first); the editorial proxy score pushes strong signals into the top N.

## Notes

- The editorial stage has **no exploration layer** (v1 is pure scoring, no web access)
- Headless claude subprocesses must **strip `ANTHROPIC_*`/`CLAUDE_*` env vars** (inherited from a proxied session → 401)
- Schedule it with your own cron/systemd/launchd; run manually to backfill/retry
- Data dir: `data/radar/` — initialize by copying `config-examples/radar.config.json`
