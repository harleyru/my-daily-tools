#!/usr/bin/env python3
"""New-mail monitor: incremental Gmail sync (incl. forwarded mail) → scan new arrivals →
push notifications for important mail.

Call every 30 min from cron/systemd/launchd. Skips automatically while a full
download (gmail_backup.py) is running — no conflict.

Usage: python3 mail_watch.py
"""
import json, os, re, subprocess, sys, time
from email.header import decode_header
from email import policy
import email

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIL_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
WATCH_STATE = os.path.join(BACKUP, ".watch_state.json")
NOTIFY = os.path.join(TOOLS, "notify", "notify.sh")

# Important-sender/keyword rules (any hit → notify; replace with your own)
IMPORTANT_SENDERS = (
    # Journal/submission systems: real review/acceptance/proof mail goes through these
    r"editorialmanager",        # Elsevier Editorial Manager
    r"scholarone|ariessys",     # Clarivate ScholarOne
    r"manuscriptcentral",       # Wiley Manuscript Central
    r"\beditor@",               # editor@... direct journal editors
    r"editorial office",
    r"submissions?@",
    r"@aps\.org", r"@aip\.org", r"@ioppublishing\.org",
    # Code platforms
    r"@github\.com",
)

# Personal important senders (add names/addresses as you like)
PERSONAL_SENDERS = (
)

IMPORTANT_SUBJECT = re.compile(
    r"remind|deadline|overdue|urgent|important|invoice|claim|expense|reimburse|"
    r"review|revision|submit|approve|forward|schedule|meeting|"
    r"decision|manuscript|referee|proofs?|galley", re.I)


def hdr(s):
    parts = decode_header(s or "")
    out = ""
    for t, enc in parts:
        out += t.decode(enc or "utf-8", errors="ignore") if isinstance(t, bytes) else t
    return out


def main():
    # Don't interfere with a running full download (matched by process name)
    r = subprocess.run(["pgrep", "-f", "gmail_backup.py"], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"[{time.strftime('%F %T')}] full download running, skipping this pass")
        return

    # 1) Incremental Gmail sync (resumable, pulls only new mail; exit code ignored —
    #    rate-limit crashes are retried next pass)
    subprocess.run([sys.executable, os.path.join(MAIL_DIR, "gmail_backup.py")],
                   capture_output=True)

    # 2) Find .eml files written since the last pass
    last = 0.0
    if os.path.exists(WATCH_STATE):
        try:
            last = json.load(open(WATCH_STATE)).get("last_run", 0.0)
        except Exception:
            pass
    now = time.time()
    fresh = []
    eml_root = os.path.join(BACKUP, "eml")
    for dirpath, _, files in os.walk(eml_root):
        for fn in files:
            if not fn.endswith(".eml"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.getmtime(p) > last:
                fresh.append(p)
    json.dump({"last_run": now}, open(WATCH_STATE, "w"))

    if not fresh:
        print(f"[{time.strftime('%F %T')}] no new mail")
        return

    # 3) Parse senders/subjects of new mail, pick the important ones
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

    print(f"[{time.strftime('%F %T')}] {len(fresh)} new, {len(hits)} important")

    # 4) Push notification (max 8 items)
    if hits:
        lines = ["📬 New mail alert"]
        for frm, subj in hits[:8]:
            lines.append(f"• {frm[:40]} | {subj[:60]}")
        if len(hits) > 8:
            lines.append(f"… {len(hits) - 8} more important messages")
        subprocess.run([NOTIFY, "\n".join(lines)])

    # 5) Ad pending review: new ad mail → queue + notify, awaiting keep/delete decision
    subprocess.run([sys.executable, os.path.join(TOOLS, "mail-ad-review", "mail_ad_review.py"), "--scan"],
                   capture_output=True)

    # 6) Forwarded-mail routing: mail forwarded from an institutional mailbox → folder
    subprocess.run([sys.executable, os.path.join(MAIL_DIR, "mail_ntu_move.py"), "--scan"],
                   capture_output=True)


if __name__ == "__main__":
    main()
