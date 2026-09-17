#!/usr/bin/env python3
"""Forwarded-mail routing (personal-scenario reference implementation):
mail in the Gmail inbox forwarded from an institutional mailbox → move into a folder (label).

Detection rules:
  - From/To/Cc contain the institution domain (Outlook forwarding keeps the original To — main signal)
  - Body contains the forwarding address (forward block signature — fallback)
  - Resent-From contains the institution domain (Outlook forward header)
Put your institution domain and forwarding address into RE_DOMAIN / SELF_ADDR.

Move: +X-GM-LABELS "FORWARDED" then -X-GM-LABELS ("\\Inbox") — label + out of the
Inbox; mail stays in All Mail, searchable and recoverable; local .eml kept.

Usage:
    python3 mail_forward_move.py --sweep-dry  # count only, no move
    python3 mail_forward_move.py --sweep      # one-shot: scan the inbox, move all matches
    python3 mail_forward_move.py --scan       # incremental: new .eml files matched the same way (called from the mail_watch loop)
    python3 mail_forward_move.py --list       # show current count of the folder
"""
import imaplib, json, os, re, subprocess, sys, time
from email import policy
from email.header import decode_header
import email

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
STATE = os.path.join(BASE, "data", "mail_forward_state.json")
FWD_LABEL = "FORWARDED"  # change to your folder name

# Same rules as --sweep's Gmail search (used for local .eml matching). Put your institution domain here.
RE_DOMAIN = re.compile(r"your-inst\.edu\.sg", re.I)
SELF_ADDR = "your.name@your-inst.edu.sg"


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


def connect():
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(keychain("gmail_email"), keychain("gmail_app_pass"))
    M.socket().settimeout(120)
    return M


def local_match(path):
    """Is the local .eml a forwarded-institutional-mail message (domain in From/To/Cc, or forward signature in body)?"""
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
    """Exact Message-ID search in All Mail → +folder label → remove \\Inbox. Returns moved|notfound|error"""
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


def inbox_forwarded_uids(M):
    """UIDs of forwarded mail in the inbox via Gmail native search + Resent-From header"""
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
    uids = inbox_forwarded_uids(M)
    print(f"{len(uids)} forwarded messages in the inbox")
    if dry:
        for uid in uids[:15]:
            st, data = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            m = email.message_from_bytes(data[0][1], policy=policy.default)
            print(f"  {uid} | {hdr(m['From'] or '')[:35]} | {hdr(m['Subject'] or '')[:45]}")
        if len(uids) > 15:
            print(f"  … and {len(uids) - 15} more")
        M.logout()
        return
    ok = 0
    # note: with INBOX selected, STORE -X-GM-LABELS \\Inbox is silently ignored by Gmail — use MOVE
    for i in range(0, len(uids), 50):
        batch = ",".join(uids[i:i + 50])
        st1, _ = M.uid("MOVE", batch, '"INBOX/FORWARDED"')
        if st1 == "OK":
            ok += len(uids[i:i + 50])
        else:
            print(f"⚠ batch {i}-{i + 50} failed: {st1}")
    M.logout()
    print(f"✅ Moved {ok} into the FORWARDED folder (recoverable in All Mail)")


def scan():
    """Incremental: new .eml on disk → local match → Gmail folder label + out of the inbox"""
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
        print("no new mail")
        return

    targets = [p for p in fresh if local_match(p)]
    if not targets:
        print(f"{len(fresh)} new messages, none forwarded")
        return

    M = None
    try:
        M = connect()
        st, _ = M.select('"[Gmail]/All Mail"', readonly=False)
        if st != "OK":
            M = None
    except Exception as e:
        M = None
        print(f"IMAP connect failed ({e}), skipping this pass")

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
            print(f"📁 moved into folder: {subj[:40]}")
        elif result == "notfound":
            print(f"🗂 not in All Mail (maybe already moved/deleted): {subj[:40]}")
        else:
            print(f"⚠ move failed (will retry): {subj[:40]}")
    if M is not None:
        try:
            M.logout()
        except Exception:
            pass
    print(f"routing done: moved {n}/{len(targets)}")


def show():
    M = connect()
    st, data = M.select('"INBOX/FORWARDED"', readonly=True)
    if st != "OK":
        print("cannot open the FORWARDED folder")
        return
    print("messages in folder:", len(data[0].decode().split() or []))
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
