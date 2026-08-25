#!/usr/bin/env python3
"""Daily morning digest: today's calendar snapshot + long-term tasks → push via notify.sh.
The calendar snapshot (data/calendar_today.json) is exported by the macOS sync script.

Section titles below ("Today's tasks", "In progress", "Todo") are the headings of
your markdown notes (journal/YYYY-MM-DD.md, ongoing.md) — rename to match yours.
"""
import re, subprocess, os, json
from datetime import datetime, date

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.environ.get("DAILY_BASE") or os.path.dirname(TOOLS)
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")
ONGOING = os.path.join(PROJ, "ongoing.md")
SNAP = os.path.join(PROJ, "data", "calendar_today.json")
NOTES_DIR = "journal"                # your daily-notes directory (日记/ in the author's setup)
TODAY_SECTION = "## Today's tasks"   # heading of the daily note's per-day tasks section
GMAIL_STATE = os.path.join(
    os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail"),
    ".state.json",
)

# Task display: task-name fragment → (current stage, total stages), dots ●●●○○. Fill your own, e.g.
# STAGES = {"paper revision": (2, 4), "experiment setup": (1, 3)}
STAGES = {}
GMAIL_TOTAL = 0  # your backup total, for the progress bar; 0 = hidden

PRI = {"P0": "🔴", "P1": "🟡", "P2": "⚪"}
WEEKDAYS = "MonTueWedThuFriSatSun"


def today_events():
    """Read today's calendar snapshot (synced from macOS); returns [(HH:MM, calendar, title)]
    sorted by time; None if the snapshot is missing or stale (macOS not synced)"""
    try:
        data = json.load(open(SNAP, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("date") != date.today().strftime("%Y-%m-%d"):
        return None
    evs = [tuple(e) for e in data.get("events", []) if len(e) == 3]
    return sorted(evs, key=lambda x: x[0])


def ongoing_rows():
    """Parse the "In progress"/"Todo" table rows of ongoing.md; returns [(task, pri, due, progress, icon)]"""
    rows = []
    section = None
    for line in open(ONGOING, encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
        if not line.startswith("|") or section not in ("In progress", "Todo"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("task", "Task", "------"):
            continue
        rows.append((cells[0], cells[1], cells[2], cells[3], "🚧" if section == "In progress" else "🗓"))
    return rows


def days_left(due):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", due)
    if not m:
        return None
    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (d - date.today()).days


def bar(cur, total, width=16):
    filled = max(0, min(width, round(width * cur / total)))
    return "█" * filled + "░" * (width - filled)


def today_journal_tasks():
    """Read today's daily note for unchecked per-day tasks (checkbox + bold stripped)"""
    diary = os.path.join(PROJ, NOTES_DIR, date.today().strftime("%Y-%m-%d") + ".md")
    items = []
    if not os.path.exists(diary):
        return items
    section = False
    for line in open(diary, encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line.startswith(TODAY_SECTION)
            continue
        if section and line.startswith("- [ ]"):
            items.append(re.sub(r"\*+", "", line[6:]).strip())
    return items


def gmail_count():
    try:
        s = json.load(open(GMAIL_STATE))
        return len(s.get("_msgids", []))
    except Exception:
        return 0


def main():
    now = datetime.now()
    lines = [f"☀️ Daily digest {now.strftime('%m-%d')} ({WEEKDAYS[now.weekday() * 3:now.weekday() * 3 + 3]})"]

    evs = today_events()
    lines.append("\n📅 Today's schedule")
    if evs:
        for hhmm, cal, title in evs:
            tag = "" if cal == "Agent" else f"({cal})"
            lines.append(f"▸ {hhmm} {title}{tag}")
    elif evs is None:
        lines.append("▸ No schedule snapshot (macOS not synced; today's events may be missing)")
    else:
        lines.append("▸ No events today")

    lines.append("\n📋 Today's tasks")
    tts = today_journal_tasks()
    if tts:
        lines.extend(f"▸ {t}" for t in tts)
    else:
        lines.append("▸ None (no per-day tasks listed in the daily note)")

    lines.append("\n🔥 Long-term tasks")
    soon = []
    for task, pri, due, prog, icon in ongoing_rows():
        badge = PRI.get(pri, "⚪")
        dl = days_left(due)
        dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", due)
        dstr = dm.group(0)[5:] if dm else None
        head = f"{badge} {task}"
        if dl is not None:
            head += f" | ⏳{dstr} {dl}d left" if dl <= 7 else f" | {dstr}"
        lines.append(head)

        # Gmail special: real progress bar (enable with GMAIL_TOTAL > 0)
        if "Gmail" in task and GMAIL_TOTAL > 0:
            cnt = gmail_count()
            lines.append(f"  └ {bar(cnt, GMAIL_TOTAL)} {cnt}/{GMAIL_TOTAL}")
            continue
        # Others: stage dots
        st = next(((a, b) for k, (a, b) in STAGES.items() if k in task), None)
        if st:
            dots = "●" * st[0] + "○" * (st[1] - st[0])
            lines.append(f"  └ {dots} {prog}")
        else:
            lines.append(f"  └ {prog}")
        if dl is not None and 0 <= dl <= 7:
            soon.append(f"{task} ({due})")

    if soon:
        lines.append("\n⏰ Due within 7 days")
        lines.extend(f"▸ {s}" for s in soon)

    subprocess.run([NOTIFY, "\n".join(lines)])


if __name__ == "__main__":
    main()
