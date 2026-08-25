#!/usr/bin/env python3
"""每日早清单: 当日日程快照 + 长期任务 → 飞书推送。2026-08-21 起在服务器 systemd 9:30 跑。
日程快照由 Mac 端 scripts/sync_daily.sh 每 30 分钟导出并同步过来 (data/calendar_today.json)。"""
import re, subprocess, os, json
from datetime import datetime, date

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.environ.get("DAILY_BASE") or os.path.dirname(TOOLS)
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")
ONGOING = os.path.join(PROJ, "ongoing.md")
SNAP = os.path.join(PROJ, "data", "calendar_today.json")
GMAIL_STATE = os.path.join(
    os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail"),
    ".state.json",
)

# 任务展示样式: 任务名片段 → (当前阶段, 总阶段), 状态点 ●●●○○
STAGES = {
    "RF analyzer 论文": (3, 4),
    "wide-field": (1, 3),
    "hBN": (1, 2),
    "NV 小型化": (2, 4),
    "DSO": (2, 3),
    "真空腔+冷台": (1, 3),
    "BP FPGA": (0, 3),
    "gauge_rpl": (4, 8),
    "电流芯片": (3, 3),
    "magnetometer": (0, 2),
    "Zitong": (0, 1),
}
GMAIL_TOTAL = 25497  # Gmail 实际应下总数(全量减去有意跳过的 Scholar 警报)

PRI = {"P0": "🔴", "P1": "🟡", "P2": "⚪"}


def today_events():
    """读 Mac 同步过来的当日日程快照, 返回 [(时间HH:MM, 日历名, 标题)] 按时间排序;
    无快照或快照日期非今天(如 Mac 未开机同步)返回 None"""
    try:
        data = json.load(open(SNAP, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("date") != date.today().strftime("%Y-%m-%d"):
        return None
    evs = [tuple(e) for e in data.get("events", []) if len(e) == 3]
    return sorted(evs, key=lambda x: x[0])


def ongoing_rows():
    """解析 ongoing.md 的 进行中/待办 表格行,返回 [(任务,优先级,截止,进展,状态)]"""
    rows = []
    section = None
    for line in open(ONGOING, encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
        if not line.startswith("|") or section not in ("🚧 进行中", "🗓 待办"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("任务", "------"):
            continue
        rows.append((cells[0], cells[1], cells[2], cells[3], "🚧" if section == "🚧 进行中" else "🗓"))
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
    """读当日日记的「今日任务」未勾选项, 返回列表(去掉复选框与加粗标记)"""
    diary = os.path.join(PROJ, "日记", date.today().strftime("%Y-%m-%d") + ".md")
    items = []
    if not os.path.exists(diary):
        return items
    section = False
    for line in open(diary, encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line.startswith("## 今日任务")
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
    lines = [f"☀️ 早清单 {now.strftime('%m-%d')}（{'一二三四五六日'[now.weekday()]}）"]

    evs = today_events()
    lines.append("\n📅 今日日程")
    if evs:
        for hhmm, cal, title in evs:
            tag = "" if cal == "Agent" else f"（{cal}）"
            lines.append(f"▸ {hhmm} {title}{tag}")
    elif evs is None:
        lines.append("▸ 无日程快照（Mac 未同步，当天日程可能缺失）")
    else:
        lines.append("▸ 无日程安排")

    lines.append("\n📋 今日任务")
    tts = today_journal_tasks()
    if tts:
        lines.extend(f"▸ {t}" for t in tts)
    else:
        lines.append("▸ 无(未在日记里细化今日任务)")

    lines.append("\n🔥 长期任务")
    soon = []
    for task, pri, due, prog, icon in ongoing_rows():
        badge = PRI.get(pri, "⚪")
        dl = days_left(due)
        dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", due)
        dstr = dm.group(0)[5:] if dm else None
        head = f"{badge} {task}"
        if dl is not None:
            head += f" | ⏳{dstr} 剩{dl}天" if dl <= 7 else f" | {dstr}"
        lines.append(head)

        # Gmail 特殊: 真实进度条
        if "Gmail" in task:
            cnt = gmail_count()
            lines.append(f"  └ {bar(cnt, GMAIL_TOTAL)} {cnt}/{GMAIL_TOTAL}")
            continue
        # 其余: 状态点
        st = next(((a, b) for k, (a, b) in STAGES.items() if k in task), None)
        if st:
            dots = "●" * st[0] + "○" * (st[1] - st[0])
            lines.append(f"  └ {dots} {prog}")
        else:
            lines.append(f"  └ {prog}")
        if dl is not None and 0 <= dl <= 7:
            soon.append(f"{task}（{due}）")

    if soon:
        lines.append("\n⏰ 7 天内到期")
        lines.extend(f"▸ {s}" for s in soon)

    subprocess.run([NOTIFY, "\n".join(lines)])


if __name__ == "__main__":
    main()
