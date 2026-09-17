#!/usr/bin/env python3
"""Discord task bridge (pool mode): poll channel messages mentioning the bot → write to a
task pool → reply. Pure stdlib (urllib, no pip deps), run every 60s from cron/systemd/launchd.

Task pool: data/discord/tasks.json. When a user says "handle the Discord todo list" in a
Claude session, the pool is executed there, then tasks are marked done with --done.

Trigger words below (EVENT_VERBS / QUERY_WORDS / QUERY_VERBS / RULE_WORDS /
AUTO_QUESTION / FORCE_POOL) are constants — match them to your own language and usage.

Usage:
    discord_tasks.py --discover            # list servers/channels, pick one
    discord_tasks.py --use-channel <id>    # write the channel to config
    discord_tasks.py --add-role <id>       # add a trigger role (users often @ a role instead of the bot)
    discord_tasks.py --poll                # poll new @ messages (incl. trigger roles): pool + reply; list-type queries answer inline
                                           # rule queries: "radar" → latest radar summary, "mail pending" → ad-review list
    discord_tasks.py --list                # print the task pool
    discord_tasks.py --done 3 [--result "done content"]   # mark done, optionally reply with a result
    discord_tasks.py --cleanup [--keep N]  # archive finished tasks, keep the last N (default 10)
    discord_tasks.py --post "text"         # post a message to the channel
    discord_tasks.py --file <path> [--text "caption"]     # upload a local file to the channel
    discord_tasks.py --answer "text"       # test the auto-answer mode locally (no Discord, prints result)
    discord_tasks.py --task "text"         # test the auto-research mode locally (read-only web, no Discord)

Token in Keychain (service: discord_bot_token); channel id / trigger roles in data/discord/config.json
"""
import datetime
import fcntl
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

# Sibling lightweight Markdown -> HTML renderer (pure stdlib). Falls back to a <pre>
# block if the module is missing, so a partial install still produces a valid page.
# The fallback escapes, so model output cannot inject markup in either path.
try:
    from markdown_render import md_to_html
except ImportError:
    def md_to_html(t):
        return "<pre>%s</pre>" % html.escape(t)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_DIR = os.path.join(BASE, "data", "discord")
CONFIG = os.path.join(CFG_DIR, "config.json")
TASKS = os.path.join(CFG_DIR, "tasks.json")
STATE = os.path.join(CFG_DIR, "state.json")
ARCHIVE = os.path.join(CFG_DIR, "archive.json")
EVENT_QUEUE = os.path.join(CFG_DIR, "pending_events.json")
LOCK = os.path.join(CFG_DIR, "poll.lock")
API = "https://discord.com/api/v10"

# --- trigger words (match to your own language) ---
EVENT_VERBS = ("add event", "schedule", "new event", "book")          # schedule-request verbs
FORCE_POOL = "force-pool"                                             # mention to force manual pool mode
# list-type queries: no pool entry, reply with the current task list instead
QUERY_WORDS = ("task list", "todo list", "todos", "list of tasks", "current tasks", "what tasks")
# "task/todo + query verb" also counts as a list query ("query tasks", "show me the tasks", ...)
QUERY_VERBS = ("query", "check", "show", "see", "look", "list", "display",
               "current", "now", "remaining", "left", "pending", "have")
# rule queries: trigger word + the remainder (after stripping it) empty or question-like → answer from local data
RULE_WORDS = {
    "radar": ("radar",),
    "mail": ("mail pending", "ad review", "ad pending", "pending mail"),
    "schedule": ("schedule", "calendar", "agenda"),
}
QUERY_MARKERS = ("pushed", "report", "what", "today", "tomorrow", "latest", "now",
                 "anything", "?")
# auto-answer trigger: question-like messages only; hands-on tasks go to the pool as usual
AUTO_QUESTION = ("?", "what", "why", "how", "explain", "summarize", "analyze", "translate",
                 "compare", "who", "when", "where", "which", "what is", "how to")
# section headings of your ongoing.md ("in-progress" / "todo" tables) — match to your own file
ONGOING_HEADERS = {
    "in-progress": "## 🚧 In progress",
    "todo": "## 🗓 Todo",
}
CALENDAR_NAME = "Agent"  # macOS calendar used for scheduled events


def _is_task_query(content):
    """List-query check; "force-pool" forces pool mode (same escape hatch as the auto modes)"""
    if FORCE_POOL in content:
        return False
    low = content.lower()
    if any(w in low for w in QUERY_WORDS):
        return True
    return ("task" in low or "todo" in low) and any(v in low for v in QUERY_VERBS)


def token():
    # .env first (server), fall back to Keychain (macOS)
    t = ""
    try:
        for line in open(os.path.join(BASE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line.startswith("discord_bot_token="):
                t = line.partition("=")[2].strip()
    except OSError:
        pass
    if not t:
        out = subprocess.run(["security", "find-generic-password", "-s", "discord_bot_token", "-w"],
                             capture_output=True, text=True)
        t = out.stdout.strip()
    if not t:
        sys.exit("❌ no discord_bot_token (need ~/daily/.env or Keychain)")
    return t


def api(method, path, data=None, timeout=30):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "Bot " + token())
    req.add_header("User-Agent", "my-daily-tools-discord-bridge")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def bot_id():
    st, me = api("GET", "/users/@me")
    if st != 200:
        sys.exit(f"❌ bot token invalid (HTTP {st})")
    return me["id"]


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # atomic write: tmp + fsync + os.replace, no half-written files on crash/concurrency
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def channel_id():
    cfg = load(CONFIG, {})
    if not cfg.get("channel_id"):
        sys.exit("❌ channel not configured: run --discover then --use-channel <id>")
    return cfg["channel_id"]


def post(text):
    if len(text) > 1900:  # Discord 2000-char limit per message, leave headroom
        text = text[:1890] + "\n…(truncated)"
    st, _ = api("POST", f"/channels/{channel_id()}/messages", {"content": text})
    print("✅ posted" if st == 200 else f"❌ post failed HTTP {st}")


def discover():
    st, me = api("GET", "/users/@me")
    print(f"bot: {me.get('username')} (id={me['id']})")
    st, guilds = api("GET", "/users/@me/guilds")
    if st != 200 or not guilds:
        print(f"bot is in no servers ({st}); add it with an invite link first")
        return
    for g in guilds:
        st2, chs = api("GET", f"/guilds/{g['id']}/channels")
        print(f"server: {g['name']} ({g['id']})")
        for c in chs:
            if c.get("type") == 0:  # 0 = text channel
                print(f"  #{c['name']}  id={c['id']}")


def parse_event_req(content):
    """Parse a schedule-request message → (title, start_str) or None.
    start_str: 'YYYY-MM-DD HH:MM' (server local time).
    Supports: today/tomorrow/day-after-tomorrow/weekday names ("next fri"), MM-DD/YYYY-MM-DD,
    HH:MM, "3pm"/"3:30pm", "half past 2"."""
    if not any(v in content.lower() for v in EVENT_VERBS):
        return None
    rest = content
    for v in sorted(EVENT_VERBS, key=len, reverse=True):  # longest verb first, avoid partial strips
        rest = re.sub(re.escape(v), " ", rest, flags=re.I)
    today = datetime.date.today()
    d = None
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", rest)
    if m:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.search(r"(\d{1,2})[-/](\d{1,2})", rest)
        if m:
            d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
        elif re.search(r"day after tomorrow", rest, re.I):
            d = today + datetime.timedelta(days=2)
        elif re.search(r"tomorrow", rest, re.I):
            d = today + datetime.timedelta(days=1)
        elif re.search(r"today", rest, re.I):
            d = today
        else:
            wk = re.search(r"(?:next\s+)?(mon|tue|wed|thu|fri|sat|sun)(?:day)?", rest, re.I)
            if wk:
                names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
                delta = (names[wk.group(1).lower()] - today.weekday()) % 7
                # "next X" or today → next week
                if re.search(r"next", rest, re.I) or delta == 0:
                    delta = 7 if delta == 0 else delta
                d = today + datetime.timedelta(days=delta)
            else:
                d = today
    hh, mm = 9, 0
    m = re.search(r"half past (\d{1,2})", rest, re.I)   # "half past 2" → 2:30
    if m:
        hh, mm = int(m.group(1)), 30
    else:
        m = re.search(r"(\d{1,2}):(\d{2})", rest)       # "14:30" / "3:30"
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d{1,2})\s*(am|pm)", rest, re.I)   # "3pm" / "9am"
            if m:
                hh, mm = int(m.group(1)), 0
    if re.search(r"\bpm\b", rest, re.I) and hh < 12:
        hh += 12
    if re.search(r"\bam\b", rest, re.I) and hh >= 12:
        hh %= 12
    title = re.sub(
        r"today|tomorrow|day after tomorrow|\bnext\b|"
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?\b|"
        r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}|\d{1,2}:\d{2}|"
        r"half past \d{1,2}|\d{1,2}\s*(?:am|pm)|add event|schedule|new event|book|at\b",
        " ", rest, flags=re.I)
    title = " ".join(title.split()).strip(" at ")
    if not title:
        title = "Event"
    return title, f"{d.isoformat()} {hh:02d}:{mm:02d}"


def enqueue_event(title, start, content, snowflake, author):
    """Queue a schedule request (written to the macOS calendar by the sync script).
    Serialized by poll.lock + atomic write, same pattern as tasks.json"""
    q = load(EVENT_QUEUE, {"items": [], "next_id": 1})
    eid = q["next_id"]
    q["next_id"] += 1
    q["items"].append({"id": eid, "title": title, "start": start,
                       "content": content, "snowflake": snowflake, "author": author,
                       "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "status": "pending", "result": None})
    save(EVENT_QUEUE, q)
    return eid


def event_replies():
    """End of poll: check schedule requests already written/failed by macOS → reply → remove from queue"""
    q = load(EVENT_QUEUE, {"items": []})
    done = [it for it in q["items"] if it["status"] in ("done", "failed")]
    if not done:
        return
    for it in done:
        if it["status"] == "done":
            post(f"📅 Event written to the {CALENDAR_NAME} calendar (iCloud-synced): {it['title']} @ {it['start']}")
        else:
            post(f"❌ Event write failed: {it['title']} @ {it['start']} — {it['result']}")
    q["items"] = [it for it in q["items"] if it["status"] not in ("done", "failed")]
    save(EVENT_QUEUE, q)
    print(f"✅ sent {len(done)} event replies, {len(q['items'])} still queued")


def _rule_query(content):
    """Rule-query check: trigger word hit and the remainder (after stripping it) is
    empty or question-like → treat as a query (no pool entry)"""
    hits = set()
    low = content.lower()
    for rule, words in RULE_WORDS.items():
        if not any(w.lower() in low for w in words):
            continue
        rest = content
        for w in words:
            rest = re.sub(re.escape(w), "", rest, flags=re.I)
        rest = re.sub(r"[\s　,，。.、:：~～!！*?？]+", "", rest)
        if not rest or any(k in rest for k in QUERY_MARKERS):
            hits.add(rule)
    return hits


def radar_reply():
    pub = os.path.join(BASE, "data", "radar", "public")
    try:
        days = sorted(f[:-5] for f in os.listdir(pub) if f.endswith(".json"))
    except OSError:
        days = []
    if not days:
        return "📡 Radar has no output yet (runs daily)."
    data = load(os.path.join(pub, days[-1] + ".json"), {})
    cards = data.get("cards", [])
    if not cards:
        return f"📡 Radar {days[-1]}: no cards."
    lines = [f"📡 Radar {days[-1]} · {len(cards)} cards:"]
    for c in cards:
        lines.append(f"• [{c.get('score', '?')}] {c.get('title', '')[:90]} ({c.get('source', '?')})")
        url = c.get("sourceUrl", "")
        if url:
            lines.append(f"  {url}")
    reply = "\n".join(lines)
    return reply if len(reply) <= 1800 else reply[:1790] + "\n…(truncated, full report in reports/radar.html)"


def mail_reply():
    pool = load(os.path.join(BASE, "data", "mail_ad_pending.json"), {"items": []})
    pend = [it for it in pool.get("items", []) if it.get("status") == "pending"]
    if not pend:
        return "📬 No pending ad-review mail."
    lines = [f"📬 Pending ad-review mail {len(pend)} (say 'delete #N' / 'keep #N' to handle):"]
    for it in pend:
        lines.append(f"  #{it['id']} {it.get('sender', '?')} — {it.get('subject', '')[:60]}")
    return "\n".join(lines)


def schedule_reply(content=""):
    """Read the macOS-synced calendar snapshot (data/calendar_today.json, pushed every 30 min),
    same source as the morning digest; content contains 'tomorrow' → tomorrow's events
    (snapshot 'tomorrow' field), else today's. Missing/stale snapshot → "macOS not synced"
    (servers have no osascript and do not read the macOS calendar)"""
    snap_path = os.path.join(BASE, "data", "calendar_today.json")
    try:
        snap = json.load(open(snap_path, encoding="utf-8"))
    except (OSError, ValueError):
        return "📅 Schedule snapshot missing (macOS not synced, retry after the next sync)."
    if snap.get("date") != time.strftime("%Y-%m-%d"):
        return "📅 Schedule snapshot stale (last sync %s) — macOS not synced." % snap.get("date", "?")
    tomorrow_q = "tomorrow" in content.lower()
    evs = [tuple(e) for e in snap.get("tomorrow" if tomorrow_q else "events", []) if len(e) == 3]
    evs.sort(key=lambda x: x[0])
    if not evs:
        return "📅 No events tomorrow." if tomorrow_q else "📅 No events today."
    lines = ["📅 Tomorrow:" if tomorrow_q else "📅 Today:"]
    for hhmm, cal, title in evs:
        tag = "" if cal == CALENDAR_NAME else f"({cal})"
        lines.append(f"• {hhmm} {title}{tag}")
    return "\n".join(lines)


def claude_cli():
    """Resolve the LLM CLI to shell out to, or None.

    CLAUDE_CLI wins -- point it at any Anthropic-compatible CLI (an absolute path, or a
    bare command name to look up on PATH). Otherwise probe PATH and then ~/.local/bin:
    cron/systemd/launchd run with a minimal PATH where shutil.which() alone finds nothing,
    which is the usual reason a command that works interactively fails from a timer.
    """
    env = os.environ.get("CLAUDE_CLI")
    if env:
        return env if os.path.exists(env) else shutil.which(env)
    p = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return p if os.path.exists(p) else None


def auto_answer(content):
    """Auto-answer mode (knowledge only, no tools): headless claude -p answers directly,
    returns (ok, text). No --allowedTools — the unapproved shell surface is rejected by the
    safety classifier, so hands-on tasks go through a session / the pool."""
    claude = claude_cli()
    if not claude:
        return False, "❌ claude CLI not found"
    cfg = load(CONFIG, {})
    prompt = (
        "You are the user's Discord auto-answer assistant (knowledge only, no tools). "
        "The user just @-mentioned you on Discord, original message:\n\n"
        f"---\n{content}\n---\n\n"
        "Answer directly, in the same language as the message. You have no tools — you "
        "cannot access local files or run commands; if the request needs hands-on work "
        "(changing files/running programs/reading local data), say 'this needs a Claude "
        "session'. Keep the answer under 1500 characters."
    )
    cmd = [claude, "-p", "--output-format", "text", "--tools", ""]
    model = cfg.get("auto_model", "")
    if model:
        cmd += ["--model", model]
    try:
        # prompt via stdin (same as the radar pipeline; --tools is a greedy variadic flag and
        # would swallow positional args); strip proxy-like env vars (a session may run behind
        # an ANTHROPIC_BASE_URL proxy — inheriting it would 401); a clean env uses the
        # claude.ai login, consistent with the radar pipeline
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.setdefault("HOME", os.path.expanduser("~"))
        r = subprocess.run(cmd, input=prompt, cwd=BASE, capture_output=True,
                           text=True, timeout=240, env=env)
    except subprocess.TimeoutExpired:
        return False, "⏱ auto-answer timed out (4 min)"
    if r.returncode != 0 or not (r.stdout or "").strip():
        return False, f"❌ claude -p failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[:200]}"
    return True, r.stdout.strip()


def auto_task(content):
    """Auto-execute mode (read-only web, explicitly authorized): WebSearch/WebFetch research,
    returns (ok, need_session, answer). Tasks that don't touch the machine are completed
    directly; tasks that need local changes (files/calendar/programs) return need_session=true
    via the JSON contract → pool for a session. No Bash/Write/Edit — safety by construction."""
    claude = claude_cli()
    if not claude:
        return False, False, "❌ claude CLI not found"
    prompt = (
        "You are the user's Discord auto-task assistant (read-only mode). The user just "
        "@-mentioned you on Discord, original message:\n\n"
        f"---\n{content}\n---\n\n"
        "You only have web research tools (WebSearch/WebFetch), no local tools "
        "(Bash/Write/Edit unavailable).\n"
        "Task classification:\n"
        "- Research / look up information → use the web, write a structured summary in the "
        "same language as the message.\n"
        "- Needs local access or changes (files/calendar/programs/databases/running code) → "
        "do not research, return need_session=true immediately.\n"
        "Final output must be JSON, nothing else:\n"
        '{"need_session": false, "answer": "summary (key points/numbers/sources, under 1000 chars, end with source links)"}'
    )
    cmd = [claude, "-p", "--output-format", "json",
           "--allowedTools", "WebSearch,WebFetch", "--max-turns", "12"]
    cfg = load(CONFIG, {})
    if cfg.get("auto_model", ""):
        cmd += ["--model", cfg["auto_model"]]
    try:
        # prompt via stdin (greedy variadic flag); strip proxy env vars (same as radar pipeline)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.setdefault("HOME", os.path.expanduser("~"))
        r = subprocess.run(cmd, input=prompt, cwd=BASE, capture_output=True,
                           text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return False, False, "⏱ auto-execute timed out (10 min)"
    if r.returncode != 0 or not (r.stdout or "").strip():
        return False, False, f"❌ claude -p failed (rc={r.returncode}): {(r.stderr or r.stdout or '')[:200]}"
    try:  # outer wrapper is claude -p's result; the model's final output (contract JSON) is in 'result'
        outer = json.loads(r.stdout)
        text = outer.get("result") or r.stdout
    except json.JSONDecodeError:
        text = r.stdout
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            c = json.loads(m.group(0))
            if isinstance(c, dict) and ("need_session" in c or "answer" in c):
                return True, bool(c.get("need_session")), (c.get("answer") or "").strip() or text[:800]
        except json.JSONDecodeError:
            pass
    return True, False, text.strip()[:1900]


def post_task_result(cid, content, ans):
    """Research done: reply with a summary + save the full report to reports/discord/ and upload it.

    Fail-safe chain. `ans` is the product of a headless LLM run that may have spent ten
    minutes on the network; no step in the render/save/upload path is allowed to discard
    it, and none is allowed to escape into poll() and kill the polling loop. Order:
    unconditionally write the raw .md first -> then try the HTML render, degrading to the
    .md as the attachment -> then reply and upload, each in its own independent try.

    Why this is structured this way: a CSS declaration `max-width:100%` inside a
    `%`-formatted string raised `ValueError: unsupported format character '}'`, which
    propagated out of poll() and silently lost two finished research reports. Hence the
    `%%` below, and hence one try per stage instead of one try around everything.
    """
    title = re.sub(r"\s+", " ", content)[:40]
    rdir = os.path.join(BASE, "reports", "discord")
    safe = re.sub(r"[^\w.\-#]", "_", title)
    stem = os.path.join(rdir, f"{time.strftime('%F')}-#{cid}-{safe}")
    raw = stem + ".md"
    try:  # stage 1: raw dump first -- everything below can fail without losing the result
        os.makedirs(rdir, exist_ok=True)
        with open(raw, "w", encoding="utf-8") as f:
            f.write(f"# Research #{cid}\n\n> Request: {content}\n\n{ans}\n")
    except Exception as e:
        print(f"⚠️ raw dump failed: {e!r}")
        raw = None
    path = None
    try:  # stage 2: HTML render; `path` stays None on failure -> the .md becomes the attachment
        body = md_to_html(ans)  # markdown -> rendered HTML (escaping happens inside)
        page = (
            "<!DOCTYPE html>\n<html lang='en'><head><meta charset='utf-8'>"
            "<title>Research #%d</title>\n<style>"
            "body{font-family:-apple-system,'Segoe UI',sans-serif;"
            "max-width:760px;margin:24px auto;padding:0 16px;line-height:1.7;color:#24292f}"
            "h1{font-size:20px;border-bottom:2px solid #0969da;padding-bottom:6px}"
            ".req{background:#f0f7ff;border-left:4px solid #0969da;padding:8px 12px;margin:12px 0;"
            "border-radius:4px}"
            "h2{font-size:17px;margin-top:24px;padding-bottom:4px;border-bottom:1px solid #d8dee4}"
            "h3{font-size:15px}h4{font-size:14px}"
            "table{border-collapse:collapse;margin:12px 0;display:block;overflow-x:auto;max-width:100%%}"
            "th,td{border:1px solid #d8dee4;padding:6px 10px;font-size:13.5px}"
            "th{background:#f6f8fa;font-weight:600}"
            "pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto;font-size:13px}"
            "code{background:#f6f8fa;padding:1px 5px;border-radius:4px;font-size:13px}"
            "pre code{background:none;padding:0}"
            "blockquote{margin:12px 0;padding:2px 14px;color:#57606a;border-left:4px solid #d8dee4}"
            "ul,ol{padding-left:24px;margin:8px 0}"
            "li{margin:4px 0}hr{border:none;border-top:1px solid #d8dee4;margin:16px 0}"
            "a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}"
            "p{margin:8px 0}"
            "</style></head><body><h1>Research #%d</h1>"
            "<div class='req'>Request: %s</div>\n<div class='content'>%s</div>\n"
            "</body></html>\n"
        ) % (cid, cid, html.escape(content), body)
        with open(stem + ".html", "w", encoding="utf-8") as f:
            f.write(page)
        path = stem + ".html"
    except Exception as e:
        print(f"⚠️ HTML render failed, falling back to the Markdown source: {e!r}")
    head = ans if len(ans) <= 1500 else ans[:290] + f"\n…(full {len(ans)} chars in the attachment)"
    try:  # stage 3: summary reply
        post(f"📄 Research done #{cid}: {head}")
    except Exception as e:
        print(f"⚠️ summary reply failed: {e!r}")
    attach = path or raw
    if attach and os.path.isfile(attach):
        # The os.path.isfile guard is load-bearing: send_file() reports a missing file
        # with sys.exit(), and SystemExit derives from BaseException -- `except Exception`
        # below would NOT catch it, so without this check a missing file would kill the
        # whole poll run from inside this function.
        try:  # stage 4: upload
            send_file(attach, f"📄 Research report #{cid}: {content[:60]}")
        except Exception as e:
            print(f"⚠️ attachment upload failed (source still at {attach}): {e!r}")
    else:
        print(f"⚠️ nothing to upload; research #{cid} survives only as the pool summary")


def send_file(path, caption):
    """Upload a local file to the Discord channel (multipart/form-data, pure stdlib)"""
    if not os.path.isfile(path):
        sys.exit(f"❌ file not found: {path}")
    fname = re.sub(r"[^\w.-]", "_", os.path.basename(path)) or "file"
    bnd = "----" + uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{bnd}\r\n"
            f'Content-Disposition: form-data; name="files[0]"; filename="{fname}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
        (f"\r\n--{bnd}\r\n"
         'Content-Disposition: form-data; name="content"\r\n\r\n').encode() + \
        caption.encode() + f"\r\n--{bnd}--\r\n".encode()
    req = urllib.request.Request(API + f"/channels/{channel_id()}/messages",
                                 method="POST", data=body)
    req.add_header("Authorization", "Bot " + token())
    req.add_header("User-Agent", "my-daily-tools-discord-bridge")
    req.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print("✅ file sent" if r.status == 200 else f"❌ send failed {r.status}")
    except urllib.error.HTTPError as e:
        print(f"❌ send failed HTTP {e.code}: {e.read().decode()[:200]}")


def ongoing_reply(tasks):
    """List-query reply: ongoing.md active/todo (main) + bridge pool pending (secondary)"""
    rows = {"in-progress": [], "todo": []}
    section = None
    try:
        with open(os.path.join(BASE, "ongoing.md"), encoding="utf-8") as f:
            for ln in f:
                if ln.startswith(ONGOING_HEADERS["in-progress"]):
                    section = "in-progress"
                    continue
                if ln.startswith(ONGOING_HEADERS["todo"]):
                    section = "todo"
                    continue
                if ln.startswith("## "):
                    section = None
                    continue
                if not section or not ln.startswith("|") or "---" in ln:
                    continue
                cells = [re.sub(r"\*+", "", c).strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) < 2 or cells[0] in ("task", "Task"):
                    continue
                rows[section].append((cells[0], cells[1] if len(cells) > 1 else "",
                                      cells[2] if len(cells) > 2 else "",
                                      cells[3] if len(cells) > 3 else ""))
    except OSError:
        pass
    labels = {"in-progress": "🚧 In progress", "todo": "🗓 Todo"}
    parts = []
    for section in ("in-progress", "todo"):
        if not rows[section]:
            continue
        parts.append(labels[section] + ":")
        for name, pri, dl, prog in rows[section]:
            meta = " ".join(x for x in (pri, dl) if x and x != "—")
            line = f"• {name}"
            if meta:
                line += f" [{meta}]"
            if prog:
                line += f" — {prog[:40]}"
            parts.append(line)
    if not parts:
        parts.append("(ongoing.md is empty)")
    pend = [it for it in tasks["items"] if it["status"] == "pending"]
    if pend:
        parts.append("📥 Bridge pool pending:")
        parts += [f"  #{it['id']} [{it['author']}] {it['content'][:60]}" for it in pend]
    reply = "📋 Current tasks:\n" + "\n".join(parts)
    return reply if len(reply) <= 1900 else reply[:1890] + "\n…(truncated, see ongoing.md)"


def poll():
    ch = channel_id()
    st, msgs = api("GET", f"/channels/{ch}/messages?limit=50")
    if st != 200:
        print(f"❌ fetch messages failed HTTP {st}")
        return
    state = load(STATE, {"last_id": "0"})
    tasks = load(TASKS, {"items": [], "next_id": 1})
    last = state.get("last_id", "0")
    if not msgs:
        print("channel has no messages")
        return
    newest = max(m["id"] for m in msgs)
    if newest == last:
        print("no new messages")
        return
    bid = bot_id()
    cfg = load(CONFIG, {})
    role_ids = cfg.get("role_ids", [])
    auto = cfg.get("auto_exec", False)
    # key: filter the bot's own messages — <@bot> in reply texts is parsed as a real mention
    # by Discord; not filtering would cause reply → re-capture → reply, forever
    def touched(m):
        if m["author"]["id"] == bid:
            return False
        if any(u["id"] == bid for u in m.get("mentions", [])):
            return True
        c = m.get("content") or ""
        # users often @ a role instead of the bot; unmentionable roles don't appear in
        # mention_roles, so scan the raw text too
        return any(f"<@&{r}>" in c for r in role_ids) or \
            any(r in m.get("mention_roles", []) for r in role_ids)

    fresh = [m for m in msgs if m["id"] > last and touched(m)]
    state["last_id"] = newest
    save(STATE, state)
    if not fresh:
        print("no new @ messages")
        return
    lines = ["✅ Tasks received:"]
    queried = False
    rules = set()
    rule_msgs = {}
    appended = []
    for m in sorted(fresh, key=lambda x: x["id"]):
        content = re.sub(r"<@[!&]?\d+>", "", m.get("content") or "").strip()
        if not content:
            continue
        if _is_task_query(content):
            queried = True
            print(f"list query: {content[:40]}")
            continue
        ev = parse_event_req(content)
        if ev:
            # schedule request: enqueue for the macOS calendar write (before rule queries,
            # so "schedule" isn't misread by the schedule rule)
            title, start = ev
            eid = enqueue_event(title, start, content, m["id"], m["author"]["username"])
            print(f"event #{eid}: «{title}» {start}")
            post(f"📅 Schedule request #{eid} received: «{title}» {start} → will be written "
                 f"to the {CALENDAR_NAME} calendar (iCloud-synced), confirmed after the macOS sync.")
            continue
        hits = _rule_query(content)
        if hits:
            rules |= hits
            for r in hits:
                rule_msgs[r] = content
            print(f"rule query {sorted(hits)}: {content[:40]}")
            continue
        if auto and FORCE_POOL not in content and any(k in content.lower() for k in AUTO_QUESTION):
            # auto-answer mode (knowledge only, no tools): question-like messages → headless
            # claude -p answers inline; on failure the task falls into the pool
            print(f"auto-answer: {content[:40]}")
            post(f"🤖 Got «{content[:50]}», answering automatically (about a minute)…")
            ok, ans = auto_answer(content)
            cid = tasks["next_id"]
            tasks["next_id"] += 1
            appended.append(cid)
            tasks["items"].append({"id": cid, "snowflake": m["id"],
                                   "author": m["author"]["username"],
                                   "content": content, "created": m.get("timestamp"),
                                   "status": "auto" if ok else "pending",
                                   "auto_result": ans[:500] if ok else None})
            save(TASKS, tasks)
            if ok:
                post("🤖 Answer: " + ans[:1800])
            else:
                post(f"❌ Auto-answer failed: {ans[:300]}\npooled as #{cid} — say 'handle the "
                     f"Discord todo list' in a Claude session to take over.")
            continue
        if auto and FORCE_POOL not in content:
            # auto-execute mode (read-only web, explicitly authorized): research-type tasks
            # complete directly with a summary; tasks needing local changes (files/calendar/
            # programs) return need_session via the contract → pool. Reserve the id first so
            # a long research run doesn't capture the message twice.
            cid = tasks["next_id"]
            tasks["next_id"] += 1
            appended.append(cid)
            item = {"id": cid, "snowflake": m["id"], "author": m["author"]["username"],
                    "content": content, "created": m.get("timestamp"), "status": "pending"}
            tasks["items"].append(item)
            save(TASKS, tasks)
            post(f"📝 Processing #{cid} «{content[:50]}» (auto mode: read-only web research; "
                 f"local hands-on work is auto-pooled)…")
            ok, need_session, ans = auto_task(content)
            if ok and not need_session:
                item["status"] = "auto"
                item["auto_result"] = ans[:500]
                save(TASKS, tasks)
                post_task_result(cid, content, ans)
            elif not ok:
                post(f"❌ Auto-execute failed: {ans[:200]}\npooled as #{cid} — say 'handle the "
                     f"Discord todo list' in a Claude session to take over.")
            else:
                post(f"📝 #{cid} needs local hands-on work (auto mode has no such access), pooled — "
                     f"say 'handle the Discord todo list' in a Claude session to take over.")
            continue
        cid = tasks["next_id"]
        tasks["next_id"] += 1
        appended.append(cid)
        tasks["items"].append({"id": cid, "snowflake": m["id"],
                               "author": m["author"]["username"],
                               "content": content, "created": m.get("timestamp"),
                               "status": "pending"})
        print(f"new task #{cid}: {content[:60]}")
        lines.append(f"  #{cid} {content[:80]}")
    save(TASKS, tasks)
    if appended:
        # self-check: tasks were once lost after the receipt was sent (#2); re-read and
        # rewrite if anything is missing
        have = {it["id"] for it in load(TASKS, {}).get("items", [])}
        missing = [c for c in appended if c not in have]
        if missing:
            print(f"⚠ pool save anomaly, rewriting (missing {missing})")
            save(TASKS, tasks)
    if queried:
        # list-type queries → no receipt/no pool entry; reply with the ongoing.md list
        # (main) + bridge pool pending (secondary)
        reply = ongoing_reply(tasks)
        print(reply)
        post(reply)
    for rule in sorted(rules):
        # rule queries (radar / mail pending / schedule) → answer from local data, no pool entry
        if rule == "schedule":
            reply = schedule_reply(rule_msgs.get("schedule", ""))
        else:
            reply = {"radar": radar_reply, "mail": mail_reply}[rule]()
        print(reply)
        post(reply)
    if len(lines) > 1:
        lines.append("handled in a Claude session, results will be posted back")
        post("\n".join(lines))
    event_replies()  # schedule receipts: macOS write results → reply → cleanup


def list_tasks():
    tasks = load(TASKS, {"items": []})
    pend = [it for it in tasks["items"] if it["status"] == "pending"]
    if not pend:
        print("pool is empty")
        return
    for it in pend:
        print(f"#{it['id']} [{it['author']}] {it['content'][:80]} ({it['created']})")


def done(num, result):
    # same lock as --poll: a non-serialized read-modify-write here would overwrite tasks
    # poll just captured (the #2 loss lesson)
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("⚠ poll in progress, try again later")
            return
        tasks = load(TASKS, {"items": []})
        for it in tasks["items"]:
            if it["id"] == num and it["status"] == "pending":
                it["status"] = "done"
                it["done_at"] = time.strftime("%F %T")
                save(TASKS, tasks)
                print(f"✅ #{num} marked done")
                if result:
                    post(f"✅ Task #{num} done: {result[:300]}")
                return
        print(f"❌ no pending task #{num}")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def cleanup(keep=10):
    """Archive finished tasks: keep the last `keep` terminal-state items (done/auto),
    move the rest to archive.json. Same lock as poll — a non-serialized read-modify-write
    would overwrite tasks poll just captured (the #2 lesson)."""
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("⚠ poll in progress, try again later")
            return
        tasks = load(TASKS, {"items": []})
        items = tasks["items"]
        finished = sorted([it for it in items if it.get("status") in ("done", "auto")],
                          key=lambda it: it.get("id", 0))
        # `if keep` rather than `if len(finished) > keep`: with keep=0 the latter passes,
        # then finished[:-0] is finished[:0] -- an empty slice -- so the command would
        # report "nothing to clean" and silently archive nothing. 0 means keep none.
        old = finished[:-keep] if keep else finished[:]
        if not old:
            print(f"✅ nothing to clean (terminal state {len(finished)} ≤ keep {keep})")
            return
        archive = load(ARCHIVE, {"archived": []})
        for it in old:
            it["archived_at"] = time.strftime("%F %T")
        archive["archived"].extend(old)
        archive["moved_at"] = time.strftime("%F %T")
        save(ARCHIVE, archive)
        old_ids = {id(it) for it in old}
        tasks["items"] = [it for it in items if id(it) not in old_ids]
        save(TASKS, tasks)
        print(f"✅ archived {len(old)} to {os.path.basename(ARCHIVE)}, {len(tasks['items'])} left "
              f"(keeping the latest {min(keep, len(finished))} terminal-state items)")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def _poll_locked():
    # re-entrancy guard: the scheduler polls every 60s; a manual --poll overlapping the
    # automatic round would double-post / double-capture
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("previous poll still running, skipping")
            return
        poll()
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def main():
    argv = sys.argv[1:]
    if "--discover" in argv:
        discover()
    elif "--use-channel" in argv:
        cfg = load(CONFIG, {})
        cfg["channel_id"] = argv[argv.index("--use-channel") + 1]
        save(CONFIG, cfg)
        print("✅ channel configured")
    elif "--add-role" in argv:
        cfg = load(CONFIG, {})
        cfg.setdefault("role_ids", []).append(argv[argv.index("--add-role") + 1])
        save(CONFIG, cfg)
        print("✅ trigger role added")
    elif "--poll" in argv:
        _poll_locked()
    elif "--list" in argv:
        list_tasks()
    elif "--done" in argv:
        i = argv.index("--done")
        result = argv[argv.index("--result") + 1] if "--result" in argv else None
        done(int(argv[i + 1]), result)
    elif "--cleanup" in argv:
        keep = 10
        if "--keep" in argv:
            keep = int(argv[argv.index("--keep") + 1])
        cleanup(keep)
    elif "--post" in argv:
        post(argv[argv.index("--post") + 1])
    elif "--answer" in argv:
        ok, ans = auto_answer(argv[argv.index("--answer") + 1])
        print(("✅ OK" if ok else "❌ FAIL") + ": " + ans[:800])
    elif "--task" in argv:
        ok, need_session, ans = auto_task(argv[argv.index("--task") + 1])
        print(f"{'✅ OK' if ok else '❌ FAIL'} need_session={need_session}\n{ans[:800]}")
    elif "--file" in argv:
        path = argv[argv.index("--file") + 1]
        caption = argv[argv.index("--text") + 1] if "--text" in argv else "📎 " + os.path.basename(path)
        send_file(path, caption)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
