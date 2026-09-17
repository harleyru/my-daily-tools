---
name: mail-ad-review
description: Automated review of promotional mail — auto-archive ads (remove the Gmail Inbox label, recoverable in All Mail), sender-whitelist auto-archive, queue shopping mails for review with a notification. Act on "delete by id" / "delete all ads" / "keep by id".
argument-hint: '[--scan | --list | --delete N | --delete-all | --keep N | --seed]'
allowed-tools:
  - Bash
  - Read
---

# mail-ad-review — Gmail ad auto-archive / pending review

## When to use

| The user says | Action |
|---|---|
| after "sync mail", or "scan the ads" | `--scan` |
| "what's pending" / "what ads are queued" | `--list` |
| "delete #N" / "delete all ads" | `--delete N` / `--delete-all` |
| "keep #N" | `--keep N` (drops it from the pending list, never asked again) |

## Usage

```bash
python3 "$SKILL_DIR/mail_ad_review.py" --scan          # scan new mail: archive promos, queue shopping, notify
python3 "$SKILL_DIR/mail_ad_review.py" --list          # show the pending list
python3 "$SKILL_DIR/mail_ad_review.py" --delete 3,5    # delete by id (→ Gmail trash, recoverable for 30 days)
python3 "$SKILL_DIR/mail_ad_review.py" --delete-all    # delete everything pending
python3 "$SKILL_DIR/mail_ad_review.py" --keep 2,4      # keep by id (never prompted again)
python3 "$SKILL_DIR/mail_ad_review.py" --seed          # initialize: mark historical mail as already handled
```

## Decision rules

| Type | Handling |
|---|---|
| Ads / promos (category directory) | **Auto-archive**: remove the `\Inbox` label; stays searchable and recoverable in All Mail; local .eml kept |
| Sender whitelist (`AUTO_ARCHIVE_SENDERS`, domain match — **empty by default, fill it in**) | Archive directly, audited as `kind: sender-auto`; **on failure it stays in the inbox with an audit entry rather than being queued** |
| Shopping (orders / invoices) | Queued for review → notification → the user decides keep or delete |

- Whitelist matching covers the **whole .eml tree**, not just the ad/shopping directories
- Delete = Gmail trash (recoverable for 30 days); **local .eml is always kept**
- Audit: `data/mail_ad_archived.json` (capped at 500 entries); queue: `data/mail_ad_pending.json`

## Gotchas

- **With INBOX selected, `STORE -X-GM-LABELS ("\\Inbox")` is silently ignored** — it returns OK and changes nothing. Archiving must run from the **All Mail** context, and taking mail out of the inbox needs `UID MOVE`.
- **Do not trust `X-GM-RAW` / `SEARCH` for locating mail.** On some accounts the search index returns scrambled uids (hits whose sender does not match the query, counts that drift between runs). Locate mail by **folder enumeration plus per-message verification** instead.
- **A batch operation returning OK is not evidence it applied.** Work in small batches (~25) and re-`FETCH` to confirm.
- **`X-GM-RAW` queries must be passed as a single quoted string.**
- **Never let an archive failure disappear silently.** A failure must be recorded in the audit and leave the message in the inbox — or be handed to the pending queue for a human decision. Quietly dropping it is the one outcome that is not acceptable, since the message then exists nowhere the user will look.
- **Scan after syncing, not before.** Running `--scan` against a tree the sync has not updated yet re-reports stale state.

## Architecture

This skill has **no schedule of its own** — it is invoked from the mail sync cycle, right after the incremental sync that brings new mail in, so that exactly one process is walking the tree at a time. Running it manually in a session is the "review now" path.

Its design principle is **reversible by default**: the archive action removes a label rather than moving or deleting anything, so every automatic decision can be undone from All Mail, and the local `.eml` copy survives regardless. Only the explicit `--delete` path (driven by a human decision) touches the trash, and even that is recoverable. Anything the rules are not confident about is queued for a person instead of being acted on.

Every automatic mutation is written to an audit file, so "why did this mail leave my inbox" is answerable after the fact.

## Files here

- `mail_ad_review.py` — the whole skill (single script)
