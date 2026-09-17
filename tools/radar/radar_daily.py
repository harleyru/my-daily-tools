#!/usr/bin/env python3
"""Research radar daily orchestration: fetch → prefilter → editorial (claude -p)
→ contract check → static site → notify.

Called once a day by the scheduler. Logs go to stdout (redirect to a log file yourself).
The editorial stage runs headless claude -p; output is validated against the frozen v1
contract, with one automatic retry on violations.
Usage: python3 radar_daily.py [YYYY-MM-DD] [--skip-fetch] [--skip-editorial] [--skip-notify]
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

BASE = os.environ.get("DAILY_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))  # radar_fetch.py / radar_prefilter.py live here
RADAR = os.path.join(BASE, "data", "radar")
RAW = os.path.join(RADAR, "raw")
EDITORIAL = os.path.join(RADAR, "editorial")
PUBLIC = os.path.join(RADAR, "public")
SITE = os.path.join(BASE, "reports", "radar.html")
CONFIG = json.load(open(os.path.join(RADAR, "config.json"), encoding="utf-8"))
PROMPT_TPL = open(os.path.join(RADAR, "daily-prompt.md"), encoding="utf-8").read()
PROFILE = open(os.path.join(RADAR, "interest-profile.md"), encoding="utf-8").read()
NOTIFY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notify", "notify.sh")

CARD_KEYS = ["id", "track", "type", "score", "crossHits", "title", "source",
             "sourceUrl", "author", "publishedAt", "summary", "whyItMatters", "extra"]
TRACKS = ("T1", "T2", "T3", "T4", "T5")
TYPES = ("paper", "product", "capital", "insight")


def valid_iso(s):
    if not isinstance(s, str) or not s:
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_day(day):
    """frozen v1 contract (ported from pipeline/frozen_contract.mjs). Returns a list of violations."""
    v = []
    add = lambda p, m: v.append(f"{p}: {m}")
    root_keys = {"date", "generatedAt", "status", "stats", "cards"}
    if not isinstance(day, dict):
        return ["$: must be an object"]
    for k in ("date", "generatedAt", "status", "stats", "cards"):
        if k not in day:
            add("$." + k, "is required")
    for k in day:
        if k not in root_keys and k != "sourceHealth":
            add(f"$.{k}", "is not in frozen v1 contract")
    if day.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day["date"])):
        add("$.date", "must be YYYY-MM-DD")
    if "generatedAt" in day and not valid_iso(day.get("generatedAt")):
        add("$.generatedAt", "must be ISO8601")
    if day.get("status") not in ("ok", "partial"):
        add("$.status", "must be ok or partial")
    stats = day.get("stats")
    if not isinstance(stats, dict):
        add("$.stats", "must be an object")
    else:
        for k in ("fetched", "prefiltered", "scored", "cards"):
            if not isinstance(stats.get(k), (int, float)) or stats[k] < 0:
                add(f"$.stats.{k}", "must be a finite non-negative number")
    cards = day.get("cards")
    if not isinstance(cards, list):
        add("$.cards", "must be an array")
        return v
    if isinstance(stats, dict) and stats.get("cards") != len(cards):
        add("$.stats.cards", "must equal cards.length")
    prior = float("inf")
    for i, c in enumerate(cards):
        lbl = f"$.cards[{i}]"
        if not isinstance(c, dict):
            add(lbl, "must be an object")
            continue
        for k in CARD_KEYS:
            if k not in c:
                add(f"{lbl}.{k}", "is required")
        for k in c:
            if k not in CARD_KEYS:
                add(f"{lbl}.{k}", "is not in frozen v1 contract")
        if not isinstance(c.get("id"), str) or not c["id"]:
            add(f"{lbl}.id", "must be a non-empty string")
        if c.get("track") not in TRACKS:
            add(f"{lbl}.track", "must be T1 through T5")
        if c.get("type") not in TYPES:
            add(f"{lbl}.type", "must be paper, product, capital, or insight")
        if not isinstance(c.get("score"), (int, float)):
            add(f"{lbl}.score", "must be a finite number")
        else:
            if c["score"] > prior:
                add(f"{lbl}.score", "cards must be sorted by descending score")
            prior = c["score"]
        if not isinstance(c.get("crossHits"), list) or not all(
                isinstance(x, str) and x for x in c.get("crossHits", [])):
            add(f"{lbl}.crossHits", "must be an array of non-empty strings")
        for k in ("title", "source", "sourceUrl", "summary"):
            if not isinstance(c.get(k), str) or not c[k]:
                add(f"{lbl}.{k}", "must be a non-empty string")
        if c.get("author") is not None and not (isinstance(c["author"], str) and c["author"]):
            add(f"{lbl}.author", "must be null or a non-empty string")
        if c.get("publishedAt") is not None and not valid_iso(c.get("publishedAt")):
            add(f"{lbl}.publishedAt", "must be null or ISO8601")
        if c.get("whyItMatters") is not None and not (
                isinstance(c["whyItMatters"], str) and c["whyItMatters"]):
            add(f"{lbl}.whyItMatters", "must be null or a non-empty string")
        if not isinstance(c.get("extra"), dict):
            add(f"{lbl}.extra", "must be an object")
    return v


def validate_ledger(led):
    v = []
    if not isinstance(led, dict):
        return ["$: must be an object"]
    entries = led.get("entries")
    if not isinstance(entries, list):
        return ["$.entries: must be an array"]
    if led.get("scoredTotal") != len(entries):
        v.append("$.scoredTotal: must equal entries.length")
    if led.get("selectedTotal") != sum(1 for e in entries if e.get("decision") == "selected"):
        v.append("$.selectedTotal: must equal selected count")
    for i, e in enumerate(entries):
        lbl = f"$.entries[{i}]"
        if e.get("score", 0) >= 5:
            for k in ("id", "score", "track", "type", "decision", "reason"):
                if k not in e:
                    v.append(f"{lbl}.{k}: required for score>=5")
            for k in e:
                if k not in ("id", "score", "track", "type", "decision", "reason"):
                    v.append(f"{lbl}.{k}: not allowed for score>=5")
        else:
            if set(e) != {"id", "score", "decision"}:
                v.append(f"{lbl}: score<5 must have only id/score/decision")
            if e.get("decision") != "rejected":
                v.append(f"{lbl}.decision: score<5 must be rejected")
    return v


def find_claude():
    """Resolve the editorial CLI: RADAR_EDITOR first (point it at any
    Anthropic-compatible CLI wrapper, e.g. one that routes to another provider),
    then plain `claude`.

    Every candidate goes through shutil.which first -- RADAR_EDITOR is commonly a
    bare command name, and a plain os.path.exists() on that would fail even when
    the command is on PATH. The ~/.local/bin fallback covers schedulers that run
    with a narrow PATH.
    """
    for cand in (os.environ.get("RADAR_EDITOR"), "claude"):
        if not cand:
            continue
        p = shutil.which(cand) or os.path.expanduser(
            "~/.local/bin/" + os.path.basename(cand))
        if os.path.exists(p):
            return p
    return None


def run_editorial(date, candidates, meta):
    """Run the editorial stage via claude -p, returns (day, ledger);
    one automatic retry on contract violations."""
    claude = find_claude()
    if not claude:
        print("❌ claude CLI not found")
        return None, None
    # keep only the fields the contract needs + text, to control prompt size
    slim = [{"id": it["id"], "source": it["source"], "sourceUrl": it["sourceUrl"],
             "title": it["title"], "author": it.get("author"),
             "publishedAt": it.get("publishedAt"), "text": it.get("text", ""),
             "trackHints": it.get("trackHints", {})}
            for it in candidates[:CONFIG.get("maxEditorialItems", 40)]]
    payload = {"__meta__": meta, "candidates": slim}
    prompt = (PROMPT_TPL.replace("{DATE}", date).replace("{PROFILE}", PROFILE)
              + "\n\n## Input for the day\n" + json.dumps(payload, ensure_ascii=False))

    for attempt in (1, 2):
        print(f"[editorial] claude -p attempt {attempt} ({len(slim)} candidates)...")
        try:
            r = subprocess.run([claude, "-p", "--output-format", "json"],
                               input=prompt, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            print("❌ editorial timeout (900s)")
            return None, None
        if r.returncode != 0:
            print(f"❌ claude exit code {r.returncode}: {r.stderr[-300:]}")
            return None, None
        out = _parse_result(r.stdout)
        if out is None:
            print("❌ output is not parseable JSON")
            continue
        day, ledger = out.get("day"), out.get("ledger")
        if not isinstance(day, dict) or not isinstance(ledger, dict):
            print("❌ missing day/ledger objects")
            continue
        day_v = validate_day(day)
        led_v = validate_ledger(ledger)
        if not day_v and not led_v:
            return day, ledger
        print(f"⚠️ {len(day_v) + len(led_v)} contract violations, retrying with feedback")
        prompt += "\n\n## Last output violated the contract — fix it and output again\n" + \
                  "\n".join((day_v + led_v)[:30])
    return None, None


def _parse_result(stdout):
    """claude -p --output-format json result: {"result": "<model output>"}."""
    try:
        outer = json.loads(stdout)
    except json.JSONDecodeError:
        outer = None
    raw = outer.get("result") if isinstance(outer, dict) and "result" in outer else stdout
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.M)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  JSON parse failed: {e}")
        _dump_bad_output(raw, e)
        return None


def _dump_bad_output(raw, err):
    """On a parse failure, persist the raw output so the cause can be diagnosed
    afterwards -- the terminal log alone is not enough to fix a prompt or a model
    that has started emitting prose around the JSON."""
    try:
        d = os.path.join(BASE, "logs")
        os.makedirs(d, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        p = os.path.join(d, f"radar_editorial_fail_{ts}.txt")
        pos = getattr(err, "pos", None)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {err}\n# len={len(raw)} pos={pos}\n")
            if pos is not None:
                f.write(f"# context around the error: ...{raw[max(0, pos - 200):pos + 200]}...\n")
            f.write("\n" + raw)
        print(f"  raw output saved to {p}")
    except Exception as ex:
        print(f"  (could not save raw output: {ex})")


def build_site():
    """public/*.json → reports/radar.html (self-contained, dark mode)."""
    days = []
    for p in sorted(glob.glob(os.path.join(PUBLIC, "*.json")), reverse=True):
        try:
            days.append(json.load(open(p, encoding="utf-8")))
        except Exception:
            continue
        if len(days) >= CONFIG.get("siteDays", 7):
            break
    if not days:
        print("⚠️ public/ is empty, skipping site build")
        return

    # validated palette: one categorical hue per track (light/dark)
    light = {"T1": "#2a78d6", "T2": "#eb6834", "T3": "#1baf7a", "T4": "#eda100", "T5": "#e87ba4"}
    dark = {"T1": "#3987e5", "T2": "#d95926", "T3": "#199e70", "T4": "#c98500", "T5": "#d55181"}
    sections = []
    for d in days:
        cards = []
        for c in d.get("cards", []):
            score = f'{c["score"]:.1f}'
            why = f'<p class="why">{esc(c["whyItMatters"])}</p>' if c.get("whyItMatters") else ""
            xhits = " · ".join(esc(x) for x in c.get("crossHits", []))
            cards.append(f"""<article class="card">
  <div class="card-head">
    <span class="dot t-{c['track']}"></span><span class="track">{esc(c['track'])}</span>
    <span class="type">{esc(c['type'])}</span>
    <a class="title" href="{esc(c['sourceUrl'])}" target="_blank" rel="noopener">{esc(c['title'])}</a>
    <span class="score">{score}</span>
  </div>
  <p class="summary">{esc(c['summary'])}</p>
  {why}
  <div class="meta">{esc(c['source'])} · {esc(c['author'] or '')} · {esc((c['publishedAt'] or '')[:10])} · cross: {xhits}</div>
</article>""")
        date_label = d.get("date", "?")
        open_attr = " open" if d is days[0] else ""
        sections.append(f"""<details class="day"{open_attr}>
<summary>{esc(date_label)} · {len(d.get('cards', []))} cards · {esc(d.get('status', ''))}</summary>
{''.join(cards)}
</details>""")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Radar</title>
<style>
:root {{ --bg:#fafafa; --surface:#ffffff; --ink:#1a1a1a; --ink2:#555555; --muted:#8a8a8a; --line:#e3e3e3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16161a; --surface:#1e1e24; --ink:#ececf1; --ink2:#b4b4be; --muted:#7a7a85; --line:#33333d; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.65 -apple-system,"Segoe UI",sans-serif; }}
header {{ padding:28px 24px 8px; max-width:900px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
header p {{ color:var(--muted); margin:0 0 16px; font-size:13px; }}
main {{ max-width:900px; margin:0 auto; padding:0 24px 48px; }}
.day {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; margin-bottom:14px; }}
.day summary {{ cursor:pointer; padding:12px 16px; font-weight:600; font-size:14px; color:var(--ink2); }}
.card {{ padding:14px 18px; border-top:1px solid var(--line); }}
.card-head {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }}
.dot {{ width:9px; height:9px; border-radius:50%; align-self:center; flex:none; }}
.track {{ font-size:12px; font-weight:700; color:var(--muted); letter-spacing:.5px; }}
.type {{ font-size:11px; color:var(--muted); border:1px solid var(--line); border-radius:4px; padding:0 6px; }}
.title {{ color:var(--ink); text-decoration:none; font-weight:600; font-size:15px; }}
.title:hover {{ text-decoration:underline; }}
.score {{ margin-left:auto; font-weight:700; color:var(--ink); font-variant-numeric:tabular-nums; }}
.summary {{ margin:6px 0 2px; color:var(--ink); }}
.why {{ margin:2px 0; color:var(--ink2); font-style:italic; font-size:13.5px; }}
.meta {{ color:var(--muted); font-size:12px; margin-top:4px; }}
.t-T1 {{ background:{light['T1']}; }} .t-T2 {{ background:{light['T2']}; }} .t-T3 {{ background:{light['T3']}; }}
.t-T4 {{ background:{light['T4']}; }} .t-T5 {{ background:{light['T5']}; }}
@media (prefers-color-scheme: dark) {{
.t-T1 {{ background:{dark['T1']}; }} .t-T2 {{ background:{dark['T2']}; }} .t-T3 {{ background:{dark['T3']}; }}
.t-T4 {{ background:{dark['T4']}; }} .t-T5 {{ background:{dark['T5']}; }}
}}
</style></head><body>
<header><h1>📡 Research Radar</h1><p>arXiv · RSS · HN → keyword prefilter → LLM editorial scoring. Interest profile: data/radar/interest-profile.md</p></header>
<main>{''.join(sections)}</main>
</body></html>"""
    with open(SITE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ site → {SITE} ({len(days)} days)")


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _clip(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def notify(date, day):
    cards = day.get("cards", [])
    if not cards:
        print("no cards, skipping push")
        return
    lines = [f"📡 Research Radar {date[5:]} | {len(cards)} cards"]
    for c in cards[:5]:
        url = c["sourceUrl"]
        if len(url) > 60:
            url = url[:60] + "…"
        lines.append(f"▸ {c['score']:.1f} [{c['track']}] {_clip(c['title'], 60)}")
        if c.get("summary"):
            lines.append(f"   📝 {_clip(c['summary'], 70)}")
        if c.get("whyItMatters"):
            lines.append(f"   💬 {_clip(c['whyItMatters'], 90)}")
        lines.append(f"   🔗 {url}")
    subprocess.run([NOTIFY, "\n".join(lines)])
    print("✅ pushed")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else datetime.now().strftime("%Y-%m-%d")
    skip_fetch = "--skip-fetch" in sys.argv
    skip_edit = "--skip-editorial" in sys.argv
    skip_notify = "--skip-notify" in sys.argv
    os.makedirs(EDITORIAL, exist_ok=True)
    os.makedirs(PUBLIC, exist_ok=True)

    if not skip_fetch:
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "radar_fetch.py"), date])
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "radar_prefilter.py"), date])

    pre_path = os.path.join(RAW, date, "prefiltered.json")
    if not os.path.exists(pre_path):
        print(f"❌ no prefilter result {pre_path}, exiting")
        sys.exit(1)
    pre = json.load(open(pre_path, encoding="utf-8"))
    n_slim = min(len(pre["items"]), CONFIG.get("maxEditorialItems", 40))
    meta = {"fetched": pre["fetched"], "prefiltered": pre["prefiltered"], "scored": n_slim}

    if skip_edit:
        print("--skip-editorial, skipping to site/push")
    elif not pre["items"]:
        print("⚠️ no new candidates (seen table deduped), skipping editorial")
    else:
        day, ledger = run_editorial(date, pre["items"], meta)
        if day is None:
            print("❌ editorial failed, day status = missed, skipping site/push")
            sys.exit(2)
        day.setdefault("date", date)
        if not day.get("stats"):
            day["stats"] = {"fetched": meta["fetched"], "prefiltered": meta["prefiltered"],
                            "scored": n_slim, "cards": len(day.get("cards", []))}
        with open(os.path.join(EDITORIAL, f"{date}.json"), "w", encoding="utf-8") as f:
            json.dump(day, f, ensure_ascii=False, indent=1)
        with open(os.path.join(EDITORIAL, f"{date}.ledger.json"), "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=1)
        with open(os.path.join(PUBLIC, f"{date}.json"), "w", encoding="utf-8") as f:
            json.dump(day, f, ensure_ascii=False, indent=1)
        print(f"✅ editorial → {len(day.get('cards', []))} cards")

    build_site()
    pub_path = os.path.join(PUBLIC, f"{date}.json")
    if not skip_notify and os.path.exists(pub_path):
        notify(date, json.load(open(pub_path, encoding="utf-8")))


if __name__ == "__main__":
    main()
