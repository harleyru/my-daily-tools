#!/usr/bin/env python3
"""Discord 收件桥(方案1: 待办池模式): 轮询频道里 @机器人 的消息 → 写入任务池 → 回执。

纯 stdlib(urllib, 无 pip 依赖), 由 launchd 每 60s 跑一次 --poll。
任务池 data/discord/tasks.json; 用户在 Claude 会话里说"处理 Discord 待办"时,我读池执行,完成后 --done 回帖。

用法:
    discord_tasks.py --discover            # 列出服务器/频道, 帮选定频道
    discord_tasks.py --use-channel <id>    # 把频道写入配置
    discord_tasks.py --add-role <id>       # 追加触发角色(用户常 @角色名而不是 @机器人)
    discord_tasks.py --poll                # 轮询新 @消息(含 @触发角色), 入池 + 回执; "任务清单"类查询即时回帖
                                           # 规则查询: 「雷达」→最新卡片摘要, 「邮件待审/广告待审/待审」→广告待删清单
    discord_tasks.py --list                # 打印待办池
    discord_tasks.py --done 3 [--result "完成内容"]   # 标记完成, 可回帖结果
    discord_tasks.py --cleanup [--keep N]  # 归档历史已完成任务, 保留最近 N 条(默认 10)
    discord_tasks.py --post "文本"         # 直接发消息到频道
    discord_tasks.py --file <path> [--text "说明"]   # 上传本地文件到频道
    discord_tasks.py --answer "文本"      # 本地测试自动问答档(不发 Discord, 打印结果)
    discord_tasks.py --task "文本"        # 本地测试自动调研档(只读联网, 不发 Discord, 打印结果)

token 存 Keychain (service: discord_bot_token); 频道 id / 触发角色在 data/discord/config.json
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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_DIR = os.path.join(BASE, "data", "discord")
CONFIG = os.path.join(CFG_DIR, "config.json")
TASKS = os.path.join(CFG_DIR, "tasks.json")
STATE = os.path.join(CFG_DIR, "state.json")
ARCHIVE = os.path.join(CFG_DIR, "archive.json")
EVENT_QUEUE = os.path.join(CFG_DIR, "pending_events.json")
EVENT_VERBS = ("加日程", "添加日程", "新增日程", "安排日程", "记日程")
LOCK = os.path.join(CFG_DIR, "poll.lock")
API = "https://discord.com/api/v10"
# 消息正文含这些词的 → 不回执不入池, 直接在频道回帖当前任务清单
QUERY_WORDS = ("任务清单", "待办清单", "待办列表", "任务列表", "任务有哪些", "有哪些任务", "待办")
# 「任务/task + 查询式动词」也判为清单查询, 覆盖「查询task」「发一下目前的任务」「今天任务还剩下几个」这类说法
QUERY_VERBS = ("查询", "查一下", "发一下", "看一下", "看看", "看下", "列出", "显示",
               "list", "目前", "现在", "有哪些", "剩下")


def _is_task_query(content):
    """清单查询判定; 含「入池」强制走任务池(与自动问答档同样的逃生舱)"""
    if "入池" in content:
        return False
    if any(w in content for w in QUERY_WORDS):
        return True
    c = content.lower()
    return ("任务" in content or "task" in c) and any(v in c for v in QUERY_VERBS)
# 规则查询: 命中触发词 且 剥掉触发词后剩余为空/含疑问词 → 直接回帖本地数据, 不入池
RULE_WORDS = {
    "radar": ("雷达", "radar"),
    "mail": ("邮件待审", "广告待审", "待审"),
    "schedule": ("日程", "安排", "calendar"),
}
QUERY_MARKERS = ("推了", "推送", "报告", "什么", "啥", "今天", "明天", "明日", "最新", "现在",
                 "吗", "呢", "有哪些", "有啥", "怎么样", "如何", "report")
# 自动问答触发特征: 疑问式才自动答, 其余(动手任务)照旧入池
AUTO_QUESTION = ("?", "？", "吗", "呢", "是什么", "为什么", "怎么", "怎样", "如何",
                 "解释", "总结", "分析", "翻译", "介绍一下", "对比",
                 "什么", "多少", "哪些", "请问")


def token():
    # .env 优先(服务器), 回退 Keychain(Mac 本地)
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
        sys.exit("❌ 没有 discord_bot_token(需要 ~/daily/.env 或 Keychain)")
    return t


def api(method, path, data=None, timeout=30):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "Bot " + token())
    req.add_header("User-Agent", "ru-discord-bridge")
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
        sys.exit(f"❌ bot token 无效(HTTP {st})")
    return me["id"]


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 原子写: tmp + fsync + os.replace, 防止并发/中途失败留下半截文件
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def channel_id():
    cfg = load(CONFIG, {})
    if not cfg.get("channel_id"):
        sys.exit("❌ 未配置频道: 先跑 --discover 再 --use-channel <id>")
    return cfg["channel_id"]


def post(text):
    if len(text) > 1900:  # Discord 单条上限 2000 字符, 留余量
        text = text[:1890] + "\n…(截断)"
    st, _ = api("POST", f"/channels/{channel_id()}/messages", {"content": text})
    print("✅ 已回帖" if st == 200 else f"❌ 回帖失败 HTTP {st}")


def discover():
    st, me = api("GET", "/users/@me")
    print(f"bot: {me.get('username')} (id={me['id']})")
    st, guilds = api("GET", "/users/@me/guilds")
    if st != 200 or not guilds:
        print(f"bot 不在任何服务器里({st}), 先用邀请链接把 bot 加进你的服务器")
        return
    for g in guilds:
        st2, chs = api("GET", f"/guilds/{g['id']}/channels")
        print(f"服务器: {g['name']} ({g['id']})")
        for c in chs:
            if c.get("type") == 0:  # 0 = 文字频道
                print(f"  #{c['name']}  id={c['id']}")


def parse_event_req(content):
    """解析加日程消息 → (title, start_str) or None。start_str: 'YYYY-MM-DD HH:MM' (服务器本地时区)。
    支持: 今天/明天/后天/周X、MM-DD/MM/DD/YYYY-MM-DD、HH:MM/HH点MM/下午3点"""
    if not any(v in content for v in EVENT_VERBS):
        return None
    rest = content
    for v in sorted(EVENT_VERBS, key=len, reverse=True):  # 长动词先替换, 防「添加日程」被「加日程」先拆
        rest = rest.replace(v, " ")
    today = datetime.date.today()
    d = None
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", rest)
    if m:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.search(r"(\d{1,2})[-/月](\d{1,2})", rest)
        if m:
            d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
        elif "后天" in rest:
            d = today + datetime.timedelta(days=2)
        elif "明天" in rest or "明日" in rest:
            d = today + datetime.timedelta(days=1)
        elif "今天" in rest or "今日" in rest:
            d = today
        else:
            wk = re.search(r"周([一二三四五六日天])", rest)
            if wk:
                names = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
                delta = (names[wk.group(1)] - today.weekday()) % 7
                d = today + datetime.timedelta(days=delta if delta else 7)  # 当天→下周
            else:
                d = today
    hh, mm = 9, 0
    m = re.search(r"(\d{1,2})点半", rest)  # 「2点半」→ 14:30
    if m:
        hh, mm = int(m.group(1)), 30
    else:
        m = re.search(r"(\d{1,2})[:：点时](\d{1,2})?分?", rest)  # 支持「14:30」「3点」「3点30」
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2)) if m.group(2) else 0
        else:
            m = re.search(r"(\d{2})(\d{2})", rest)
            if m and 0 <= int(m.group(1)) <= 23:
                hh, mm = int(m.group(1)), int(m.group(2))
    if ("下午" in rest or "晚上" in rest or "今晚" in rest) and hh < 12:
        hh += 12
    title = re.sub(r"今天|今日|明天|明日|后天|今晚|周[一二三四五六日天]|"
                   r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}[-/月]\d{1,2}|"
                   r"\d{1,2}点半|\d{1,2}[:：点时]\d{1,2}分?|\d{1,2}点|\d{4}|\d{2}\d{2}|上午|下午|晚上", " ", rest)
    title = " ".join(title.split())
    if not title:
        title = "日程"
    return title, f"{d.isoformat()} {hh:02d}:{mm:02d}"


def enqueue_event(title, start, content, snowflake, author):
    """加日程请求入队(待 Mac 写入日历)。poll.lock 串行 + 原子写, 与 tasks.json 同模式"""
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
    """poll 末尾: 检查已由 Mac 写入/失败的日程请求 → 回帖结果 → 从队列移除"""
    q = load(EVENT_QUEUE, {"items": []})
    done = [it for it in q["items"] if it["status"] in ("done", "failed")]
    if not done:
        return
    for it in done:
        if it["status"] == "done":
            post(f"📅 日程已写入 Agent 日历（iCloud 同步）: 《{it['title']}》 {it['start']}")
        else:
            post(f"❌ 日程写入失败: 《{it['title']}》 {it['start']} —— {it['result']}")
    q["items"] = [it for it in q["items"] if it["status"] not in ("done", "failed")]
    save(EVENT_QUEUE, q)
    print(f"✅ 日程回执 {len(done)} 条已发, 队列剩余 {len(q['items'])}")


def _rule_query(content):
    """规则查询判定: 命中触发词 且 剥掉触发词后剩余为空或含疑问词 → 视为查询(不入池)"""
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
        return "📡 雷达还没产出过内容(每天 8:37 自动跑)。"
    data = load(os.path.join(pub, days[-1] + ".json"), {})
    cards = data.get("cards", [])
    if not cards:
        return f"📡 雷达 {days[-1]}: 无卡片。"
    lines = [f"📡 雷达 {days[-1]} · {len(cards)} 张卡片:"]
    for c in cards:
        lines.append(f"• [{c.get('score', '?')}] {c.get('title', '')[:90]} ({c.get('source', '?')})")
        url = c.get("sourceUrl", "")
        if url:
            lines.append(f"  {url}")
    reply = "\n".join(lines)
    return reply if len(reply) <= 1800 else reply[:1790] + "\n…(截断, 其余见 reports/radar.html)"


def mail_reply():
    pool = load(os.path.join(BASE, "data", "mail_ad_pending.json"), {"items": []})
    pend = [it for it in pool.get("items", []) if it.get("status") == "pending"]
    if not pend:
        return "📬 暂无待审广告邮件。"
    lines = [f"📬 待审广告邮件 {len(pend)} 封(说「删 #N」/「留 #N」处理):"]
    for it in pend:
        lines.append(f"  #{it['id']} {it.get('sender', '?')} — {it.get('subject', '')[:60]}")
    return "\n".join(lines)


def schedule_reply(content=""):
    """读 Mac 同步的日程快照(data/calendar_today.json, 每 30 分钟推送), 与 daily_push 同源;
    content 含「明天/明日」→ 答明日日程(快照 tomorrow 字段), 否则当日。
    快照缺失/过期提示 Mac 未同步(服务器无 osascript, 不读 Mac 日历)"""
    snap_path = os.path.join(BASE, "data", "calendar_today.json")
    try:
        snap = json.load(open(snap_path, encoding="utf-8"))
    except (OSError, ValueError):
        return "📅 日程快照缺失（Mac 未同步，等下次 sync 后重试）。"
    if snap.get("date") != time.strftime("%Y-%m-%d"):
        return "📅 日程快照过期（最后同步 %s），Mac 未同步。" % snap.get("date", "?")
    tomorrow_q = "明天" in content or "明日" in content
    evs = [tuple(e) for e in snap.get("tomorrow" if tomorrow_q else "events", []) if len(e) == 3]
    evs.sort(key=lambda x: x[0])
    if not evs:
        return "📅 明日无日程。" if tomorrow_q else "📅 今日无日程。"
    lines = ["📅 明日日程:" if tomorrow_q else "📅 今日日程:"]
    for hhmm, cal, title in evs:
        tag = "" if cal == "Agent" else f"（{cal}）"
        lines.append(f"• {hhmm} {title}{tag}")
    return "\n".join(lines)


def auto_answer(content):
    """自动问答档(纯知识, 无工具): 无头 claude -p 直接回答, 返回 (ok, text)。
    不带 --allowedTools——无审批 shell 面被分类器否决, 动手任务走会话/入池。"""
    claude = shutil.which("claude-ds") or os.path.expanduser("~/.local/bin/claude-ds")
    if not os.path.exists(claude):
        claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not claude or not os.path.exists(claude):
        return False, "❌ 找不到 claude CLI"
    cfg = load(CONFIG, {})
    prompt = (
        "你是用户的 Discord 自动问答助手(纯知识档, 无工具)。用户刚在 Discord 里 @ 你, 原文:\n\n"
        f"---\n{content}\n---\n\n"
        "用原文语言(通常中文)直接回答。你没有工具, 不能访问本机文件或执行命令; "
        "如果请求需要动手操作(改文件/跑程序/查本机数据), 直接说「这个需要打开 Claude 会话处理」。"
        "回答控制在 1500 字以内。"
    )
    cmd = [claude, "-p", "--output-format", "text", "--tools", ""]
    model = cfg.get("auto_model", "")
    if model:
        cmd += ["--model", model]
    try:
        # 提示词走 stdin(同雷达管线; --tools 是贪婪变参, 位置参数会被吞);
        # 剥离代理类环境变量(本机会话可能跑在 ANTHROPIC_BASE_URL 代理上, 直接继承会 401);
        # 干净环境 = launchd 环境, 走 claude.ai 登录, 与雷达管线一致
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.setdefault("HOME", os.path.expanduser("~"))
        r = subprocess.run(cmd, input=prompt, cwd=BASE, capture_output=True,
                           text=True, timeout=240, env=env)
    except subprocess.TimeoutExpired:
        return False, "⏱ 自动回答超时(4 分钟)"
    if r.returncode != 0 or not (r.stdout or "").strip():
        return False, f"❌ claude -p 失败(rc={r.returncode}): {(r.stderr or r.stdout or '')[:200]}"
    return True, r.stdout.strip()


def auto_task(content):
    """自动执行档(只读工具, 2026-08-16 用户显式授权): WebSearch/WebFetch 联网调研,
    返回 (ok, need_session, answer)。不涉本机修改的任务直接完成; 需改本机(文件/日历/程序)
    的由模型按合同返回 need_session=true → 入池走会话。无 Bash/Write/Edit——安全由构造保证。"""
    claude = shutil.which("claude-ds") or os.path.expanduser("~/.local/bin/claude-ds")
    if not os.path.exists(claude):
        claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not claude or not os.path.exists(claude):
        return False, False, "❌ 找不到 claude CLI"
    prompt = (
        "你是用户的 Discord 自动任务助手(只读档)。用户刚在 Discord 里 @ 你, 原文:\n\n"
        f"---\n{content}\n---\n\n"
        "你只有联网检索工具(WebSearch/WebFetch), 没有本机工具(Bash/Write/Edit 均不可用)。\n"
        "任务类型判定:\n"
        "- 调研/查资料/了解某主题 → 联网检索, 用原文语言(通常中文)写结构化总结。\n"
        "- 需要访问或修改本机(文件/日历/程序/数据库/执行代码) → 不要检索, 立即返回 need_session=true。\n"
        "最终输出必须是 JSON, 不要任何多余文字:\n"
        '{"need_session": false, "answer": "总结内容(要点/数字/来源, 1000 字内, 结尾附来源链接)"}'
    )
    cmd = [claude, "-p", "--output-format", "json",
           "--allowedTools", "WebSearch,WebFetch", "--max-turns", "12"]
    cfg = load(CONFIG, {})
    if cfg.get("auto_model", ""):
        cmd += ["--model", cfg["auto_model"]]
    try:
        # 提示词走 stdin(贪婪变参); 剥代理环境变量(同雷达管线, 防 401)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.setdefault("HOME", os.path.expanduser("~"))
        r = subprocess.run(cmd, input=prompt, cwd=BASE, capture_output=True,
                           text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return False, False, "⏱ 自动执行超时(10 分钟)"
    if r.returncode != 0 or not (r.stdout or "").strip():
        return False, False, f"❌ claude -p 失败(rc={r.returncode}): {(r.stderr or r.stdout or '')[:200]}"
    try:  # 外层是 claude -p 的结果包, result 字段里是模型的最终输出(合同 JSON)
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
    """调研完成: 回帖摘要 + 总结文档存 reports/discord/(HTML) 并上传频道"""
    title = re.sub(r"\s+", " ", content)[:40]
    rdir = os.path.join(BASE, "reports", "discord")
    os.makedirs(rdir, exist_ok=True)
    safe = re.sub(r"[^\w.\-#]", "_", title)
    path = os.path.join(rdir, f"{time.strftime('%F')}-#{cid}-{safe}.html")
    page = (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>调研 #%d</title>\n<style>"
        "body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
        "max-width:760px;margin:24px auto;padding:0 16px;line-height:1.7;color:#24292f}"
        "h1{font-size:20px;border-bottom:2px solid #0969da;padding-bottom:6px}"
        ".req{background:#f0f7ff;border-left:4px solid #0969da;padding:8px 12px;margin:12px 0}"
        "pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px;font-size:13px}"
        "</style></head><body><h1>调研 #%d</h1>"
        "<div class='req'>请求: %s</div><pre>%s</pre>"
        "</body></html>\n"
    ) % (cid, cid, html.escape(content), html.escape(ans))
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    head = ans if len(ans) <= 1500 else ans[:290] + f"\n…(完整 {len(ans)} 字见附件)"
    post(f"📄 调研完成 #{cid}: {head}")
    send_file(path, f"📄 调研总结 #{cid}: {content[:60]}")


def send_file(path, caption):
    """上传本地文件到 Discord 频道(multipart/form-data, 纯 stdlib)"""
    if not os.path.isfile(path):
        sys.exit(f"❌ 文件不存在: {path}")
    fname = re.sub(r"[^\w.-]", "_", os.path.basename(path)) or "file"
    bnd = "----ru" + uuid.uuid4().hex
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
    req.add_header("User-Agent", "ru-discord-bridge")
    req.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print("✅ 文件已发送" if r.status == 200 else f"❌ 发送失败 {r.status}")
    except urllib.error.HTTPError as e:
        print(f"❌ 发送失败 HTTP {e.code}: {e.read().decode()[:200]}")


def ongoing_reply(tasks):
    """任务清单查询回帖: ongoing.md 进行中/待办(主) + 收件桥池内待办(次)"""
    rows = {"进行中": [], "待办": []}
    section = None
    try:
        with open(os.path.join(BASE, "ongoing.md"), encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("## 🚧 进行中"):
                    section = "进行中"
                    continue
                if ln.startswith("## 🗓 待办"):
                    section = "待办"
                    continue
                if ln.startswith("## "):
                    section = None
                    continue
                if not section or not ln.startswith("|") or "---" in ln:
                    continue
                cells = [re.sub(r"\*+", "", c).strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) < 2 or cells[0] in ("任务",):
                    continue
                rows[section].append((cells[0], cells[1] if len(cells) > 1 else "",
                                      cells[2] if len(cells) > 2 else "",
                                      cells[3] if len(cells) > 3 else ""))
    except OSError:
        pass
    parts = []
    for section in ("进行中", "待办"):
        if not rows[section]:
            continue
        parts.append(("🚧 " if section == "进行中" else "🗓 ") + section + ":")
        for name, pri, dl, prog in rows[section]:
            meta = " ".join(x for x in (pri, dl) if x and x != "—")
            line = f"• {name}"
            if meta:
                line += f" [{meta}]"
            if prog:
                line += f" — {prog[:40]}"
            parts.append(line)
    if not parts:
        parts.append("(ongoing.md 为空)")
    pend = [it for it in tasks["items"] if it["status"] == "pending"]
    if pend:
        parts.append("📥 收件桥待办:")
        parts += [f"  #{it['id']} [{it['author']}] {it['content'][:60]}" for it in pend]
    reply = "📋 当前任务:\n" + "\n".join(parts)
    return reply if len(reply) <= 1900 else reply[:1890] + "\n…(截断, 详情见 ongoing.md)"


def poll():
    ch = channel_id()
    st, msgs = api("GET", f"/channels/{ch}/messages?limit=50")
    if st != 200:
        print(f"❌ 拉取消息失败 HTTP {st}")
        return
    state = load(STATE, {"last_id": "0"})
    tasks = load(TASKS, {"items": [], "next_id": 1})
    last = state.get("last_id", "0")
    if not msgs:
        print("频道无消息")
        return
    newest = max(m["id"] for m in msgs)
    if newest == last:
        print("无新消息")
        return
    bid = bot_id()
    cfg = load(CONFIG, {})
    role_ids = cfg.get("role_ids", [])
    auto = cfg.get("auto_exec", False)
    # 关键: 过滤机器人自己的消息——回执文本里的 <@bot> 会被 Discord 解析成真 mention,
    # 不过滤会导致"回执→再收录→再回执"无限循环
    def touched(m):
        if m["author"]["id"] == bid:
            return False
        if any(u["id"] == bid for u in m.get("mentions", [])):
            return True
        c = m.get("content") or ""
        # 用户常 @角色而不是 @机器人; 不可 mention 的角色不会进 mention_roles, 需扫原文
        return any(f"<@&{r}>" in c for r in role_ids) or \
            any(r in m.get("mention_roles", []) for r in role_ids)

    fresh = [m for m in msgs if m["id"] > last and touched(m)]
    state["last_id"] = newest
    save(STATE, state)
    if not fresh:
        print("无新 @消息")
        return
    lines = ["✅ 收到任务:"]
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
            print(f"清单查询: {content[:40]}")
            continue
        ev = parse_event_req(content)
        if ev:
            # 加日程: 入队等待 Mac 写入 Agent 日历 (先于规则查询, 避免「安排日程」被 schedule 规则误判)
            title, start = ev
            eid = enqueue_event(title, start, content, m["id"], m["author"]["username"])
            print(f"加日程 #{eid}: 《{title}》 {start}")
            post(f"📅 已收到加日程请求 #{eid}: 《{title}》 {start} → 将写入 Agent 日历(iCloud 同步), Mac 同步后回执。")
            continue
        hits = _rule_query(content)
        if hits:
            rules |= hits
            for r in hits:
                rule_msgs[r] = content
            print(f"规则查询 {sorted(hits)}: {content[:40]}")
            continue
        if auto and "入池" not in content and any(k in content for k in AUTO_QUESTION):
            # 自动问答档(纯知识, 无工具): 疑问式消息 → 无头 claude -p 回答回帖; 失败才落池
            print(f"自动问答: {content[:40]}")
            post(f"🤖 收到「{content[:50]}」, 自动回答中(约 1 分钟)…")
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
                post("🤖 回答: " + ans[:1800])
            else:
                post(f"❌ 自动回答失败: {ans[:300]}\n已入池 #{cid}——打开 Claude 会话时说「处理 Discord 待办」接手。")
            continue
        if auto and "入池" not in content:
            # 自动执行档(只读联网, 用户 2026-08-16 授权): 调研类任务直接完成回帖总结文档;
            # 需改本机(文件/日历/程序)的按合同 need_session 转池。先占位入池防长调研期间重复收录。
            cid = tasks["next_id"]
            tasks["next_id"] += 1
            appended.append(cid)
            item = {"id": cid, "snowflake": m["id"], "author": m["author"]["username"],
                    "content": content, "created": m.get("timestamp"), "status": "pending"}
            tasks["items"].append(item)
            save(TASKS, tasks)
            post(f"📝 开始处理 #{cid}「{content[:50]}」(自动档: 只读联网调研, 需本机动手会自动转池)…")
            ok, need_session, ans = auto_task(content)
            if ok and not need_session:
                item["status"] = "auto"
                item["auto_result"] = ans[:500]
                save(TASKS, tasks)
                post_task_result(cid, content, ans)
            elif not ok:
                post(f"❌ 自动执行失败: {ans[:200]}\n已入池 #{cid}——打开 Claude 会话时说「处理 Discord 待办」接手。")
            else:
                post(f"📝 #{cid} 需要本机动手(自动档无此权限), 已入池——打开 Claude 会话时说「处理 Discord 待办」接手。")
            continue
        cid = tasks["next_id"]
        tasks["next_id"] += 1
        appended.append(cid)
        tasks["items"].append({"id": cid, "snowflake": m["id"],
                               "author": m["author"]["username"],
                               "content": content, "created": m.get("timestamp"),
                               "status": "pending"})
        print(f"新任务 #{cid}: {content[:60]}")
        lines.append(f"  #{cid} {content[:80]}")
    save(TASKS, tasks)
    if appended:
        # 自检: 曾出现"回执已发但池里任务丢失"(#2), 重读确认, 丢了就重写一次
        have = {it["id"] for it in load(TASKS, {}).get("items", [])}
        missing = [c for c in appended if c not in have]
        if missing:
            print(f"⚠ 池保存异常, 重写 (缺 {missing})")
            save(TASKS, tasks)
    if queried:
        # "任务清单"类查询 → 不回执不入池, 回帖 ongoing.md 任务清单(主) + 收件桥待办(次)
        reply = ongoing_reply(tasks)
        print(reply)
        post(reply)
    for rule in sorted(rules):
        # 规则查询(雷达/邮件待审/日程) → 直接回帖本地数据, 不入池
        if rule == "schedule":
            reply = schedule_reply(rule_msgs.get("schedule", ""))
        else:
            reply = {"radar": radar_reply, "mail": mail_reply}[rule]()
        print(reply)
        post(reply)
    if len(lines) > 1:
        lines.append("打开 Claude 会话时处理, 完成后会回帖")
        post("\n".join(lines))
    event_replies()  # 加日程回执: Mac 写入结果 → 回帖 → 清理


def list_tasks():
    tasks = load(TASKS, {"items": []})
    pend = [it for it in tasks["items"] if it["status"] == "pending"]
    if not pend:
        print("待办池为空")
        return
    for it in pend:
        print(f"#{it['id']} [{it['author']}] {it['content'][:80]} ({it['created']})")


def done(num, result):
    # 与 --poll 同一把锁: done 的读-改-写若不串行, 会覆盖 poll 刚收录的任务(#2 丢失教训)
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("⚠ 轮询进行中, 稍后再试")
            return
        tasks = load(TASKS, {"items": []})
        for it in tasks["items"]:
            if it["id"] == num and it["status"] == "pending":
                it["status"] = "done"
                it["done_at"] = time.strftime("%F %T")
                save(TASKS, tasks)
                print(f"✅ #{num} 已标记完成")
                if result:
                    post(f"✅ 任务 #{num} 完成: {result[:300]}")
                return
        print(f"❌ 找不到待办 #{num}")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def cleanup(keep=10):
    """归档历史已完成任务: 保留最近 keep 条结束态(done/auto), 其余移到 archive.json。
    与 poll 同一把锁——读-改-写不串行会覆盖 poll 刚收录的任务(#2 教训)。"""
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("⚠ 轮询进行中, 稍后再试")
            return
        tasks = load(TASKS, {"items": []})
        items = tasks["items"]
        finished = sorted([it for it in items if it.get("status") in ("done", "auto")],
                          key=lambda it: it.get("id", 0))
        old = finished[:-keep] if len(finished) > keep else []
        if not old:
            print(f"✅ 无需清理（结束态 {len(finished)} 条 ≤ 保留 {keep} 条）")
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
        print(f"✅ 归档 {len(old)} 条到 {os.path.basename(ARCHIVE)}，池内剩 {len(tasks['items'])} 条"
              f"（结束态保留最近 {min(keep, len(finished))} 条）")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()


def _poll_locked():
    # 防并发重入: launchd 每 60s 一轮, 手动跑 --poll 若与自动轮重叠会双发回帖/重复收录
    lf = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("上次轮询未结束, 跳过")
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
        print("✅ 频道已配置")
    elif "--add-role" in argv:
        cfg = load(CONFIG, {})
        cfg.setdefault("role_ids", []).append(argv[argv.index("--add-role") + 1])
        save(CONFIG, cfg)
        print("✅ 已添加触发角色")
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
