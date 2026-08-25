---
name: notify
description: 推送提醒到飞书自定义机器人（自动带关键词）+ 镜像 Discord。用户在会话里说「推送提醒 X」或「发飞书 X」时调用。
---

# notify — 飞书/Discord 通知推送

## 用法

```bash
"$SKILL_DIR/notify.sh" "消息内容"            # 普通（飞书 + Discord 镜像）
"$SKILL_DIR/notify.sh" "消息内容" -u          # 紧急（🔔 前缀）
```

## 配置（凭据走 macOS Keychain 或 .env，无硬编码）

| Keychain service | 用途 |
|---|---|
| `lark_webhook` | 飞书自定义机器人 webhook URL |
| `lark_keyword` | 飞书关键词（消息必须包含，否则拒收） |

Discord 镜像：全量 copy；设环境变量 `DAILY_NO_DISCORD=1` 跳过镜像；Discord 失败不影响飞书。

## 换机器复用

- macOS 先写入 Keychain：`security add-generic-password -s lark_webhook -w '<url>'`（注意：`printf | security add -w "$(cat)"` 会存空密码，必须直接传参）；Linux 服务器写在项目根 `.env`（`lark_webhook=...`）
- 脚本位置：`tools/notify/` 独立可迁移（Discord 镜像引用兄弟目录 `../discord-bridge/discord_tasks.py`）

## 本目录脚本

- `notify.sh` — 飞书 + Discord 镜像（核心）
