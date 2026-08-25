#!/usr/bin/env python3
"""周五周报: 汇总本周每日文件的完成情况 + 长期任务进展 → 飞书推送。crontab 周五 17:00 调用。"""
import re, subprocess, os
from datetime import datetime, date, timedelta

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.environ.get("DAILY_BASE") or os.path.dirname(TOOLS)
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")


def daily_done(fname):
    """提取每日文件的「完成情况」小节"""
    out, section = [], None
    try:
        f = open(os.path.join(PROJ, "日记", fname), encoding="utf-8")
    except OSError:
        return []
    for line in f:
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
            continue
        if section == "完成情况" and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
    return out


def _mask_parens(text):
    """把括号组(全/半角, 嵌套由内而外)整体替换为占位符, 避免括号内容参与 → 或 / 切分"""
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
    """去掉所有括号(全/半角)组(补空格), 支持嵌套; 压缩空格, 中文间不留空格"""
    while True:
        nt = re.sub(r"[（(][^（）()]*[）)]", " ", text)
        if nt == text:
            break
        text = nt
    t = re.sub(r" +", " ", text)
    t = re.sub(r"([一-鿿])\s+([一-鿿])", r"\1\2", t)          # 中文间不留空格
    return re.sub(r"\s+([，。；;、)）])", r"\1", t).strip()    # 标点前不留空格


def shrink(text, maxlen=40):
    """精简: 按 '→' 取第一段(标题) → 去括号组 → 仍超长按 ':' 取标题部分。细节留在日记。"""
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
    """长期任务当前状态: 括号组整体保护; 无 → 再按 / 切;
    从后往前找含状态标记(当前/🚧/待/下一步/进行中)的段, 状态词在第一段则整条同状态返回整条"""
    masked, ph = _mask_parens(progress)
    segs = [s.strip() for s in masked.split("→")]
    if len(segs) == 1:
        segs = [s.strip() for s in segs[0].split("/") if s.strip()]
    segs = [_unmask(s, ph) for s in segs if s.strip()]
    for i, s in enumerate(reversed(segs)):
        if any(k in s for k in ("当前", "🚧", "待", "下一步", "进行中")):
            if i == len(segs) - 1:          # 状态词在第一段 → 整条同一状态
                return shrink(progress, 45)
            return shrink(s, 45)
    return shrink(segs[-1], 45)


def main():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    lines = [f"📊 周报（{monday.strftime('%m-%d')} ~ {today.strftime('%m-%d')}）"]

    # 本周完成
    done, days = [], 0
    for i in range((today - monday).days + 1):
        fname = (monday + timedelta(days=i)).strftime("%Y-%m-%d") + ".md"
        items = daily_done(fname)
        if items:
            days += 1
            done.extend(items)
    lines.append("\n✅ 本周完成")
    if done:
        for d in done:
            d = d.strip()
            if d in ("无", "暂无", "—", "-"):
                continue
            lines.append(f"• {shrink(d)}")
    else:
        lines.append("• 本周每日文件暂无完成记录")

    # 长期任务进展
    lines.append("\n🚧 长期任务进展")
    section = None
    for line in open(os.path.join(PROJ, "ongoing.md"), encoding="utf-8"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:]
        if section == "🚧 进行中" and line.startswith("|") and "任务" not in line and "---" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4:
                lines.append(f"• [{cells[1]}] {cells[0]} | {status(cells[3])}")

    # 下周预告: 读 Mac 同步的快照 week_count (data/calendar_today.json)
    import json as _json
    try:
        _snap = _json.load(open(os.path.join(PROJ, "data", "calendar_today.json"), encoding="utf-8"))
        _n = _snap.get("week_count")
        if _n is None or _snap.get("date") != date.today().strftime("%Y-%m-%d"):
            _n = "?(Mac 未同步)"
    except (OSError, ValueError):
        _n = "?(Mac 未同步)"
    lines.append(f"\n🗓 下周预告：未来 7 天日历有 {_n} 个日程，长期任务详见早清单")

    subprocess.run([NOTIFY, "\n".join(lines)])


if __name__ == "__main__":
    main()
