---
name: notify
description: Push a reminder to a Lark (Feishu) custom-bot webhook (auto-prefixes the required keyword) and mirror it to Discord. Invoke when the user says "push a reminder X" or "send to Feishu X".
---

# notify — Lark/Discord notifications

## Usage

```bash
"$SKILL_DIR/notify.sh" "message text"         # normal (Lark + Discord mirror)
"$SKILL_DIR/notify.sh" "message text" -u       # urgent (🔔 prefix)
```

## Configuration (credentials via macOS Keychain or `.env`, never hardcoded)

| Keychain service | Purpose |
|---|---|
| `lark_webhook` | Lark custom-bot webhook URL |
| `lark_keyword` | Required keyword for Lark (messages must contain it, else rejected) |

Discord mirror: full copy of the message; set `DAILY_NO_DISCORD=1` to skip; Discord failure never affects Lark.

## Setup on a new machine

- macOS: store in Keychain — `security add-generic-password -s lark_webhook -w '<url>'` (note: `printf | security add -w "$(cat)"` stores an empty password; pass the value as an argument directly). Linux server: put `lark_webhook=...` in the project-root `.env`.
- The scripts live in `tools/notify/` and are self-contained (the Discord mirror calls `../discord-bridge/discord_tasks.py`).

## Files in this directory

- `notify.sh` — Lark + Discord mirror (core)
