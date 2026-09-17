#!/usr/bin/env python3
"""Research radar prefilter: keyword recall + sqlite seen dedupe
→ data/radar/raw/YYYY-MM-DD/prefiltered.json

Keywords are grouped by track; recall-only (the editorial stage makes the semantic
decision — better to over-recall). The seen table prevents re-scoring duplicates
within the 96h lookback window.
Usage: python3 radar_prefilter.py [YYYY-MM-DD]
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

BASE = os.environ.get("DAILY_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR = os.path.join(BASE, "data", "radar")
RAW = os.path.join(RADAR, "raw")
CONFIG = json.load(open(os.path.join(RADAR, "config.json"), encoding="utf-8"))
SEEN_DB = os.path.join(RADAR, "seen.sqlite")

# Drop items published longer ago than this — history that fell outside the fetch window.
SITE_DAYS = int(CONFIG.get("siteDays", 7))
# Junk/placeholder titles (e.g. "not much happened today"), matched case-insensitively as
# substrings against the title only. Empty unless titleBlocklist is set in config.
BLOCKLIST = [b.lower() for b in CONFIG.get("titleBlocklist", [])]


def init_db():
    con = sqlite3.connect(SEEN_DB)
    con.execute("CREATE TABLE IF NOT EXISTS seen (uid TEXT PRIMARY KEY, first_seen TEXT)")
    return con


def match_tracks(title, text):
    """Returns {track: [matched keywords...]}. Case-insensitive substring match."""
    hay = (title + " " + text).lower()
    hits = {}
    for track, kws in CONFIG["keywords"].items():
        got = [k for k in kws if k.lower() in hay]
        if got:
            hits[track] = got
    return hits


def _parse_time(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def old_entry(it, now):
    """True if published more than siteDays ago -> drop during prefilter.

    Deliberately conservative: a missing or unparseable publishedAt returns False (keep
    the item), so a datetime parsing failure can never silently drop a candidate.
    """
    t = _parse_time(it.get("publishedAt"))
    if t is None:
        return False
    return (now - t).total_seconds() > SITE_DAYS * 86400


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    items_path = os.path.join(RAW, date, "items.json")
    if not os.path.exists(items_path):
        print(f"❌ missing {items_path}, run radar_fetch.py first")
        sys.exit(1)
    data = json.load(open(items_path, encoding="utf-8"))
    fetched = len(data["items"])

    con = init_db()
    now_dt = datetime.now(timezone.utc)   # for date arithmetic
    now = now_dt.isoformat()              # for what we write to the db
    kept = []
    seen = set()
    renewed = set()
    dropped_old = 0
    dropped_bl = 0
    for it in data["items"]:
        title = it.get("title", "")
        # Junk title blocklist (placeholders/noise, e.g. "not much happened today")
        if BLOCKLIST and any(b in title.lower() for b in BLOCKLIST):
            dropped_bl += 1
            continue
        hits = match_tracks(title, it.get("text", ""))
        if not hits:
            continue
        # Age filter: anything older than siteDays is history outside the fetch window.
        if old_entry(it, now_dt):
            dropped_old += 1
            continue
        uid = it["id"]
        row = con.execute("SELECT first_seen FROM seen WHERE uid=?", (uid,)).fetchone()
        if row:
            first = _parse_time(row[0])
            # seen would otherwise pin entries that reached the candidate list but the
            # editorial stage never picked -> let them through again once out of window,
            # restarting their clock.
            if first is None or (now_dt - first).total_seconds() < SITE_DAYS * 86400:
                continue
            renewed.add(uid)
        seen.add(uid)
        out = dict(it)
        out["trackHints"] = {t: len(k) for t, k in hits.items()}
        # rank proxy: keyword hit density + cross-track bonus (multi-dimensional
        # candidates go to the editorial stage first)
        out["rank"] = sum(out["trackHints"].values()) + 5 * max(0, len(out["trackHints"]) - 1)
        kept.append(out)
    kept.sort(key=lambda x: -x["rank"])
    if seen:
        con.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(u, now) for u in seen])
    if renewed:
        con.executemany("UPDATE seen SET first_seen=? WHERE uid=?",
                        [(now, u) for u in renewed])
    con.commit()
    con.close()

    print(f"fetched {fetched} → {len(kept)} keyword hits (after dedupe; "
          f"{dropped_old} stale / {dropped_bl} blocklisted / {len(renewed)} re-scored past window)")
    out = {"date": date, "fetched": fetched, "prefiltered": len(kept),
           "generatedAt": now, "items": kept}
    with open(os.path.join(RAW, date, "prefiltered.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
