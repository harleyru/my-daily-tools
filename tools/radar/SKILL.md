---
name: radar
description: 研究雷达：arXiv API + RSS + HN Algolia 抓取 → 关键词预筛去重 → claude -p 无头编辑台打分 → 生成 reports/radar.html + 飞书推送。用户在会话里说「跑雷达」「今天雷达」时执行。
---

# radar — 研究雷达（个人研究信息源）

## 流程

```
radar_fetch.py（抓取）→ radar_prefilter.py（关键词召回 + seen.sqlite 去重 + 命中密度排序）
→ radar_daily.py 编辑台（claude -p --output-format json，无头不用工具）
→ frozen v1 合同校验（违规自动重试一次）→ reports/radar.html + 飞书 notify.sh 推送
```

## 用法

```bash
python3 "$SKILL_DIR/radar_daily.py" [YYYY-MM-DD] [--skip-fetch] [--skip-editorial] [--skip-notify]
```

## 配置与画像（data/radar/）

| 文件 | 用途 |
|---|---|
| `config.json` | 源/关键词/预算 |
| `interest-profile.md` | 打分锚点（修订记入文内日志） |
| `daily-prompt.md` | 编辑台任务书 |
| `seen.sqlite` | 去重表（**改关键词后需删表重置**才能重评旧条目） |

数据：`data/radar/raw|editorial|public/YYYY-MM-DD/`。预筛命中率 ~75% 属预期（recall-first），排序代理分保证强信号进 top N。

## 注意

- 编辑台**无探索层**（v1 纯打分，不联网）
- 无头 claude 子进程需剥离 `ANTHROPIC_*`/`CLAUDE_*` 环境变量（跑在代理会话里直接继承会 401）
- 定时跑用你自己的调度器（如 launchd/systemd cron），手动跑用于补跑/重试
- 数据目录：`data/radar/`（无则复制 `config-examples/radar.config.json` 初始化）
