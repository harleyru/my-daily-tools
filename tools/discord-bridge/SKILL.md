---
name: discord-bridge
description: Discord 收件桥：轮询频道里 @触发角色（config.json 的 role_ids）的消息 → 记入待办池 → 频道回执；查询类消息自动回帖（任务清单/雷达/邮件待审/日程/自动问答）。用户在会话里说「处理 Discord 待办」时读池执行并 --done 回帖。
---

# discord-bridge — Discord 收件桥（待办池）

## 用法

```bash
python3 "$SKILL_DIR/discord_tasks.py" --poll          # 轮询一次（launchd 每 60 秒）
python3 "$SKILL_DIR/discord_tasks.py" --list          # 查看待办池
python3 "$SKILL_DIR/discord_tasks.py" --post "文本"   # 直接发频道
python3 "$SKILL_DIR/discord_tasks.py" --done N --result "结果"   # 完成回帖
python3 "$SKILL_DIR/discord_tasks.py" --file <path> [--text "说明"]  # 上传文件
python3 "$SKILL_DIR/discord_tasks.py" --add-role <id> # 追加触发角色
python3 "$SKILL_DIR/discord_tasks.py" --task          # 本地测试
```

## 消息分档（@消息）

| 类型 | 判定 | 处理 |
|---|---|---|
| 查询 | 正文含「任务清单/待办」或「任务/task+查询动词」（查询/查一下/发一下/看一下/列出/显示/list/目前/现在）| 不入池，回帖 ongoing.md 任务清单（主）+ 池内待办（次） |
| 规则查询 | 触发词 + 剩余为疑问式：雷达/邮件待审/日程 | 回帖对应信息（雷达卡片/广告待删清单/当日日程） |
| 自动问答 | 疑问式（?/吗/呢/为什么）| 无头 `claude -p --tools ""`（纯知识，无工具）回帖 |
| 自动执行 | 非查询/非疑问/无「入池」| 无头 `claude -p --allowedTools WebSearch,WebFetch --max-turns 12`，JSON 合同 `{need_session, answer}`；需改本机 → `need_session=true` 转池 |
| 手动 | 正文含「入池」强制 | 记入池，会话处理 |

## ⚠️ 重要约定

- **本机写入工具（Bash/Write/Edit）被安全分类器否决**——自动档只给 WebSearch/WebFetch；本机动手任务走合同转池 + 会话处理
- 无头 claude 子进程必须**剥离 `ANTHROPIC_*`/`CLAUDE_*` 环境变量**（会话跑代理时直接继承会 401；干净环境走 claude.ai 登录）
- `--poll` 与 `--done` 共用 flock 互斥（`data/discord/poll.lock`）；保存走原子写（tmp+fsync+os.replace）；**勿绕过锁手动改 tasks.json**（曾出现「回执已发但池里任务丢失」）
- 会话处理池内任务时先 `--post "📝 开始处理 #N…"` 发进度提示

## 配置

| 项 | 位置 |
|---|---|
| bot token | Keychain `discord_bot_token`（ACL `-T /usr/bin/security`，勿打印到 transcript） |
| channel id / role_ids | `data/discord/config.json` |

## 注意

- 定时跑用你自己的调度器（每 60 秒 `--poll`）；`data/discord/config.json` 从 `config-examples/discord.config.json` 复制，填你的 channel_id / role_ids（触发角色：用户常 @角色名而不是 @机器人）
- 修改待办池时走 `--poll`/`--done`（flock 互斥），勿绕过锁手动改 tasks.json
