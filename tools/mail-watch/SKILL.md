---
name: mail-watch
description: Incremental Gmail sync to a local .eml backup + push notifications for important senders (custom rules); also chains ad-archive and forwarded-mail routing. Invoke before searching when the user says "sync mail" / "check latest mail".
allowed-tools:
  - Bash
  - Read
---

# mail-watch — Gmail incremental sync + important-mail notifications

## When to use

- The user says "sync mail", "check the latest mail", "I just got an email, take a look"
- **Before searching the local .eml tree** — unsynced mail is not there yet, so the search would silently come up short
- Debugging "a class of mail never reached the backup tree", "ads were not archived", "forwarded mail was not filed"

## Usage

```bash
python3 "$SKILL_DIR/mail_watch.py"      # no arguments; one run does everything below
```

## Decision rules (what one run chains together)

| Order | Sub-task | Effect |
|---|---|---|
| 0 | Pre-check | `pgrep -f gmail_backup.py`; if a full backup is already running, **skip the whole round** (two processes writing one tree would collide) |
| 1 | `gmail_backup.py` | incremental Gmail → local .eml tree (resumable; skips trash and excluded labels) |
| 2 | Important-mail push | match this round's new mail against the rules → summarise → push |
| 3 | `mail_ad_review.py --scan` | auto-archive promos + whitelist senders; queue shopping mail for review |
| 4 | `mail_forward_move.py --scan` | mail forwarded in from an institutional mailbox → move into the configured folder |

- Physical storage: `<EMAIL_BACKUP>/eml/<category>/<year>/<month>/` (default `~/Documents/email_backup/gmail`, override with the `EMAIL_BACKUP` env var); index at `<EMAIL_BACKUP>/index.csv`
- **The index CSV must be parsed with the `csv` module** — subjects contain commas
- Resume state (`.state.json` / `.watch_state.json`) lives at the root of the backup tree; it must travel with the tree if you move it

## Important-sender rules

Ships with journal/submission systems (editorialmanager / scholarone / manuscriptcentral / editor@ …), publishers (@aps.org / @aip.org / @ioppublishing.org), and GitHub. Append a regex to the `IMPORTANT_SENDERS` tuple at the top of the script for your own (advisor, collaborators, procurement…). This is personal configuration — **the script is the source of truth**.

## Gotchas

- **The IMAP search index may be untrustworthy.** On some accounts `X-GM-RAW` / `SEARCH` returns **scrambled uids** — hits whose From does not match the query, with counts that drift between runs. The only dependable way to locate mail is **folder enumeration** (`FETCH 1:*` in batches, verifying the From header/labels per message). Base every mutation on an enumerated list, never on search results.
- **A batch `STORE`/`MOVE` returning OK does not mean it worked** — it can silently no-op or apply only partially. Work in small batches (~25) and re-`FETCH` to verify each batch.
- **With INBOX selected, `STORE -X-GM-LABELS ("\\Inbox")` is silently ignored** (returns OK, changes nothing). To take mail out of the inbox, use `UID MOVE`.
- **uids are folder-local and can be ghosted.** Verify against `FETCH` metadata; never trust a `SEARCH` count.
- **Do not run a second backup by hand while one is running.** The script's own `pgrep` guard protects the scheduled path, but invoking `gmail_backup.py` manually bypasses it and collides.
- **Mail arriving from an institutional mailbox is a forward, not a direct connection.** If that mailbox enforces MFA, IMAP is blocked entirely; the working pattern is webmail auto-forward → Gmail → this sync. So the tree contains forwarded copies, and rules must match on the forwarding headers (the original `To`, the `Resent-From`, or the forward signature in the body).

## Architecture

The sync is **incremental and resumable**: only mail newer than the last run is pulled, and state lives next to the data so an interrupted run continues rather than restarting. The tree is the durable artifact — mail is stored as `.eml` on disk, so searching and recovery do not depend on the IMAP account staying reachable.

This skill is the periodic entry point of the mail subsystem; the other mail skills are invoked from its cycle rather than scheduled independently, so there is exactly one writer at a time. That ordering matters: ad-archive and forwarded-mail routing both operate on the tree this step just updated, and run before — not after — it.

## Files here

| File | Purpose |
|---|---|
| `mail_watch.py` | main script: sync + push + chains the two below |
| `gmail_backup.py` | incremental backup to the .eml tree (called by mail_watch, also runnable alone) |
| `mail_ad_review.py` | ad/whitelist archiving, shopping queue (see the `mail-ad-review` skill) |
| `mail_forward_move.py` | forwarded-mail routing (`--scan` incremental / `--sweep` one-shot) |
