#!/usr/bin/env python3
"""新邮件审查(2026-08-16 起, 用户拍板):
「广告促销」→ 自动归档: Gmail 移除 \\Inbox 标签(邮件保留 All Mail 可搜可恢复), 本地 eml 照存, 不再人工审查。
「发件人白名单」(AUTO_ARCHIVE_SENDERS 域名匹配, 2026-08-18 起: Academia Mentions) → 同样直接归档; 失败记审计留收件箱。
「直接删除白名单」(AUTO_DELETE_SENDERS 域名匹配, 2026-08-20 起: Spotify) → 直接删除(移入 Gmail 回收站, 30天可恢复; 本地 eml 保留); 失败记审计留收件箱。
「购物」(订单/发票类可能有用) → 记入待定清单 → 推飞书 → 用户判断删/留。

由 mail_watch.py 每 30 分钟调 --scan。用户在会话里说"删 #3 #5"/"广告全删"/"留 #2",我跑 --delete/--keep。
删除方式: 按 Message-ID 在 All Mail 精确搜索 → \\Deleted + EXPUNGE → Gmail 回收站(30天可恢复)。
本地 eml 保留归档(与既往清理策略一致: 只删 Gmail 端, 本地备份不删)。
归档审计: data/mail_ad_archived.json(最多留 500 条)。

用法:
    python3 mail_ad_review.py --scan            # 扫描新邮件: 促销自动归档, 购物/归档失败记入清单并推飞书
    python3 mail_ad_review.py --seed            # 只初始化状态(历史邮件不当作新邮件)
    python3 mail_ad_review.py --list            # 打印待定清单
    python3 mail_ad_review.py --delete 3,5      # 删除指定编号(→ Gmail 回收站)
    python3 mail_ad_review.py --delete-all      # 删除全部待定
    python3 mail_ad_review.py --keep 2,4        # 保留指定编号(不再提示)
"""
import imaplib, json, os, re, subprocess, sys, time
from email import policy
from email.header import decode_header
from email.utils import parseaddr
import email

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
STATE = os.path.join(BASE, "data", "mail_ad_review_state.json")
PENDING = os.path.join(BASE, "data", "mail_ad_pending.json")
ARCHIVED = os.path.join(BASE, "data", "mail_ad_archived.json")
NOTIFY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notify", "notify.sh")

# 参与待定审查的分类目录(示例: 广告促销/购物; 按你的 Gmail 分类结构改)
AD_DIRS = ("广告促销", "购物")

# 发件人白名单(按域名匹配): 命中 → 直接归档, 无需审查。示例: ("your-sender.com",)
AUTO_ARCHIVE_SENDERS = ()
AUTO_DELETE_SENDERS = ()

# 直接删除白名单: 用户指定「直接删除」的发件人(按域名匹配, 2026-08-20 起: Spotify) → 移入 Gmail 回收站(30天可恢复), 本地 eml 保留


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


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"last_scan": 0.0}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f)


def load_pending():
    try:
        with open(PENDING) as f:
            p = json.load(f)
        return p if isinstance(p.get("items"), list) else {"items": []}
    except Exception:
        return {"items": []}


def save_pending(p):
    with open(PENDING, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def load_archived():
    try:
        with open(ARCHIVED) as f:
            p = json.load(f)
        return p if isinstance(p.get("items"), list) else {"items": []}
    except Exception:
        return {"items": []}


def save_archived(p):
    with open(ARCHIVED, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def gmail_archive_one(M, path):
    """按 Message-ID 在 All Mail 精确搜索 → 移除 \\Inbox 标签(归档, 邮件仍在 All Mail)。
    返回 'archived' | 'notfound'(All Mail 未见, 视为已处理) | 'error'"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        mid = (msg["Message-ID"] or "").strip().strip("<>")
        if not mid:
            return "notfound"
        q = mid.replace('"', "").replace("\\", "")
        st2, data = M.uid("SEARCH", None, f'HEADER Message-ID "{q}"')
        if st2 != "OK" or not data or not data[0]:
            return "notfound"
        uids = data[0].decode().split()
        st3, _ = M.uid("STORE", ",".join(uids), '-X-GM-LABELS', '("\\\\Inbox")')
        return "archived" if st3 == "OK" else "error"
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
        return "error"


def sender_auto_match(path):
    """发件人白名单判定: From 域名命中 AUTO_ARCHIVE_SENDERS → 直接归档"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        _, addr = parseaddr(hdr(msg["From"] or ""))
    except Exception:
        return False
    return any(s in (addr or "").lower() for s in AUTO_ARCHIVE_SENDERS)


def sender_delete_match(path):
    """直接删除白名单判定: From 域名命中 AUTO_DELETE_SENDERS → 直接删除"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        _, addr = parseaddr(hdr(msg["From"] or ""))
    except Exception:
        return False
    return any(s in (addr or "").lower() for s in AUTO_DELETE_SENDERS)


def gmail_delete_one(M, path):
    """按 Message-ID 在 All Mail 精确搜索 → \\Deleted + EXPUNGE → Gmail 回收站(30天可恢复)。
    返回 'deleted' | 'notfound'(All Mail 未见, 视为已处理) | 'error'"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        mid = (msg["Message-ID"] or "").strip().strip("<>")
        if not mid:
            return "notfound"
        q = mid.replace('"', "").replace("\\", "")
        st2, data = M.uid("SEARCH", None, f'HEADER Message-ID "{q}"')
        if st2 != "OK" or not data or not data[0]:
            return "notfound"
        uids = data[0].decode().split()
        st3, _ = M.uid("STORE", ",".join(uids), "+FLAGS (\\Deleted)")
        st4, _ = M.uid("EXPUNGE", ",".join(uids))
        return "deleted" if st3 == "OK" else "error"
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
        return "error"


def scan():
    last = load_state().get("last_scan", 0.0)
    now = time.time()
    fresh = []
    for dirpath, _, files in os.walk(os.path.join(BACKUP, "eml")):
        for fn in files:
            if not fn.endswith(".eml"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.getmtime(p) > last:
                fresh.append(p)
    save_state({"last_scan": now})
    if not fresh:
        print("无新邮件")
        return

    promo = [p for p in fresh if os.path.sep + "广告促销" + os.path.sep in p]
    shop = [p for p in fresh if os.path.sep + "购物" + os.path.sep in p]
    auto_send = [p for p in fresh if p not in promo + shop and sender_auto_match(p)]
    auto_del = [p for p in fresh if p not in promo + shop + auto_send and sender_delete_match(p)]

    p = load_pending()
    next_id = max([it["id"] for it in p["items"]], default=0) + 1
    new_items = []
    n_archived = 0
    n_send = 0
    n_del = 0

    # 广告促销 + 发件人白名单 → 自动归档(移除收件箱标签, All Mail 可搜可恢复, 本地 eml 照存);
    # 直接删除白名单 → 移入回收站(30天可恢复), 本地 eml 保留;
    # 促销归档失败/IMAP 连接失败 → 转入待定审查, 不静默丢; 白名单(归档/删除)失败则记审计留收件箱
    if promo or auto_send or auto_del:
        M = None
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
            M.socket().settimeout(120)
            st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
            if st != "OK":
                M = None
                print("无法打开 All Mail, 促销邮件转待定审查")
        except Exception as e:
            M = None
            print(f"IMAP 连接失败({e}), 促销邮件转待定审查")
        alog = load_archived()
        for path in sorted(promo, key=os.path.getmtime):
            try:
                msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
                frm = hdr(msg["From"] or "")
                _, addr = parseaddr(frm)
                subj = hdr(msg["Subject"] or "")
            except Exception:
                continue
            result = gmail_archive_one(M, path) if M is not None else "error"
            alog["items"].append({"sender": addr or frm[:60], "subject": subj,
                                  "path": path, "result": result,
                                  "at": time.strftime("%F %T")})
            if result == "archived":
                n_archived += 1
                print(f"🗂 已归档: {subj[:40]}")
            elif result == "notfound":
                print(f"🗂 All Mail 未见(可能已归档/删除): {subj[:40]}")
            else:
                print(f"⚠ 归档失败转待定: {subj[:40]}")
                it = {"id": next_id, "sender": addr or frm[:60], "subject": subj,
                      "path": path, "status": "pending", "added": time.strftime("%F %T")}
                p["items"].append(it)
                new_items.append(it)
                next_id += 1
        for path in sorted(auto_send, key=os.path.getmtime):
            try:
                msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
                frm = hdr(msg["From"] or "")
                _, addr = parseaddr(frm)
                subj = hdr(msg["Subject"] or "")
            except Exception:
                continue
            result = gmail_archive_one(M, path) if M is not None else "error"
            alog["items"].append({"sender": addr or frm[:60], "subject": subj,
                                  "path": path, "result": result, "kind": "sender-auto",
                                  "at": time.strftime("%F %T")})
            if result == "archived":
                n_send += 1
                print(f"🗂 发件人白名单归档: {subj[:40]}")
            else:
                print(f"⚠ 发件人白名单归档失败(留收件箱, 已记审计): {subj[:40]}")
        for path in sorted(auto_del, key=os.path.getmtime):
            try:
                msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
                frm = hdr(msg["From"] or "")
                _, addr = parseaddr(frm)
                subj = hdr(msg["Subject"] or "")
            except Exception:
                continue
            result = gmail_delete_one(M, path) if M is not None else "error"
            alog["items"].append({"sender": addr or frm[:60], "subject": subj,
                                  "path": path, "result": result, "kind": "sender-delete",
                                  "at": time.strftime("%F %T")})
            if result == "deleted":
                n_del += 1
                print(f"🗑️ 发件人白名单删除(回收站): {subj[:40]}")
            elif result == "notfound":
                print(f"🗑️ All Mail 未见(可能已删除): {subj[:40]}")
            else:
                print(f"⚠ 发件人白名单删除失败(留收件箱, 已记审计): {subj[:40]}")
        alog["items"] = alog["items"][-500:]  # 审计日志封顶
        save_archived(alog)
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass

    # 购物(订单/发票类可能有用) → 仍走待定审查
    for path in sorted(shop, key=os.path.getmtime):
        try:
            msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
            frm = hdr(msg["From"] or "")
            _, addr = parseaddr(frm)
            subj = hdr(msg["Subject"] or "")
        except Exception:
            continue
        it = {"id": next_id, "sender": addr or frm[:60], "subject": subj,
              "path": path, "status": "pending", "added": time.strftime("%F %T")}
        p["items"].append(it)
        new_items.append(it)
        next_id += 1
    save_pending(p)

    if not new_items:
        print(f"促销归档 {n_archived} 封, 发件人白名单归档 {n_send} 封, 直接删除 {n_del} 封, 无待定新邮件")
        return
    n = len(new_items)
    print(f"新购物邮件 {n} 封, 已记入待定清单; 促销归档 {n_archived} 封, 发件人白名单归档 {n_send} 封, 直接删除 {n_del} 封")
    lines = ["🗑️ 新邮件待定(购物/归档失败)"]
    for it in new_items[:8]:
        lines.append(f"#{it['id']} {it['sender'][:32]} | {it['subject'][:40]}")
    if n > 8:
        lines.append(f"… 还有 {n - 8} 封")
    if n_archived:
        lines.append(f"🗂 另自动归档促销广告 {n_archived} 封(All Mail 可恢复, 本地 eml 保留)")
    if n_send:
        lines.append(f"🗂 发件人白名单归档 {n_send} 封(Academia Mentions 等, 可直接归档)")
    if n_del:
        lines.append(f"🗑️ 直接删除 {n_del} 封(Spotify 等, 回收站 30 天可恢复)")
    lines.append("要删在 Claude 会话里说编号或'广告全删', 不回应则保留")
    subprocess.run([NOTIFY, "\n".join(lines)])


def gmail_delete(items):
    """按 Message-ID 在 All Mail 精确搜索并移入回收站。返回 (deleted, skipped)。"""
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
    M.socket().settimeout(120)
    st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
    if st != "OK":
        raise RuntimeError("无法打开 All Mail")
    deleted, skipped = [], []
    for it in items:
        try:
            msg = email.message_from_bytes(open(it["path"], "rb").read(), policy=policy.default)
            mid = (msg["Message-ID"] or "").strip().strip("<>")
        except Exception:
            skipped.append(it)
            continue
        if not mid:
            print(f"#{it['id']} 无 Message-ID, 跳过(需人工处理)")
            skipped.append(it)
            continue
        q = mid.replace('"', "").replace("\\", "")
        uids = []
        try:
            st2, data = M.uid("SEARCH", None, f'HEADER Message-ID "{q}"')
            if st2 == "OK" and data and data[0]:
                uids = data[0].decode().split()
        except imaplib.IMAP4.error:
            pass
        if not uids:
            print(f"#{it['id']} 未在 All Mail 找到(可能已删), 记跳过")
            skipped.append(it)
            continue
        try:
            st3, _ = M.uid("STORE", ",".join(uids), "+FLAGS (\\Deleted)")
            st4, _ = M.uid("EXPUNGE", ",".join(uids))
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
            print(f"#{it['id']} 删除出错, 记跳过")
            skipped.append(it)
            continue
        if st3 == "OK":
            print(f"#{it['id']} ✅ 已移入回收站")
            deleted.append(it)
        else:
            print(f"#{it['id']} STORE 失败, 记跳过")
            skipped.append(it)
    try:
        M.logout()
    except Exception:
        pass
    return deleted, skipped


def main():
    argv = sys.argv[1:]
    if "--seed" in argv:
        save_state({"last_scan": time.time()})
        if not os.path.exists(PENDING):
            save_pending({"items": []})
        print("已初始化: 历史广告邮件不当作新邮件")
        return

    if "--scan" in argv:
        scan()
        return

    if "--list" in argv:
        p = load_pending()
        pend = [it for it in p["items"] if it["status"] == "pending"]
        if not pend:
            print("待定清单为空")
            return
        for it in pend:
            print(f"#{it['id']} [{it['status']}] {it['sender'][:40]} | {it['subject'][:50]} ({it['added']})")
        return

    p = load_pending()

    if "--delete-all" in argv:
        ids = {it["id"] for it in p["items"] if it["status"] == "pending"}
    elif "--delete" in argv:
        i = argv.index("--delete")
        ids = {int(x) for x in argv[i + 1].split(",") if x.strip()}
    elif "--keep" in argv:
        i = argv.index("--keep")
        ids = {int(x) for x in argv[i + 1].split(",") if x.strip()}
        for it in p["items"]:
            if it["id"] in ids and it["status"] == "pending":
                it["status"] = "kept"
                print(f"#{it['id']} 已保留")
        save_pending(p)
        return
    else:
        print(__doc__)
        return

    targets = [it for it in p["items"] if it["id"] in ids and it["status"] == "pending"]
    if not targets:
        print("没有待定编号(可能已处理或编号不存在)")
        return
    deleted, skipped = gmail_delete(targets)
    for it in deleted:
        it["status"] = "deleted"
        it["deleted"] = time.strftime("%F %T")
    for it in skipped:
        it["status"] = "skipped"
    save_pending(p)
    print(f"完成: 删除 {len(deleted)} 封 → Gmail 回收站(30天可恢复), 本地 eml 保留归档; 跳过 {len(skipped)} 封")


if __name__ == "__main__":
    main()
