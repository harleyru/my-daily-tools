---
name: notify
description: Push a reminder to a Lark (Feishu) custom-bot webhook (auto-prefixes the required keyword) and mirror it to Discord. Invoke when the user says "push a reminder X" or "send to Feishu X".
argument-hint: '"<message>" [-u for urgent]'
allowed-tools:
  - Bash
---

# notify — Lark / Discord notifications

## When to use

- The user says "push a reminder …", "send to Feishu …", "notify me when it's done" → `notify.sh`
- A long job finished (download, analysis, scheduled task) and is worth interrupting for → use the widest channel set you have configured
- From other skills: radar, the weekly report and any drift check call this skill rather than reimplementing a push

## Usage

```bash
"$SKILL_DIR/notify.sh" "message text"         # normal (Lark + Discord mirror)
"$SKILL_DIR/notify.sh" "message text" -u       # urgent (🔔 prefix)
```

## Decision rules

| Situation | Use |
|---|---|
| Ordinary progress / result | `notify.sh` |
| The user should look now | `notify.sh "…" -u` |
| Long job finished, important conclusion | every channel you have configured |
| Do not want the Discord mirror | `DAILY_NO_DISCORD=1` |

## Gotchas

- **A Lark webhook with a required keyword rejects any message that lacks it.** `notify.sh` injects the keyword itself — a hand-rolled `curl` straight at the webhook gets a 400. Always go through the script. The keyword *value* belongs in credentials, never in code or docs.
- **A mirror must never be able to break the primary.** The Discord copy runs in its own branch and fails silently; `DAILY_NO_DISCORD=1` skips it entirely.
- **Store keychain values as command arguments, not through a pipe.** Use `security add-generic-password -s <service> -w '<value>'`. The tempting `printf … | security add-generic-password -w "$(cat)"` stores an **empty** password, because `$(cat)` reads the shell's own stdin, not that pipe.
- **Cross-script calls must use `$SCRIPT_DIR`-relative paths**, never a hardcoded absolute path into your checkout — the same skill runs on another machine where that path does not exist. Two copies of a script drifting in exactly this way is how one tree ends up broken while the other still works.
- **A channel that is OS-specific must degrade to a skip, not an error.** Anything driving a GUI app via AppleScript does not exist on a Linux host; if it raises, one unavailable channel takes down the whole notification.

## Architecture

Credentials resolve in a fixed order: a `.env` file in the project base first, then the OS keychain as a fallback. That lets the identical script run on a workstation (keychain) and on a server (`.env`) with no code change, and nothing is ever hardcoded.

Push fan-out is deliberately one-directional: a core script sends the essential channel, and any mirror or extra channel is additive — failures there are logged and swallowed, because a notification that half-fails is worse than one that arrives on fewer channels.

## Files here

- `notify.sh` — Lark + Discord mirror (core; every other script calls this)
