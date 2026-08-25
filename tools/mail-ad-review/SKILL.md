---
name: mail-ad-review
description: Automated review of promotional mail: auto-archive ads (remove the Gmail Inbox label, recoverable in All Mail), sender-whitelist auto-archive, queue shopping mails for review with a notification. Act on "delete #N" / "delete all ads" / "keep #N".
---

# mail-ad-review — Gmail ad auto-archive / pending review

## Usage

```bash
python3 "$SKILL_DIR/mail_ad_review.py" --scan          # scan new mail: archive promos, queue shopping, notify
python3 "$SKILL_DIR/mail_ad_review.py" --list          # show pending list
python3 "$SKILL_DIR/mail_ad_review.py" --delete 3,5    # delete listed ids (→ Gmail trash, 30-day recoverable)
python3 "$SKILL_DIR/mail_ad_review.py" --delete-all    # delete all pending
python3 "$SKILL_DIR/mail_ad_review.py" --keep 2,4      # keep listed ids (never prompted again)
python3 "$SKILL_DIR/mail_ad_review.py" --seed          # initialize (historical mail not treated as new)
```

## Rules

| Type | Handling |
|---|---|
| Ads/promos (category dir) | Auto-archive: remove the `\Inbox` label; searchable/recoverable in All Mail; local .eml kept |
| Sender whitelist (`AUTO_ARCHIVE_SENDERS` domain match, **empty by default — fill in**) | Direct archive (audit `kind: sender-auto`); on failure stays in Inbox with an audit entry, not queued |
| Shopping (orders/invoices) | Queued for pending review → notification → user decides keep/delete |

Delete = Gmail trash (30-day recoverable); local .eml kept. Audit: `data/mail_ad_archived.json` (capped at 500 entries).

## ⚠️ Gmail gotchas (do not change)

- With INBOX selected, `STORE -X-GM-LABELS ("\\Inbox")` is silently ignored (returns OK without effect) — archiving must run from the **All Mail** context
- Deleting: exact Message-ID search in All Mail → `\\Deleted` + EXPUNGE
- X-GM-RAW queries must be passed as a single quoted string

## Configuration

Keychain: `gmail_email` / `gmail_app_pass`. Sender whitelist at the top of the script (`AUTO_ARCHIVE_SENDERS`, substring domain match).

## Notes

Triggered from the `mail-watch` cycle; run manually in a session for an immediate review. On "delete #N", act by id and confirm.
