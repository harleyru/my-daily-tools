#!/bin/bash
# Send a reminder via a Lark (Feishu) custom bot and mirror it to Discord.
# Usage: notify.sh "message" [-u]     # -u = urgent (🔔 prefix)
# Credentials: .env first (project root), fall back to macOS Keychain (lark_webhook / lark_keyword)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
get_env() {
    [ -f "$ENV_FILE" ] && grep "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-
}
HOOK=$(get_env lark_webhook)
[ -z "$HOOK" ] && HOOK=$(security find-generic-password -s lark_webhook -w 2>/dev/null)
KEYWORD=$(get_env lark_keyword)
[ -z "$KEYWORD" ] && KEYWORD=$(security find-generic-password -s lark_keyword -w 2>/dev/null)

if [ -z "$HOOK" ] || [ -z "$1" ]; then
    echo "Usage: notify.sh \"message\" [-u]" >&2
    exit 1
fi

TEXT="${KEYWORD} "
[ "$2" = "-u" ] && TEXT+="🔔 Urgent: "
TEXT+="$1"

RESP=$(curl -s -X POST "$HOOK" \
    -H "Content-Type: application/json" \
    -d "{\"msg_type\":\"text\",\"content\":{\"text\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TEXT")}}")

echo "$RESP" | grep -q '"code":0' && echo "✅ Lark sent" || echo "❌ Lark failed: $RESP"

# Discord mirror: original text (without the Lark keyword prefix); sent even if Lark failed
# (acts as a fallback channel); a Discord failure never affects Lark
if [ -z "$DAILY_NO_DISCORD" ]; then
    DISCORD_TEXT="$1"
    [ "$2" = "-u" ] && DISCORD_TEXT="🔔 Urgent: $1"
    DISCORD_OUT=$("$SCRIPT_DIR/../discord-bridge/discord_tasks.py" --post "$DISCORD_TEXT" 2>&1)
    case "$DISCORD_OUT" in
        *"posted"*) echo "✅ Discord mirrored" ;;
        *) echo "⚠ Discord mirror failed: $DISCORD_OUT" >&2 ;;
    esac
fi
