---
name: weekly-report
description: Weekly report: aggregate this week's daily-note "accomplishments" + long-term task progress → push via notify. Invoke when the user says "generate weekly report".
---

# weekly-report — weekly report generation + push

## Usage

```bash
python3 "$SKILL_DIR/weekly_report.py"            # this week (Friday)
python3 "$SKILL_DIR/weekly_report.py" 2026-08-10  # specific week
```

- Aggregates the "accomplishments" section of `<BASE>/journal/YYYY-MM-DD.md` + long-term task progress from `<BASE>/ongoing.md`
- Push goes through notify.sh (Lark + Discord mirror)
- Schedule with your own scheduler (weekly); `BASE` = repo root, or override with the `DAILY_BASE` env var

## Configuration

No independent config — depends on your notes layout (`journal/`, `ongoing.md`, format described in the repo README) and the notify skill's credentials.

## Files in this directory

- `weekly_report.py` — the weekly report script
- `daily_push.py` — daily morning digest (schedule + long-term tasks; independent of the weekly report, archived here)
