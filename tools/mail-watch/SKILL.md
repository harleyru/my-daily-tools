---
name: mail-watch
description: Incremental Gmail sync to a local .eml backup + push notifications for important senders (custom rules). Invoke before searching when the user says "sync mail" / "check latest mail".
---

# mail-watch — Gmail incremental sync + important-mail notifications

## Usage

```bash
python3 "$SKILL_DIR/mail_watch.py"
```

- Incremental sync (only mail newer than the last run) → sort by rules → push important mail to notify
- Physical storage: `<EMAIL_BACKUP>/eml/<category>/<year>/<month>/` (default `~/Documents/email_backup/gmail`, override with the `EMAIL_BACKUP` env var)
- Skips automatically while a background full backup is running (no conflict)

## Important-sender rules (IMPORTANT_SENDERS at the top of the script)

Ships with journal/submission systems (editorialmanager / scholarone / manuscriptcentral / editor@ …), publishers (@aps.org / @aip.org / @ioppublishing.org), and GitHub. **Add your own** (advisor, collaborators, procurement…).

## Configuration

| Keychain service | Purpose |
|---|---|
| `gmail_email` | Gmail account |
| `gmail_app_pass` | Gmail app password |

## Adding important senders

Append a regex to the `IMPORTANT_SENDERS` tuple at the top of the script.

## Notes

- Schedule with your own cron/systemd/launchd; run manually for an immediate "just got mail" sync
- New mail **goes through mail_watch first**, then search the local .eml tree (the `mail-ad-review` skill processes ads/whitelist after sync)
