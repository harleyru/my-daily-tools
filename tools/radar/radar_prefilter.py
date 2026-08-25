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


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    items_path = os.path.join(RAW, date, "items.json")
    if not os.path.exists(items_path):
        print(f"❌ missing {items_path}, run radar_fetch.py first")
        sys.exit(1)
    data = json.load(open(items_path, encoding="utf-8"))
    fetched = len(data["items"])

    con = init_db()
    now = datetime.now(timezone.utc).isoformat()
    kept = []
    seen = set()
    for it in data["items"]:
        hits = match_tracks(it.get("title", ""), it.get("text", ""))
        if not hits:
            continue
        uid = it["id"]
        cur = con.execute("SELECT 1 FROM seen WHERE uid=?", (uid,))
        if cur.fetchone():
            continue
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
    con.commit()
    con.close()

    print(f"fetched {fetched} → {len(kept)} keyword hits (after dedupe)")
    out = {"date": date, "fetched": fetched, "prefiltered": len(kept),
           "generatedAt": now, "items": kept}
    with open(os.path.join(RAW, date, "prefiltered.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
