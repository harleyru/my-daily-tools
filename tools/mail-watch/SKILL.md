---
name: mail-watch
description: 增量同步 Gmail 新邮件到本地 eml 备份 + 重要邮件（自定规则）推送通知。用户在会话里说「同步邮件」「查最新邮件」「刚收到邮件看下」时先跑这个再搜。
---

# mail-watch — Gmail 增量同步 + 重要邮件推送

## 用法

```bash
python3 "$SKILL_DIR/mail_watch.py"
```

- 增量同步（只拉比上次更新的邮件）→ 按规则分类 → 重要邮件推飞书+Discord
- 邮件物理存放：`<EMAIL_BACKUP>/eml/<分类>/<年>/<月>/`（默认 `~/Documents/email_backup/gmail`，可用环境变量 `EMAIL_BACKUP` 覆盖）
- 备份中后台任务运行时自动跳过（不冲突）

## 重要邮件规则（脚本头部 IMPORTANT_SENDERS）

默认含期刊/投稿系统（editorialmanager/scholarone/manuscriptcentral/editor@ 等）、出版社（@aps.org/@aip.org/@ioppublishing.org）、GitHub。**按你的场景追加**（导师、合作方、采购等）。

## 配置

| Keychain service | 用途 |
|---|---|
| `gmail_email` | Gmail 账号 |
| `gmail_app_pass` | Gmail 应用专用密码 |

## 新增重要发件人

在脚本头部 `IMPORTANT_SENDERS` 元组追加正则即可。

## 注意

- 定时跑用你自己的调度器；手动跑用于「刚收到邮件」立即同步
- 新邮件**先过 mail_watch 同步**再搜索本地 eml（`mail-ad-review` skill 会在同步后自动处理广告/白名单）
