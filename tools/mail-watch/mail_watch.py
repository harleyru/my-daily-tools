#!/usr/bin/env python3
"""新邮件监控: 增量同步 Gmail(含转发邮件) → 扫描新到的邮件 → 重要邮件推送通知。

由 crontab 每 30 分钟调用一次。全量下载(后台任务)进行中时自动跳过,不冲突。

用法: python3 mail_watch.py
"""
import json, os, re, subprocess, sys, time
from email.header import decode_header
from email import policy
import email

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
WATCH_STATE = os.path.join(BACKUP, ".watch_state.json")
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")

# 重要发件人/关键词规则(命中任一 → 推送通知; 换成你自己的)
IMPORTANT_SENDERS = (
    # 期刊/投稿系统: 真正的审稿/录用/校对通信都走这些系统或 editor@ 地址
    r"editorialmanager",        # Elsevier Editorial Manager
    r"scholarone|ariessys",     # Clarivate ScholarOne
    r"manuscriptcentral",       # Wiley Manuscript Central
    r"\beditor@",               # editor@... 期刊编辑直发
    r"editorial office",        # 编辑部
    r"submissions?@",           # submissions@...
    r"@aps\.org", r"@aip\.org", r"@ioppublishing\.org",
    # 代码平台
    r"@github\.com",
)

# 私人重要发件人白名单(用户随时报名字/地址加进来)
PERSONAL_SENDERS = (
)

IMPORTANT_SUBJECT = re.compile(
    r"remind|deadline|overdue|urgent|important|invoice|claim|expense|reimburse|"
    r"review|revision|submit|approve|forward|schedule|meeting|邀请|提醒|截止|审稿|投稿|报销|"
    r"decision|manuscript|referee|proofs?|galley", re.I)


def hdr(s):
    parts = decode_header(s or "")
    out = ""
    for t, enc in parts:
        out += t.decode(enc or "utf-8", errors="ignore") if isinstance(t, bytes) else t
    return out


def main():
    # 全量下载还在跑时不干扰(进程名匹配)
    r = subprocess.run(["pgrep", "-f", "gmail_backup.py"], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"[{time.strftime('%F %T')}] 全量下载运行中,本次跳过")
        return

    # 1) 增量同步 Gmail(有断点,只拉新邮件;退出码忽略——限流崩溃下次再来)
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "gmail_backup.py")],
                   capture_output=True)

    # 2) 找出上次检查之后新落盘的邮件
    last = 0.0
    if os.path.exists(WATCH_STATE):
        try:
            last = json.load(open(WATCH_STATE)).get("last_run", 0.0)
        except Exception:
            pass
    now = time.time()
    fresh = []
    eml_root = os.path.join(GMAIL, "eml")
    for dirpath, _, files in os.walk(eml_root):
        for fn in files:
            if not fn.endswith(".eml"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.getmtime(p) > last:
                fresh.append(p)
    json.dump({"last_run": now}, open(WATCH_STATE, "w"))

    if not fresh:
        print(f"[{time.strftime('%F %T')}] 无新邮件")
        return

    # 3) 解析新邮件的发件人/主题,挑重要的
    hits = []
    for p in sorted(fresh, key=os.path.getmtime):
        try:
            msg = email.message_from_bytes(open(p, "rb").read(), policy=policy.default)
            frm = hdr(msg["From"] or "")
            subj = hdr(msg["Subject"] or "")
        except Exception:
            continue
        if (any(re.search(s, frm, re.I) for s in IMPORTANT_SENDERS + PERSONAL_SENDERS)
                or IMPORTANT_SUBJECT.search(subj)):
            hits.append((frm, subj))

    print(f"[{time.strftime('%F %T')}] 新邮件 {len(fresh)} 封,重要 {len(hits)} 封")

    # 4) 推飞书(最多 8 条)
    if hits:
        lines = ["📬 新邮件提醒"]
        for frm, subj in hits[:8]:
            lines.append(f"• {frm[:40]} | {subj[:60]}")
        if len(hits) > 8:
            lines.append(f"… 还有 {len(hits) - 8} 封重要邮件")
        subprocess.run([NOTIFY, "\n".join(lines)])

    # 5) 广告待定审查: 新广告邮件 → 记清单 + 推飞书, 等用户判断删/留
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "mail_ad_review.py"), "--scan"],
                   capture_output=True)

    # 6) 转发邮件归位: 机构邮箱转发过来的新邮件 → 对应文件夹
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "mail_ntu_move.py"), "--scan"],
                   capture_output=True)


if __name__ == "__main__":
    main()
