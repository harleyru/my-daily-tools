---
name: calendar
description: Operate a macOS Calendar via AppleScript (add / query / delete events, add an advance alarm). Invoke when the user says "add event X at time", "what's on today", or "remind me N minutes before". Events sync to iCloud (iPhone).
argument-hint: '<event description + time>, e.g. "add: group meeting 2026-09-22 16:00"'
allowed-tools:
  - Bash
  - Read
---

# calendar — macOS Calendar operations (AppleScript)

> Calendar name defaults to `Agent` (the author's own) — **change it to yours**: edit `add_event.scpt` and the templates below (`calendar "Agent"`).

## When to use

| The user says | Action |
|---|---|
| "add event …", "schedule …", "on <date> at <time> …" | add an event |
| "remind me N minutes before", "add an alarm" | add `display alarm` to the existing event |
| "what's on today / tomorrow" | query events |
| "move it to <time>", "cancel that event" | edit / delete |

## Usage

```bash
open -a Calendar && sleep 4          # Calendar must be running or osascript errors out
osascript add_event.scpt             # template: edit date/time/title, then run
```

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

## Decision rules

| Operation | Key points |
|---|---|
| Add event | assign `current date` **field by field** (year/month/day/hours/minutes/seconds); `end date` defaults to +1 hour |
| Add an advance alarm | `make new display alarm at endDate with properties {trigger interval:-10}` |
| Query | `whose summary contains "keyword"`; zero the lower date bound by hand |
| Delete | list the uids first, then `delete (first event of calendar "Agent" whose uid is "...")` |

## Gotchas

- **`date "ISO string"` silently drops the time.** AppleScript parses `date` literals in the system locale; when parsing fails it keeps midnight, so the event lands at 00:00 with no error. Always assign fields individually.
- **`trigger interval` on a `display alarm` is in MINUTES**, not seconds. Negative means "before the event" (`-10` = 10 minutes early); `0` is at start time.
- **`whose summary is "<non-ASCII text>"` fails with -1728** under a CJK locale — exact matching on non-ASCII summaries is unreliable. Use `contains` instead.
- **The query's lower date bound must have hours/minutes/seconds zeroed.** `current date` carries the current time, so using it directly as the `≥` bound silently misses events earlier in the same day.
- **Deleting from a `whose`-filtered list inside a loop invalidates the iterator** (-1728 "can't get item 2"). Collect the uids first, then delete one by one by uid.
- **`«class isot»` cannot be coerced to text.** Format times with `time string`, which is locale-safe.
- **If Calendar.app is not running, osascript fails outright** — always `open -a Calendar && sleep 4` first (a cold start can take longer than 4s).

## Architecture

This skill is **Mac-only and unscheduled**: the scheduler for the rest of the collection runs on a Linux host, which has no `osascript`, so calendar work stays on the workstation and is triggered from a session.

The calendar is matched **by name**, and it must live under an iCloud account for events to reach other devices. AppleScript has no notion of accounts, so a new calendar can only be created in the GUI — a script that tries to create one, or that names an account, will not work.

Other skills that need the schedule do not query the calendar live. A periodic job on the Mac exports a **snapshot** of today's and tomorrow's events, which is then shipped to the scheduler host; a missing or stale snapshot means the Mac side has not run (asleep, off, or the job unloaded), not that the reader is broken.

## Files here

- `add_event.scpt` — add-event template (edit date/time/title, then `osascript add_event.scpt`)
