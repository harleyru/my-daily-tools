---
name: discord-bridge
description: Discord inbox bridge: poll a channel for @-mentions of trigger roles (config.json role_ids) → task pool → channel receipt; query-type messages are answered in-thread (task list / radar / mail review / schedule / Q&A). Invoke "handle Discord todos" by reading the pool, executing, and replying with --done.
---

# discord-bridge — Discord inbox bridge (task pool)

## Usage

```bash
python3 "$SKILL_DIR/discord_tasks.py" --poll          # poll once (run every 60s from your scheduler)
python3 "$SKILL_DIR/discord_tasks.py" --list          # show task pool
python3 "$SKILL_DIR/discord_tasks.py" --post "text"   # post directly to the channel
python3 "$SKILL_DIR/discord_tasks.py" --done N --result "result"   # mark done with a reply
python3 "$SKILL_DIR/discord_tasks.py" --file <path> [--text "note"]  # upload a file
python3 "$SKILL_DIR/discord_tasks.py" --add-role <id> # add a trigger role
python3 "$SKILL_DIR/discord_tasks.py" --task          # local test
```

## Message routing (for @-mentions)

| Type | Trigger | Handling |
|---|---|---|
| Query | body contains task-list keywords (e.g. "task list", "todos", or task + query verb: list/show/current/now) | not pooled; reply with the task list (primary) + pooled todos (secondary) |
| Rule query | trigger word + rest is a question: radar / mail review / schedule | reply with the corresponding info (radar card / pending-ad list / today's schedule) |
| Q&A | interrogative (?, why, how, is it…) | headless `claude -p --tools ""` (knowledge only, no tools) reply |
| Auto-exec | not query, not interrogative, no "queue" marker | headless `claude -p --allowedTools WebSearch,WebFetch --max-turns 12`, JSON contract `{need_session, answer}`; host-mutating → `need_session=true`, routed to pool |
| Manual | body contains the "queue" marker | added to the pool, handled in a Claude session |

## ⚠️ Important conventions

- **Host-mutating tools (Bash/Write/Edit) are denied by the safety classifier** — the auto-exec tier only gets WebSearch/WebFetch; mutating tasks go to the pool by contract and are handled in a session
- Headless claude subprocesses must **strip `ANTHROPIC_*`/`CLAUDE_*` env vars** (inherited from a proxied session → 401; clean env → claude.ai login)
- `--poll` and `--done` share a flock mutex (`data/discord/poll.lock`); saves use atomic write (tmp+fsync+os.replace); **never edit tasks.json around the lock** (a receipt once fired with a lost task — see issue notes)
- When handling pooled tasks in a session, post `📝 Starting #N…` first as a progress receipt

## Configuration

| Item | Location |
|---|---|
| bot token | Keychain `discord_bot_token` (ACL `-T /usr/bin/security`; never print it to a transcript) |
| channel id / role_ids | `data/discord/config.json` |

## Notes

- Run `--poll` every 60s from your own scheduler; copy `config-examples/discord.config.json` to `data/discord/config.json` and fill in channel_id / role_ids (users usually @-mention a role name, not the bot)
- Mutate the pool only via `--poll`/`--done` (flock mutex), never by editing tasks.json directly
