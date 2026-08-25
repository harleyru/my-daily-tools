---
name: mail-ad-review
description: 新邮件广告审查：广告促销自动归档（移除 Gmail 收件箱标签，All Mail 可恢复）+ 发件人白名单直接归档 + 购物类记入待定清单推飞书，用户说「删 #N」/「广告全删」/「留 #N」时执行。
---

# mail-ad-review — 邮件广告自动归档/待定审查

## 用法

```bash
python3 "$SKILL_DIR/mail_ad_review.py" --scan          # 扫描新邮件：促销自动归档，购物记待定并推飞书
python3 "$SKILL_DIR/mail_ad_review.py" --list          # 查看待定清单
python3 "$SKILL_DIR/mail_ad_review.py" --delete 3,5    # 删除指定编号（→ Gmail 回收站，30 天可恢复）
python3 "$SKILL_DIR/mail_ad_review.py" --delete-all    # 删除全部待定
python3 "$SKILL_DIR/mail_ad_review.py" --keep 2,4      # 保留指定编号（不再提示）
python3 "$SKILL_DIR/mail_ad_review.py" --seed          # 初始化（历史邮件不当作新邮件）
```

## 规则

| 类型 | 处理 |
|---|---|
| 广告促销（分类目录） | 自动归档：移除 `\Inbox` 标签，All Mail 可搜可恢复，本地 eml 照存 |
| 发件人白名单（`AUTO_ARCHIVE_SENDERS` 域名匹配，**默认空，按需填**） | 直接归档（审计 `kind: sender-auto`）；失败留收件箱记审计不转待定 |
| 购物（订单/发票类） | 记入待定清单 → 推送 → 用户判断删/留 |

删除 = Gmail 回收站（30 天可恢复），本地 eml 保留归档。审计：`data/mail_ad_archived.json`（上限 500 条）。

## ⚠️ Gmail 坑（勿改）

- INBOX 选中态下 `STORE -X-GM-LABELS ("\\Inbox")` 会被静默忽略（返回 OK 不生效）——归档必须从 **All Mail** 上下文执行
- 删除用 Message-ID 在 All Mail 精确搜索 → `\\Deleted` + EXPUNGE
- X-GM-RAW 查询必须作为单个引号包裹字符串传递

## 配置

Keychain：`gmail_email` / `gmail_app_pass`。发件人白名单在脚本头部 `AUTO_ARCHIVE_SENDERS`（按域名子串匹配）。

## 注意

由 mail-watch 30 分钟循环触发（launchd），会话内手动跑用于立即审查。用户说「删 #N」时按编号操作并回执。
