# my-daily-tools

Portable [Claude Code](https://claude.com/claude-code) skills for personal daily automation. Each skill is a self-contained directory (`tools/<name>/`) with a `SKILL.md` — install by symlinking into `~/.claude/skills/` and they become available in every Claude Code session via `/skill-name`.

These are the tools I run for my own daily workflow. They are published as-is because they are cleanly separated from private data — credentials live in the OS keychain (macOS) or a `.env` file (Linux), never in code — and the patterns (keychain-first config, atomic writes, fail-closed design) may be worth reusing. YMMV; this is a personal collection, not a product.

## Contents

| Skill | What it does | Deps |
|---|---|---|
| `notify` | Push a message to a Lark (Feishu) webhook with a required keyword, mirror to Discord. | curl, macOS Keychain or `.env`, optional Discord bot |
| `radar` | Personal research radar: arXiv API + RSS + Hacker News Algolia → keyword prefilter + dedup (sqlite) → headless LLM editorial scoring → HTML report + notify push. Pure stdlib. | python3, optional `claude` CLI |
| `mail-watch` | Incremental Gmail sync to local .eml backup + push important senders to notify; chains ad-archive and forwarded-mail routing. | python3 + imaplib, Gmail app password |
| `mail-ad-review` | Archive/queue promotional Gmail automatically (remove `\Inbox` label; recoverable in All Mail), sender whitelist auto-archive. | python3, Gmail app password |
| `discord-bridge` | Poll a Discord channel for @-mentions → task pool (JSON) → reply receipt; query/QA/auto-execution tiers; queues "add event" requests for the Mac; renders replies as HTML. | python3, Discord bot token, `claude` CLI |
| `calendar` | Add/query/delete events on a macOS calendar via AppleScript (iCloud-synced). | macOS Calendar |
| `weekly-report` | Aggregate a week of daily markdown notes + task tracker → push weekly report via notify; also a daily morning digest. | python3 |

## Install

```bash
git clone https://github.com/harleyru/my-daily-tools.git
ln -s "$PWD/my-daily-tools/tools/"* ~/.claude/skills/   # new sessions pick them up
```

Each skill's `SKILL.md` documents its own configuration. The general model:

- **Credentials**: macOS → `security add-generic-password -s <service> -w '<value>'` (Keychain); Linux/server → `~/.env` in the project base (the scripts read `.env` first, then Keychain). No secrets are hardcoded anywhere.
- **Runtime state**: scripts write to `data/` under the project base — either the repo root (create `data/` after clone) or `$DAILY_BASE` if you set it. State files are gitignored.
- **Cross-skill calls** (e.g. `mail-watch` pushing via `notify`) resolve relative to the skill directory, so the layout works whether symlinked or run in place.

## Quick start per skill

```bash
# notify (macOS): store a Lark webhook, then
tools/notify/notify.sh "hello" -u

# radar: needs data/radar/config.json — copy from config-examples/radar.config.json and edit keywords
python3 tools/radar/radar_daily.py

# mail-watch: needs Gmail app password in Keychain (gmail_email, gmail_app_pass)
python3 tools/mail-watch/mail_watch.py

# discord-bridge: token in Keychain (discord_bot_token), channel config in data/discord/config.json
python3 tools/discord-bridge/discord_tasks.py --list
```

## Layout

```
tools/<skill>/SKILL.md   — usage + config for Claude Code sessions
tools/<skill>/<scripts>  — standalone scripts (also runnable without Claude Code)
config-examples/         — config templates (copy to data/, which is gitignored)
```

Every `SKILL.md` follows the same sections: **When to use** → **Usage** → **Decision rules** → **Gotchas** → **Architecture** → **Files here**. The Gotchas sections are the useful part — each one is a failure that actually happened, and most of them are portable lessons rather than notes about this particular setup.

## Notes & caveats

- `calendar` uses the calendar named "Agent" — edit `add_event.scpt` to your own calendar name.
- `mail-watch`'s `IMPORTANT_SENDERS` and `mail-ad-review`'s `AUTO_ARCHIVE_SENDERS` are personal rules — replace with your own.
- `mail_forward_move.py` is a reference implementation for routing forwarded mail from an institutional mailbox into a folder; adjust the domain/label to your own setup.
- `radar`'s editorial stage and `discord-bridge`'s answer tiers shell out to an LLM CLI headlessly — point `RADAR_EDITOR` (radar) or `CLAUDE_CLI` (discord-bridge) at any Anthropic-compatible CLI; both fall back to `claude`. A wrapper that routes to another provider works fine, as long as it clears `ANTHROPIC_*` / `CLAUDE_*` in the environment it passes down — inheriting a proxied session's env is a common cause of 401s.
- The `discord-bridge` auto-execution tier only grants read-only web tools to the LLM agent; mutating host actions are queued to the human session by contract (`need_session`).

## Running across two machines

This collection is built for a split setup: an **always-on Linux host** runs the schedules (radar, mail sync, weekly report, the Discord poll), while a **Mac** owns the two things only a Mac can do — the calendar and the local mail tree. Three rules make that split work, and they are the reason several of these scripts look more defensive than they need to:

- **Anything Mac-only is triggered by request, not assumed.** A calendar change arrives as a queued request that the Mac picks up on its own sync and answers by pushing a result back — the host never calls the Mac directly.
- **Data crosses in one direction and by explicit file list.** The Mac pushes a small, named set of files to the host; the host owns its own live `data/` and never syncs it back. A directory-level sync in both directions is how two machines silently overwrite each other.
- **Reads use snapshots, and a missing snapshot is reported, never faked.** A query that needs Mac-side data reads the last synced snapshot and says "not synced" when it is stale, rather than answering from nothing.

Credentials follow the same split: `.env` on the host, keychain on the Mac, same scripts.

## License

MIT — see [LICENSE](LICENSE).
