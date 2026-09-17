---
name: weekly-report
description: Weekly report and daily morning digest — aggregate this week's daily-note "accomplishments" plus long-term task progress from ongoing.md, and a daily digest of the schedule snapshot and open todos. Invoke when the user says "generate weekly report" or "send the morning digest".
argument-hint: '[YYYY-MM-DD] (Monday of the target week; defaults to this week)'
allowed-tools:
  - Bash
  - Read
---

# weekly-report — weekly report + daily morning digest

## When to use

| The user says | Action |
|---|---|
| "generate the weekly report" | `weekly_report.py` (defaults to this week) |
| "catch up last week's report" | `weekly_report.py <a date in that week>` |
| "send the morning digest" | `daily_push.py` |

## Usage

```bash
python3 "$SKILL_DIR/weekly_report.py"             # this week
python3 "$SKILL_DIR/weekly_report.py" 2026-08-10  # a specific week
python3 "$SKILL_DIR/daily_push.py"                # morning digest
```

## Decision rules

- **Weekly report**: collects the "accomplishments" section of each `journal/YYYY-MM-DD.md` in the week + long-term task progress from `ongoing.md` → composes a message → pushes via `notify.sh` (Lark + Discord mirror)
- **Morning digest**: today's schedule (read from the synced `data/calendar_today.json` snapshot) + long-term todos still open

## Gotchas

- **The digest's schedule comes from a snapshot, not a live calendar.** The scheduler host cannot read a Mac calendar. A periodic job on the Mac exports today's (and tomorrow's) events into `data/calendar_today.json`, which is then shipped over. **When the snapshot is missing or stale, the script says so — that is not a broken script, it is the Mac side not having run** (machine asleep or off, or the sync job unloaded). Look at the Mac before debugging the reader.
- **A one-way data sync must push an explicit, minimal file list.** Whatever ships from the workstation to the host must be named item by item — never a whole directory. The host owns its own `data/` and writes live state there; a directory-level sync would overwrite the host's newer files with the workstation's older copies, in both directions.
- **Neither report invents content.** They only summarise: daily notes and the ongoing-task file have to be updated as work happens (the working convention is "user states progress → update `ongoing.md` + that day's note"). An empty report means the notes were not updated, not that the script failed.
- **Pushes depend on the `notify` skill's credential chain** (`.env` → keychain). If nothing arrives, check that first.
- **Re-running is safe and is the normal recovery path** — both scripts are read-only summarisers, so a missed push is fixed by running the script again rather than by fixing state.

## Architecture

Both jobs are **pure aggregation over files that already exist**: daily notes as plain Markdown and a single long-term task file. No database, no separate tracker — which is why the reports can be regenerated at any time and never drift from the notes.

They run on the always-on host, not the workstation, so the report goes out regardless of whether the Mac is awake. The only thing they need from the Mac is the calendar snapshot, and that dependency is made explicit and failure-visible rather than hidden: when the snapshot is absent the digest reports it instead of quietly printing an empty schedule.

The two scripts are independent of each other — the digest does not feed the report, and either can be scheduled, skipped or re-run alone.

## Files here

| File | Purpose |
|---|---|
| `weekly_report.py` | weekly report |
| `daily_push.py` | morning digest (today's schedule + long-term todos) |
