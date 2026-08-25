#!/usr/bin/env python3
"""IMAP backup of Gmail: download all mail as .eml, archived by year/month, deduped by Message-ID.

Gmail quirk: the same message appears in multiple label folders (e.g. Inbox and
[Gmail]/All Mail). Strategy: download concrete labels first (Inbox/Sent/custom
labels), [Gmail]/All Mail last as a fallback, dedupe globally by Message-ID so
nothing is duplicated and label information is kept as much as possible.

Usage:
    gmail_backup.py                    # incremental backup
    gmail_backup.py --dry-run          # count only, no download
Credentials: Keychain (gmail_email / gmail_app_pass)
Archive: <EMAIL_BACKUP> (default ~/Documents/email_backup/gmail)
"""
import argparse
import imaplib
import json
import os
import re
import subprocess
import sys
from email.utils import parsedate_to_datetime, parseaddr
from email.header import decode_header

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mail_categories import categorize  # noqa: E402

BACKUP_DIR = os.environ.get("EMAIL_BACKUP") or os.path.expanduser("~/Documents/email_backup/gmail")
STATE_FILE = os.path.join(BACKUP_DIR, ".state.json")
IMAP_HOST = "imap.gmail.com"


def keychain(service):
    # .env first (server deployments), fall back to macOS Keychain
    try:
        for line in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), encoding="utf-8"):
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


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass  # corrupt state file (e.g. interrupted write); treat as no progress
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n]+', "_", s or "no_subject")
    return s[:80].strip() or "no_subject"


def msg_id(raw):
    m = re.search(rb"^Message-ID:\s*(.+)$", raw, re.M | re.I)
    # decode to str, otherwise bytes can't be written to the JSON state file
    return m.group(1).strip().lower().decode(errors="ignore") if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=keychain("gmail_email"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    password = keychain("gmail_app_pass")
    if not args.email or not password:
        print("❌ Missing credentials. Run first:")
        print("  security add-generic-password -U -a $USER -s gmail_email -w <account>")
        print("  security add-generic-password -U -a $USER -s gmail_app_pass -w <16-char app password>")
        sys.exit(1)

    M = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        M.login(args.email, password)
    except imaplib.IMAP4.error as e:
        print(f"❌ Login failed: {e}")
        print("   Check: 1) app password is 16 chars  2) IMAP is enabled in Gmail settings")
        sys.exit(3)
    # Time out after 300s instead of hanging forever (outer loop retries);
    # large batch FETCHes need a generous timeout
    M.socket().settimeout(300)

    state = load_state()
    seen_ids = set(state.get("_msgids", []))
    total_new = 0
    total_failed = 0

    # Fallback: state file missing (interrupted run) — rebuild the Message-ID set
    # from already-downloaded files to avoid re-downloading
    if not state.get("_msgids"):
        eml_root = os.path.join(BACKUP_DIR, "eml")
        for dirpath, _, files in os.walk(eml_root):
            for fn in files:
                if not fn.endswith(".eml"):
                    continue
                try:
                    with open(os.path.join(dirpath, fn), "rb") as f:
                        head = f.read(8192)
                    mid = msg_id(head)
                    if mid:
                        seen_ids.add(mid)
                except OSError:
                    pass
        if seen_ids:
            print(f"Rebuilt {len(seen_ids)} Message-IDs from downloaded files")

    # Concrete labels first, All Mail last; skip spam/trash
    def parse_folder(line):
        line = line.decode(errors="ignore") if isinstance(line, bytes) else line
        m = re.search(r'"([^"]*)"\s*$', line)   # folder name in trailing quotes
        return m.group(1) if m else line.strip()

    SKIP = ("[Gmail]/Spam", "[Gmail]/Bin", "[Gmail]/Trash", "Google scholar")
    folders = [parse_folder(f) for f in M.list()[1]]
    folders = [f for f in folders if f not in SKIP]
    folders.sort(key=lambda f: "[Gmail]/All Mail" in f)

    for folder in folders:
        # note: imaplib doesn't auto-quote folder names — names with spaces need quotes
        status, cnt = M.select(f'"{folder}"', readonly=True)
        if status != "OK":
            continue
        n = int(cnt[0])
        seen_uids = set(state.get(folder, []))
        new_ids = []
        if n > 0:
            status, data = M.uid("SEARCH", None, "ALL")
            raw_ids = data[0] if data and data[0] else b""
            if isinstance(raw_ids, bytes):
                raw_ids = raw_ids.decode(errors="ignore")
            new_ids = [u for u in raw_ids.split() if u not in seen_uids]

        # Fast header scan: batch-fetch Message-IDs only; skip messages already
        # downloaded in another folder (otherwise All Mail re-pulls 30k messages
        # and discards them — hours wasted)
        if new_ids:
            scan = new_ids
            new_ids = []
            for i in range(0, len(scan), 500):
                batch = ",".join(scan[i:i + 500])
                st, d = M.uid("FETCH", batch, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM)] UID)")
                if st != "OK" or not d:
                    new_ids.extend(scan[i:i + 500])   # scan failed → download directly, better slow than lossy
                    continue
                for part in d:
                    if not isinstance(part, tuple):
                        continue
                    mu = re.search(rb"UID (\d+)", part[0])
                    if not mu:
                        continue
                    body = part[1]
                    if isinstance(body, str):
                        body = body.encode()
                    if re.search(rb"^From:.*scholaralerts", body or b"", re.M | re.I):
                        seen_uids.add(mu.group(1).decode())   # deliberately skip Scholar alerts, mark handled
                        continue
                    mid = msg_id(body or b"")
                    if mid and mid in seen_ids:
                        seen_uids.add(mu.group(1).decode())   # already downloaded, mark handled
                    else:
                        new_ids.append(mu.group(1).decode())
            print(f"📁 {folder}: {n} total, header scan {len(scan)} → {len(new_ids)} to fetch")
        else:
            print(f"📁 {folder}: {n} total, {len(new_ids)} new")
        if args.dry_run:
            continue

        # Batch download: 25 per FETCH; failed batches degrade to per-message retry
        def store(uid_s, raw):
            """Write one message; returns whether it was handled (incl. deliberate skips)."""
            nonlocal total_new
            if not raw:
                return False
            if isinstance(raw, str):
                raw = raw.encode()
            # double safety: even via the All Mail fallback, filter Scholar alerts
            if re.search(rb"^From:.*scholaralerts", raw, re.M | re.I):
                seen_uids.add(uid_s)   # deliberate skip, mark handled to avoid re-fetching
                return True
            mid = msg_id(raw)
            if mid and mid in seen_ids:
                return True  # already downloaded under another label
            try:
                dt = parsedate_to_datetime(
                    re.search(rb"^Date:\s*(.+)$", raw, re.M | re.I).group(1).decode(errors="ignore"))
            except Exception:
                dt = None
            year = str(dt.year) if dt else "unknown"
            month = f"{dt.month:02d}" if dt else "00"
            subj = re.search(rb"^Subject:\s*(.+)$", raw, re.M | re.I)
            frm = re.search(rb"^From:\s*(.+)$", raw, re.M | re.I)
            _, faddr = parseaddr(frm.group(1).decode(errors="ignore")) if frm else ("", "")
            cat = categorize(faddr, "").replace("/", "")
            fname = f"{uid_s}_{safe_name(subj.group(1).decode(errors='ignore') if subj else '')}.eml"
            outdir = os.path.join(BACKUP_DIR, "eml", cat, year, month)
            os.makedirs(outdir, exist_ok=True)
            with open(os.path.join(outdir, fname), "wb") as f:
                f.write(raw)
            if mid:
                seen_ids.add(mid)
            seen_uids.add(uid_s)
            total_new += 1
            # Checkpoint every 100 messages so an interrupted run resumes cleanly
            if total_new % 100 == 0:
                state[folder] = sorted(seen_uids)
                state["_msgids"] = sorted(seen_ids)
                save_state(state)
            return True

        before_folder = total_new
        folder_failed = 0
        for i in range(0, len(new_ids), 25):
            batch = new_ids[i:i + 25]
            done = set()
            try:
                status, msg = M.uid("FETCH", ",".join(batch), "(RFC822)")
            except Exception as e:
                status, msg = f"EXC:{type(e).__name__}", None
            if status != "OK" or not msg:
                print(f"  ⚠️ batch failed ({status}) → per-message retry for {len(batch)}")
            else:
                for part in msg:
                    if not isinstance(part, tuple) or not part[0]:
                        continue
                    mu = re.search(rb"UID (\d+)", part[0])
                    if not mu:
                        continue
                    uid_s = mu.group(1).decode()
                    raw = part[1]
                    if store(uid_s, raw):
                        done.add(uid_s)
            # Missing/failed of this batch → per-message retry (nothing lost before the next run)
            for uid in batch:
                if uid in done:
                    continue
                try:
                    st, m1 = M.uid("FETCH", uid, "(RFC822)")
                    if st == "OK" and m1 and m1[0]:
                        if store(uid, m1[0][1] if isinstance(m1[0], tuple) else m1[0]):
                            continue
                except Exception:
                    pass
                folder_failed += 1
        if total_new > before_folder:
            print(f"  ✅ wrote {total_new - before_folder} new this folder")
        if folder_failed:
            print(f"  ⚠️ {folder_failed} failed per-message retries, retried next run")
        total_failed += folder_failed

        state[folder] = sorted(seen_uids)
        state["_msgids"] = sorted(seen_ids)
        save_state(state)

    M.logout()
    print(f"\n✅ Done, {total_new} new messages → {BACKUP_DIR}/eml/")
    if total_failed:
        print(f"⚠️ {total_failed} failed retries, exit code 2 triggers outer retry")
        sys.exit(2)


if __name__ == "__main__":
    main()
