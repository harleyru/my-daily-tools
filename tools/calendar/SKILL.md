---
name: calendar
description: Operate a macOS Calendar via AppleScript (add / today's events / delete). Invoke when the user says "add event X at time" or "what's on today". Events sync to iCloud (iPhone).
---

# calendar — macOS Calendar operations (AppleScript)

> Calendar name defaults to `Agent` (the author's own) — **change it to yours**: edit `add_event.scpt` and the templates below (`calendar "Agent"`).

## Reliable patterns (learned the hard way — do not change)

1. **Run `open -a Calendar && sleep 4` first**, then run osascript (fails if Calendar isn't running).
2. **Add events** by constructing with `current date` (set year/month/day/hours/minutes/seconds), template in `add_event.scpt`; events default to 1 hour.
3. **Query events**: `whose summary contains "keyword"` (**do not use `is` with exact-match Chinese — fails with -1728**); the lower date bound must have hours/minutes/seconds reset to 0 (`current date` keeps the current time and silently misses earlier events of the day).
4. **Delete events**: list the uid first, then `delete (first event of calendar "Agent" whose uid is "..." )` — deleting from a `whose`-filtered list inside a loop invalidates the iterator (-1728 "can't get item 2").

## Template

```applescript
tell application "Calendar"
	tell calendar "Agent"
		set startDate to current date
		set year of startDate to 2026
		set month of startDate to 8
		set day of startDate to 21
		set hours of startDate to 10
		set minutes of startDate to 0
		set seconds of startDate to 0
		set endDate to startDate + (1 * hours)
		make new event with properties {summary:"Event name", start date:startDate, end date:endDate}
	end tell
end tell
```

## Query today's events (locale-safe)

```applescript
tell application "Calendar"
	tell calendar "Agent"
		set lb to current date
		set hours of lb to 0
		set minutes of lb to 0
		set seconds of lb to 0
		set ub to lb + (1 * days)
		set out to ""
		repeat with ev in (every event whose start date ≥ lb and start date < ub)
			set out to out & (time string of (start date of ev)) & " " & (summary of ev) & linefeed
		end repeat
		return out
	end tell
end tell
```

Note: `«class isot»` to text fails — use `time string` (locale-safe).

## Files in this directory

- `add_event.scpt` — add-event template (edit date/time/title, then `osascript add_event.scpt`)
