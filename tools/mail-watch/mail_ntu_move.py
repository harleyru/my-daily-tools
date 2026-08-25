#!/usr/bin/env python3
"""转发邮箱归位(个人场景参考实现):
Gmail 收件箱里「从机构邮箱转发过来」的邮件 → 移到对应文件夹(标签)。

判定规则:
  - From/To/Cc 含机构域(Outlook 转发保留原 To, 这是主信号)
  - 正文含转发地址(转发块签名, 兜底)
  - Resent-From 含机构域(Outlook 转发头)
把你的机构域和转发地址填到 RE_DOMAIN / SELF_ADDR。

移动方式: +X-GM-LABELS "FORWARDED" 再 -X-GM-LABELS ("\\Inbox")——只是打标签+移出收件箱,
邮件仍在 All Mail 可搜可恢复, 本地 eml 照存。

用法:
    python3 mail_ntu_move.py --sweep-dry  # 只统计不移动
    python3 mail_ntu_move.py --sweep      # 一次性清点收件箱, 全部匹配邮件移入文件夹
    python3 mail_ntu_move.py --scan       # 增量: 新落盘 eml 按同规则移入(mail_watch 30 分钟循环调用)
    python3 mail_ntu_move.py --list       # 显示文件夹当前邮件数
"""
import imaplib, json, os, re, subprocess, sys, time
from email import policy
from email.header import decode_header
import email

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
STATE = os.path.join(BASE, "data", "mail_ntu_state.json")
FWD_LABEL = "FORWARDED"  # 改成你自己的文件夹名

# 与 --sweep 的 Gmail 搜索规则保持一致(本地 eml 判定用)。改成你自己的机构域。
RE_DOMAIN = re.compile(r"your-inst\.edu\.sg", re.I)
SELF_ADDR = "your.name@your-inst.edu.sg"


def keychain(service):
    # .env 优先(服务器 ~/daily/.env), 回退 macOS Keychain(Mac 本地)
    try:
        for line in open(os.path.join(BASE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k == service:
                    return v.strip()
    except OSError:
        pass
    out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                         capture_output=True, text=True)
    return out.stdout.strip()


def hdr(s):
    parts = decode_header(s or "")
    out = ""
    for t, enc in parts:
        out += t.decode(enc or "utf-8", errors="ignore") if isinstance(t, bytes) else t
    return out


def connect():
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
    M.socket().settimeout(120)
    return M


def local_match(path):
    """本地 eml 是否属于机构域邮件(From/To/Cc 含机构域, 或正文含转发签名)"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        for name in ("From", "To", "Cc"):
            if RE_DOMAIN.search(hdr(msg[name] or "") or ""):
                return True
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            try:
                if SELF_ADDR in part.get_content():
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def gmail_move_one(M, path):
    """按 Message-ID 在 All Mail 精确搜索 → +文件夹标签 → 移除 \\Inbox。返回 moved|notfound|error"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        mid = (msg["Message-ID"] or "").strip().strip("<>")
        if not mid:
            return "no-mid"
        q = mid.replace('"', "").replace("\\", "")
        st2, data = M.uid("SEARCH", None, f'HEADER Message-ID "{q}"')
        if st2 != "OK" or not data or not data[0]:
            return "notfound"
        uids = data[0].decode().split()
        st3, _ = M.uid("STORE", ",".join(uids), "+X-GM-LABELS", f'"{FWD_LABEL}"')
        st4, _ = M.uid("STORE", ",".join(uids), "-X-GM-LABELS", '("\\\\Inbox")')
        return "moved" if st3 == "OK" and st4 == "OK" else "error"
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
        return "error"


def inbox_ntu_uids(M):
    """收件箱里按 Gmail 原生搜索 + Resent-From 头找 转发邮件 UID 集合"""
    uids = set()
    for q in ('from:your-inst.edu.sg OR to:your-inst.edu.sg', 'cc:your-inst.edu.sg', '"%s"' % SELF_ADDR):
        st, data = M.uid("SEARCH", None, 'X-GM-RAW "in:inbox %s"' % q.replace('"', '\\"'))
        if st == "OK" and data and data[0]:
            uids.update(data[0].decode().split())
    st, data = M.uid("SEARCH", None, 'HEADER Resent-From "your-inst.edu.sg"')
    if st == "OK" and data and data[0]:
        uids.update(data[0].decode().split())
    return sorted(uids, key=int)


def sweep(dry=False):
    M = connect()
    M.select("INBOX")
    uids = inbox_ntu_uids(M)
    print(f"收件箱 转发邮件共 {len(uids)} 封")
    if dry:
        for uid in uids[:15]:
            st, data = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            m = email.message_from_bytes(data[0][1], policy=policy.default)
            print(f"  {uid} | {hdr(m['From'] or '')[:35]} | {hdr(m['Subject'] or '')[:45]}")
        if len(uids) > 15:
            print(f"  … 还有 {len(uids) - 15} 封")
        M.logout()
        return
    ok = 0
    # 注意: INBOX 选中态下 STORE -X-GM-LABELS 移 \\Inbox 会被 Gmail 静默忽略, 必须用 MOVE
    for i in range(0, len(uids), 50):
        batch = ",".join(uids[i:i + 50])
        st1, _ = M.uid("MOVE", batch, '"INBOX/FORWARDED"')
        if st1 == "OK":
            ok += len(uids[i:i + 50])
        else:
            print(f"⚠ 批次 {i}-{i + 50} 失败: {st1}")
    M.logout()
    print(f"✅ 已移入 FORWARDED 文件夹 {ok} 封(All Mail 可恢复)")


def scan():
    """增量: 新落盘 eml → 本地判定 → Gmail 打文件夹标签 + 移出收件箱"""
    last = 0.0
    if os.path.exists(STATE):
        try:
            last = json.load(open(STATE)).get("last_scan", 0.0)
        except Exception:
            pass
    now = time.time()
    fresh = []
    for dirpath, _, files in os.walk(os.path.join(BACKUP, "eml")):
        for fn in files:
            if not fn.endswith(".eml"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.getmtime(p) > last:
                fresh.append(p)
    json.dump({"last_scan": now}, open(STATE, "w"))
    if not fresh:
        print("无新邮件")
        return

    targets = [p for p in fresh if local_match(p)]
    if not targets:
        print(f"新邮件 {len(fresh)} 封, 无 转发邮件")
        return

    M = None
    try:
        M = connect()
        st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
        if st != "OK":
            M = None
    except Exception as e:
        M = None
        print(f"IMAP 连接失败({e}), 本次跳过")

    n = 0
    for p in sorted(targets, key=os.path.getmtime):
        try:
            msg = email.message_from_bytes(open(p, "rb").read(), policy=policy.default)
            subj = hdr(msg["Subject"] or "")
        except Exception:
            continue
        result = gmail_move_one(M, p) if M is not None else "error"
        if result == "moved":
            n += 1
            print(f"📁 已移入文件夹: {subj[:40]}")
        elif result == "notfound":
            print(f"🗂 All Mail 未见(可能已移/已删): {subj[:40]}")
        else:
            print(f"⚠ 移动失败(下次重试): {subj[:40]}")
    if M is not None:
        try:
            M.logout()
        except Exception:
            pass
    print(f"归位完成: 移动 {n}/{len(targets)} 封")


def show():
    M = connect()
    st, data = M.select('"INBOX/FORWARDED"', readonly=True)
    if st != "OK":
        print("无法打开 FORWARDED 文件夹")
        return
    print("FORWARDED 文件夹当前邮件数:", len(data[0].decode().split() or []))
    M.logout()


def main():
    argv = sys.argv[1:]
    if "--sweep-dry" in argv:
        sweep(dry=True)
    elif "--sweep" in argv:
        sweep()
    elif "--scan" in argv:
        scan()
    elif "--list" in argv:
        show()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
