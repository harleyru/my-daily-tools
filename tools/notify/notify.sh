#!/bin/bash
# 通过飞书 Lark 自定义机器人发送提醒, 并镜像一份到 Discord(2026-08-16 起, 用户要求全量 copy,
# 观察后再决定删飞书哪些; 设 DAILY_NO_DISCORD=1 可跳过镜像)
# 用法: notify.sh "消息内容" [-u]     # -u = 紧急提醒
# 凭据: .env 优先(服务器 ~/daily/.env), 回退 Keychain (lark_webhook / lark_keyword)

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
    echo "用法: notify.sh \"消息\" [-u]" >&2
    exit 1
fi

TEXT="${KEYWORD} "
[ "$2" = "-u" ] && TEXT+="🔔 紧急提醒: "
TEXT+="$1"

RESP=$(curl -s -X POST "$HOOK" \
    -H "Content-Type: application/json" \
    -d "{\"msg_type\":\"text\",\"content\":{\"text\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$TEXT")}}")

echo "$RESP" | grep -q '"code":0' && echo "✅ 飞书已发送" || echo "❌ 飞书发送失败: $RESP"

# Discord 镜像: 发原文(不含飞书关键词前缀); 飞书失败也照发(相当于兜底渠道), 失败不影响飞书
if [ -z "$DAILY_NO_DISCORD" ]; then
    DISCORD_TEXT="$1"
    [ "$2" = "-u" ] && DISCORD_TEXT="🔔 紧急提醒: $1"
    DISCORD_OUT=$("$SCRIPT_DIR/../discord-bridge/discord_tasks.py" --post "$DISCORD_TEXT" 2>&1)
    case "$DISCORD_OUT" in
        *"已回帖"*) echo "✅ Discord 已镜像" ;;
        *) echo "⚠ Discord 镜像失败: $DISCORD_OUT" >&2 ;;
    esac
fi
