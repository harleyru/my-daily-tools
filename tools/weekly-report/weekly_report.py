#!/usr/bin/env python3
"""Weekly report: aggregate this week's daily-note accomplishments + long-term task
progress → push via notify.sh. Call from cron/systemd every Friday.

Note: section titles below ("Accomplishments", "In progress") are the headings of
your markdown notes (journal/YYYY-MM-DD.md and ongoing.md) — rename to match yours.
"""
import re, subprocess, os
from datetime import datetime, date, timedelta

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.environ.get("DAILY_BASE") or os.path.dirname(TOOLS)
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")

DONE_SECTION = "Accomplishments"   # heading of the daily note's "done" section
NOTES_DIR = "journal"              # your daily-notes directory (日记/ in the author's setup)


def daily_done(fname):
    """Extract the "done" bullets from a daily note"""
    out, section = [], None
    try:
        f = open(os.path.join(PROJ, NOTES_DIR, fname), encoding="utf-8")
    except OSError:
        return []
    for line in f:
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
            continue
        if section == DONE_SECTION and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
    return out


def _mask_parens(text):
    """Replace parenthesized groups (full/half width, nested inside-out) with
    placeholders so paren content doesn't take part in '→' or '/' splitting"""
    ph = []
    while True:
        m = re.search(r"[（(][^（）()]*[）)]", text)
        if not m:
            break
        ph.append(m.group(0))
        text = text[:m.start()] + "\x01%d\x01" % (len(ph) - 1) + text[m.end():]
    return text, ph


def _unmask(text, ph):
    for i, p in enumerate(ph):
        text = text.replace("\x01%d\x01" % i, p)
    return text


def _strip_parens(text):
    """Remove all paren groups (full/half width) with padding; supports nesting;
    collapse spaces, none between CJK chars"""
    while True:
        nt = re.sub(r"[（(][^（）()]*[）)]", " ", text)
        if nt == text:
            break
        text = nt
    t = re.sub(r" +", " ", text)
    t = re.sub(r"([一-鿿])\s+([一-鿿])", r"\1\2", t)          # no space between CJK chars
    return re.sub(r"\s+([，。；;、)）])", r"\1", t).strip()    # no space before punctuation


def shrink(text, maxlen=40):
    """Condense: first segment by '→' (title) → strip parens → if still too long,
    cut at ':' to keep the title. Details stay in the daily note."""
    masked, ph = _mask_parens(text)
    t = _unmask(masked.split("→", 1)[0], ph)
    t = _strip_parens(t)
    t = t.rstrip("：:。;；,，")
    if len(t) > maxlen:
        t = re.split(r"[：:]", t, 1)[0].strip()
        if len(t) > maxlen:
            t = t[:maxlen].rstrip() + "…"
    return t


def status(progress):
    """Current status of a long-term task: parens protected; split by '→', else '/';
    scan segments back-to-front for status markers (current/wip/todo/next/in progress);
    if the first segment carries the marker the whole row shares that status"""
    masked, ph = _mask_parens(progress)
    segs = [s.strip() for s in masked.split("→")]
    if len(segs) == 1:
        segs = [s.strip() for s in segs[0].split("/") if s.strip()]
    segs = [_unmask(s, ph) for s in segs if s.strip()]
    for i, s in enumerate(reversed(segs)):
        if any(k in s for k in ("current", "wip", "todo", "next", "in progress", "🚧")):
            if i == len(segs) - 1:          # marker in the first segment → whole row shares it
                return shrink(progress, 45)
            return shrink(s, 45)
    return shrink(segs[-1], 45)


def main():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    lines = [f"📊 Weekly report ({monday.strftime('%m-%d')} ~ {today.strftime('%m-%d')})"]

    # This week's accomplishments
    done, days = [], 0
    for i in range((today - monday).days + 1):
        fname = (monday + timedelta(days=i)).strftime("%Y-%m-%d") + ".md"
        items = daily_done(fname)
        if items:
            days += 1
            done.extend(items)
    lines.append("\n✅ This week")
    if done:
        for d in done:
            d = d.strip()
            if d in ("none", "n/a", "—", "-", "None"):
                continue
            lines.append(f"• {shrink(d)}")
    else:
        lines.append("• No accomplishments recorded this week")

    # Long-term task progress
    lines.append("\n🚧 Long-term tasks")
    section = None
    for line in open(os.path.join(PROJ, "ongoing.md"), encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
        if section == "In progress" and line.startswith("|") and "task" not in line and "---" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4:
                lines.append(f"• [{cells[1]}] {cells[0]} | {status(cells[3])}")

    # Next-week preview: calendar snapshot synced from macOS (data/calendar_today.json)
    import json as _json
    try:
        _snap = _json.load(open(os.path.join(PROJ, "data", "calendar_today.json"), encoding="utf-8"))
        _n = _snap.get("week_count")
        if _n is None or _snap.get("date") != date.today().strftime("%Y-%m-%d"):
            _n = "?(macOS not synced)"
    except (OSError, ValueError):
        _n = "?(macOS not synced)"
    lines.append(f"\n🗓 Next week: {_n} events in the next 7 days; long-term tasks in the daily digest")

    subprocess.run([NOTIFY, "\n".join(lines)])


if __name__ == "__main__":
    main()
