#!/usr/bin/env python3
"""IMAP 备份 Gmail: 全部邮件下载为 .eml,按 年/月 归档,按 Message-ID 去重。

Gmail 特点: 同一封邮件会出现在多个标签文件夹里(如 Inbox 和 [Gmail]/All Mail)。
策略: 先下载具体标签(Inbox/Sent/自定义标签),[Gmail]/All Mail 最后兜底,
用 Message-ID 全局去重,保证不重复、标签信息尽量保留。

用法:
    gmail_backup.py                    # 增量备份
    gmail_backup.py --dry-run          # 只统计不下载
凭据: Keychain (gmail_email / gmail_app_pass)
存档: ~/Documents/email_backup/gmail/
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
    # .env 优先(服务器 ~/daily/.env), 回退 macOS Keychain(Mac 本地)
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
            pass  # 状态文件损坏(如写入中断),当作无进度处理
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n]+', "_", s or "no_subject")
    return s[:80].strip() or "no_subject"


def msg_id(raw):
    m = re.search(rb"^Message-ID:\s*(.+)$", raw, re.M | re.I)
    # 解码为 str,否则 bytes 无法写入 JSON 状态文件
    return m.group(1).strip().lower().decode(errors="ignore") if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=keychain("gmail_email"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    password = keychain("gmail_app_pass")
    if not args.email or not password:
        print("❌ 缺少凭据。先运行:")
        print("  security add-generic-password -U -a $USER -s gmail_email -w <邮箱>")
        print("  security add-generic-password -U -a $USER -s gmail_app_pass -w <16位应用密码>")
        sys.exit(1)

    M = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        M.login(args.email, password)
    except imaplib.IMAP4.error as e:
        print(f"❌ 登录失败: {e}")
        print("   检查: 1) 应用密码是否16位 2) Gmail 设置里 IMAP 是否开启(设置→查看所有设置→转发和POP/IMAP→启用IMAP)")
        sys.exit(3)
    # 连接卡住时 300 秒后报错退出(外层循环会自动重试),而不是永远挂起
    # 批量 FETCH 响应大, 超时放宽到 5 分钟
    M.socket().settimeout(300)

    state = load_state()
    seen_ids = set(state.get("_msgids", []))
    total_new = 0
    total_failed = 0

    # 兜底: 上次中断时状态未存档,从已下载文件重建 Message-ID 集合,避免重复下载
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
            print(f"从已下载文件重建 {len(seen_ids)} 个 Message-ID")

    # 具体标签先下,All Mail 最后兜底;垃圾箱/已删除跳过
    def parse_folder(line):
        line = line.decode(errors="ignore") if isinstance(line, bytes) else line
        m = re.search(r'"([^"]*)"\s*$', line)   # 取行尾引号内的文件夹名
        return m.group(1) if m else line.strip()

    SKIP = ("[Gmail]/Spam", "[Gmail]/Bin", "[Gmail]/Trash", "Google scholar")
    folders = [parse_folder(f) for f in M.list()[1]]
    folders = [f for f in folders if f not in SKIP]
    folders.sort(key=lambda f: "[Gmail]/All Mail" in f)

    for folder in folders:
        # 注意: 本机 imaplib 不自动给文件夹名加引号,含空格的名字必须手动加
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

        # 快速头扫描: 批量只取 Message-ID, 已在其他文件夹下过的直接跳过
        # (否则 All Mail 等文件夹会把 3 万封再拉一遍又丢弃, 白白多跑十几个小时)
        if new_ids:
            scan = new_ids
            new_ids = []
            for i in range(0, len(scan), 500):
                batch = ",".join(scan[i:i + 500])
                st, d = M.uid("FETCH", batch, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM)] UID)")
                if st != "OK" or not d:
                    new_ids.extend(scan[i:i + 500])   # 扫描失败退回直接下载, 宁慢勿漏
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
                        seen_uids.add(mu.group(1).decode())   # Scholar 警报有意跳过, 记入已处理
                        continue
                    mid = msg_id(body or b"")
                    if mid and mid in seen_ids:
                        seen_uids.add(mu.group(1).decode())   # 已下过, 记入已处理
                    else:
                        new_ids.append(mu.group(1).decode())
            print(f"📁 {folder}: {n} 封, 头扫描 {len(scan)} → 实际需下 {len(new_ids)} 封")
        else:
            print(f"📁 {folder}: {n} 封, 新 {len(new_ids)} 封")
        if args.dry_run:
            continue

        # 批量下载: 每次 FETCH 25 封; 整批失败自动退化为单封补齐, 保证不漏
        def store(uid_s, raw):
            """写入一封邮件, 返回是否已处理(含有意跳过)。"""
            nonlocal total_new
            if not raw:
                return False
            if isinstance(raw, str):
                raw = raw.encode()
            # 双保险: 即使通过 All Mail 兜底,Scholar 警报也过滤掉
            if re.search(rb"^From:.*scholaralerts", raw, re.M | re.I):
                seen_uids.add(uid_s)   # 有意跳过, 记入已处理避免下轮重复拉取
                return True
            mid = msg_id(raw)
            if mid and mid in seen_ids:
                return True  # 已在别的标签下载过
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
            # 每 100 封存档一次断点,中断/卡死重启不丢进度
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
                print(f"  ⚠️ 批量失败({status}) → 单封补齐 {len(batch)} 封")
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
            # 该批缺失/失败的 → 单封补齐(下一轮运行前不漏)
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
            print(f"  ✅ 本文件夹实际写入 {total_new - before_folder} 封")
        if folder_failed:
            print(f"  ⚠️ 本文件夹 {folder_failed} 封单封补齐失败,下一轮重试")
        total_failed += folder_failed

        state[folder] = sorted(seen_uids)
        state["_msgids"] = sorted(seen_ids)
        save_state(state)

    M.logout()
    print(f"\n✅ 完成,本次新下载 {total_new} 封 → {BACKUP_DIR}/eml/")
    if total_failed:
        print(f"⚠️ 其中 {total_failed} 封补齐失败,退出码 2 触发外层重试")
        sys.exit(2)


if __name__ == "__main__":
    main()
