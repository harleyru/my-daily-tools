#!/usr/bin/env python3
"""New-mail review (reference implementation):
「Promotions」category → auto-archive: remove the \\Inbox label in Gmail (mail stays in
All Mail, searchable and recoverable), local .eml kept, no manual review.
Sender allow-list (AUTO_ARCHIVE_SENDERS, domain match) → archived the same way;
failures are logged to the audit and left in the inbox.
Sender delete-list (AUTO_DELETE_SENDERS, domain match) → deleted (Gmail trash,
recoverable for 30 days; local .eml kept); failures logged, left in the inbox.
「Shopping」category (orders/invoices may be useful) → pending queue + push → keep/delete decision.

Called from mail_watch.py every 30 min with --scan. Say "delete #3 #5" / "delete all" /
"keep #2" in a Claude session to run --delete / --keep.
Delete: exact Message-ID search in All Mail → \\Deleted + EXPUNGE → Gmail trash (30 days recoverable).
Local .eml always kept (only the Gmail side is deleted).
Audit: data/mail_ad_archived.json (capped at 500 entries).

Usage:
    python3 mail_ad_review.py --scan            # scan new mail: promotions auto-archive, shopping/failures → queue + push
    python3 mail_ad_review.py --seed            # initialize state only (historical mail not treated as new)
    python3 mail_ad_review.py --list            # print the pending queue
    python3 mail_ad_review.py --delete 3,5      # delete the given ids (→ Gmail trash)
    python3 mail_ad_review.py --delete-all      # delete all pending
    python3 mail_ad_review.py --keep 2,4        # keep the given ids (never prompt again)
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

# eml folder names whose new mail is auto-archived (promotional — no review needed).
# These come from your category tree (see mail_categories.py) — match to your own.
AUTO_ARCHIVE_DIRS = ("Promotions",)
# eml folder names whose new mail goes to the pending queue for a keep/delete decision
PENDING_DIRS = ("Shopping",)

# Sender allow-list (domain match): hit → auto-archive, no review. Example: ("your-sender.com",)
AUTO_ARCHIVE_SENDERS = ()
# Sender delete-list (domain match): hit → Gmail trash (30 days recoverable), local .eml kept
AUTO_DELETE_SENDERS = ()


def keychain(service):
    # .env first (server deployments), fall back to macOS Keychain
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
    """Exact Message-ID search in All Mail → remove the \\Inbox label (archive; mail stays in All Mail).
    Returns 'archived' | 'notfound' (not in All Mail, treat as handled) | 'error'"""
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
    """Allow-list check: From domain hits AUTO_ARCHIVE_SENDERS → archive directly"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        _, addr = parseaddr(hdr(msg["From"] or ""))
    except Exception:
        return False
    return any(s in (addr or "").lower() for s in AUTO_ARCHIVE_SENDERS)


def sender_delete_match(path):
    """Delete-list check: From domain hits AUTO_DELETE_SENDERS → delete directly"""
    try:
        msg = email.message_from_bytes(open(path, "rb").read(), policy=policy.default)
        _, addr = parseaddr(hdr(msg["From"] or ""))
    except Exception:
        return False
    return any(s in (addr or "").lower() for s in AUTO_DELETE_SENDERS)


def gmail_delete_one(M, path):
    """Exact Message-ID search in All Mail → \\Deleted + EXPUNGE → Gmail trash (30 days recoverable).
    Returns 'deleted' | 'notfound' (not in All Mail, treat as handled) | 'error'"""
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
        print("no new mail")
        return

    in_dir = lambda dirs: [p for p in fresh if any(os.path.sep + d + os.path.sep in p for d in dirs)]
    promo = in_dir(AUTO_ARCHIVE_DIRS)
    shop = in_dir(PENDING_DIRS)
    auto_send = [p for p in fresh if p not in promo + shop and sender_auto_match(p)]
    auto_del = [p for p in fresh if p not in promo + shop + auto_send and sender_delete_match(p)]

    p = load_pending()
    next_id = max([it["id"] for it in p["items"]], default=0) + 1
    new_items = []
    n_archived = 0
    n_send = 0
    n_del = 0

    # Promotions + allow-list senders → auto-archive (remove inbox label, All Mail searchable,
    # local .eml kept); delete-list senders → trash (30 days recoverable), local .eml kept;
    # archive failures / IMAP failures → pending review, never silently dropped;
    # allow/delete-list failures are logged to the audit and left in the inbox
    if promo or auto_send or auto_del:
        M = None
        try:
            M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
            M.socket().settimeout(120)
            st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
            if st != "OK":
                M = None
                print("cannot open All Mail, promotions → pending review")
        except Exception as e:
            M = None
            print(f"IMAP connect failed ({e}), promotions → pending review")
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
                print(f"🗂 archived: {subj[:40]}")
            elif result == "notfound":
                print(f"🗂 not in All Mail (maybe already archived/deleted): {subj[:40]}")
            else:
                print(f"⚠ archive failed → pending: {subj[:40]}")
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
                print(f"🗂 allow-list archived: {subj[:40]}")
            else:
                print(f"⚠ allow-list archive failed (left in inbox, audited): {subj[:40]}")
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
                print(f"🗑️ delete-list removed (trash): {subj[:40]}")
            elif result == "notfound":
                print(f"🗑️ not in All Mail (maybe already deleted): {subj[:40]}")
            else:
                print(f"⚠ delete-list removal failed (left in inbox, audited): {subj[:40]}")
        alog["items"] = alog["items"][-500:]  # cap the audit log
        save_archived(alog)
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass

    # Shopping (orders/invoices may be useful) → pending queue
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
        print(f"archived {n_archived} promotions, {n_send} allow-list, deleted {n_del} direct — no new pending mail")
        return
    n = len(new_items)
    print(f"new mail pending {n}; archived {n_archived} promotions, {n_send} allow-list, deleted {n_del} direct")
    lines = ["🗑️ New mail pending (shopping/archive failures)"]
    for it in new_items[:8]:
        lines.append(f"#{it['id']} {it['sender'][:32]} | {it['subject'][:40]}")
    if n > 8:
        lines.append(f"… {n - 8} more")
    if n_archived:
        lines.append(f"🗂 auto-archived {n_archived} promotional messages (recoverable in All Mail, local .eml kept)")
    if n_send:
        lines.append(f"🗂 allow-list archived {n_send} messages (domain allow-list, no review)")
    if n_del:
        lines.append(f"🗑️ deleted {n_del} messages (trash, 30 days recoverable)")
    lines.append("say the numbers or 'delete all' in a Claude session; no reply = keep")
    subprocess.run([NOTIFY, "\n".join(lines)])


def gmail_delete(items):
    """Exact Message-ID search in All Mail and move to trash. Returns (deleted, skipped)."""
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
    M.socket().settimeout(120)
    st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
    if st != "OK":
        raise RuntimeError("cannot open All Mail")
    deleted, skipped = [], []
    for it in items:
        try:
            msg = email.message_from_bytes(open(it["path"], "rb").read(), policy=policy.default)
            mid = (msg["Message-ID"] or "").strip().strip("<>")
        except Exception:
            skipped.append(it)
            continue
        if not mid:
            print(f"#{it['id']} no Message-ID, skipped (manual handling needed)")
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
            print(f"#{it['id']} not found in All Mail (maybe already deleted), skipped")
            skipped.append(it)
            continue
        try:
            st3, _ = M.uid("STORE", ",".join(uids), "+FLAGS (\\Deleted)")
            st4, _ = M.uid("EXPUNGE", ",".join(uids))
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
            print(f"#{it['id']} delete error, skipped")
            skipped.append(it)
            continue
        if st3 == "OK":
            print(f"#{it['id']} ✅ moved to trash")
            deleted.append(it)
        else:
            print(f"#{it['id']} STORE failed, skipped")
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
        print("initialized: historical mail not treated as new")
        return

    if "--scan" in argv:
        scan()
        return

    if "--list" in argv:
        p = load_pending()
        pend = [it for it in p["items"] if it["status"] == "pending"]
        if not pend:
            print("pending queue is empty")
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
                print(f"#{it['id']} kept")
        save_pending(p)
        return
    else:
        print(__doc__)
        return

    targets = [it for it in p["items"] if it["id"] in ids and it["status"] == "pending"]
    if not targets:
        print("no matching pending ids (already handled or don't exist)")
        return
    deleted, skipped = gmail_delete(targets)
    for it in deleted:
        it["status"] = "deleted"
        it["deleted"] = time.strftime("%F %T")
    for it in skipped:
        it["status"] = "skipped"
    save_pending(p)
    print(f"done: deleted {len(deleted)} → Gmail trash (30 days recoverable), local .eml kept; skipped {len(skipped)}")


if __name__ == "__main__":
    main()
