---
name: discord-bridge
description: Discord inbox bridge — poll a channel for @-mentions of trigger roles (config.json role_ids) → task pool → channel receipt; query-type messages are answered in-thread (task list / radar / mail review / schedule / Q&A / read-only web research), and "add event" requests are queued for the Mac to write into Calendar. Invoke "handle Discord todos" by reading the pool, executing, and replying with --done.
argument-hint: '[--poll | --list | --done N [--result "…"] | --post "text" | --file <path>]'
allowed-tools:
  - Bash
  - Read
---

# discord-bridge — Discord inbox bridge (task pool)

## When to use

- The user says "handle the Discord todos" → `--list`, work the pool item by item, then `--done N --result "…"`
- While working a pooled item, **first** post a progress receipt so the channel knows it is being handled
- Debugging "the message was sent but never reached the pool" or "a receipt went out but the task vanished" → see the lock gotcha

## Usage

```bash
python3 "$SKILL_DIR/discord_tasks.py" --poll                        # poll once (your scheduler runs this every 60s)
python3 "$SKILL_DIR/discord_tasks.py" --list                        # show the task pool
python3 "$SKILL_DIR/discord_tasks.py" --post "text"                 # post directly to the channel
python3 "$SKILL_DIR/discord_tasks.py" --done N --result "result"    # mark done and reply
python3 "$SKILL_DIR/discord_tasks.py" --file <path> [--text "note"] # upload a file to the channel
python3 "$SKILL_DIR/discord_tasks.py" --cleanup [--keep N]          # archive finished items (default: keep 10)
python3 "$SKILL_DIR/discord_tasks.py" --answer "question"           # local test: Q&A tier
python3 "$SKILL_DIR/discord_tasks.py" --task "instruction"          # local test: auto-exec tier
python3 "$SKILL_DIR/discord_tasks.py" --discover                    # list visible channel ids
python3 "$SKILL_DIR/discord_tasks.py" --use-channel <id>            # set the channel
python3 "$SKILL_DIR/discord_tasks.py" --add-role <id>               # add a trigger role
```

## Decision rules (how @-mentions are routed)

Evaluated **in order — first match wins**:

| Tier | Trigger | Handling |
|---|---|---|
| Add event | body contains an add-event verb | parsed, queued for the Mac, **not pooled** (see Architecture) |
| Task list | body contains task-list words, or task + a query verb (list / show / current / now …) | not pooled; reply with the task list (primary) + pooled items (secondary) |
| Rule query | trigger word + the remainder is a question: radar / mail review / schedule | reply with the matching info (latest radar card / pending-ads list / today's-or-tomorrow's schedule) |
| Q&A | interrogative (?, why, how, is it …) | headless `claude -p --tools ""` (knowledge only, no tools) and reply |
| Auto-exec | not a query, not interrogative, no "queue" marker | headless `claude -p --allowedTools WebSearch,WebFetch --max-turns 12`, JSON contract `{need_session, answer}`; anything host-mutating → `need_session=true` and routed to the pool |
| Manual | body contains the "queue" marker | added to the pool, handled in a session |

## Gotchas

- **Parse order matters twice over.** The add-event check must run **before** the rule-query check, or "schedule a meeting" is misread as "what's my schedule". And within the add-event tier the verbs must be stripped **longest first** (`sorted(verbs, key=len, reverse=True)`), or a longer verb gets partially eaten by a shorter one and the remainder parses wrong.
- **The auto-exec tier gets read-only tools only.** Host-mutating tools are denied (the agent could rewrite files or reach the shell); mutating work goes to the pool by contract via `need_session=true`, and a human session does it. Do not widen this.
- **Never bypass the lock.** `--poll` and `--done` share a flock; saves are atomic (tmp + fsync + `os.replace`) and re-read for self-check after writing. Editing `tasks.json` by hand around the lock has already caused a receipt to fire with the task lost — always mutate the pool through the CLI.
- **A reply path must never be able to kill the poll loop.** Renders, file writes, `post()` and uploads each need their own `try`: one unhandled exception in reply formatting once took the whole poll loop down and silently discarded research results. Write the raw `.md` to disk **first and unconditionally**, render HTML in a separate step, and fall back to attaching the raw file.
- **Guard the upload with an existence check.** The upload helper reports failure by exiting the process, and `SystemExit` does not derive from `Exception` — an `except Exception` around it will not catch it, and the poll loop dies. Check the file exists before calling it.
- **If you point the CLI env vars at a wrapper that routes to another provider, have the wrapper strip `ANTHROPIC_*` / `CLAUDE_*` from the child environment** — inheriting a proxied session's env causes 401s. Resolve the CLI through `shutil.which` with a `~/.local/bin` fallback: schedulers run with a narrow `PATH`.
- **Never print the bot token into a transcript.** It lives in the keychain / `.env`.

## Architecture

The bridge is a **two-way** link between a chat channel and this machine:

```
message @-mention → poll (every 60s on an always-on host)
  → classify → reply in-thread, or add to the task pool
  → host-mutating work waits in the pool until a session picks it up
```

Some work cannot run on the scheduler host at all — anything touching the Mac's calendar. That is handled by a **queue and reconcile** pattern rather than a direct call: the always-on host parses the request and appends it to a pending-events file, the Mac picks the file up on its own periodic sync and performs the privileged write, then pushes the result back, and the next poll turns that result into a channel reply and clears the entry. The same file is the shared object across two machines, so every write is atomic and every reader re-reads before modifying — otherwise one side's receipt erases the other side's fresh entry.

Read-only queries follow the same rule as the rest of the collection: data that lives on the Mac (the calendar) is **snapshotted** to the host on a schedule and read from there, so a query never depends on the Mac being awake. A missing or stale snapshot reports "Mac has not synced" rather than silently answering from nothing.

Pool state is a single JSON file mutated only while holding the lock, and the archive is a separate file, so cleanup never races the poll.

## Files here

| File | Purpose |
|---|---|
| `discord_tasks.py` | main script (poll / task pool / Q&A / auto-exec / event queue) |
| `markdown_render.py` | Markdown → HTML (pure stdlib), used for replies and `.html` attachments |

Config template: `config-examples/discord.config.json` → copy to `data/discord/config.json` and fill in `channel_id` / `role_ids` (people usually @-mention a **role**, not the bot, so the role ids are what actually trigger a match).
