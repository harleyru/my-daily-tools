---
name: weekly-report
description: 周五周报：汇总本周每日文件「完成情况」+ 长期任务进展 → 飞书推送。用户在会话里说「生成周报」「周报」时执行（launchd 周五 17:00 自动）。
---

# weekly-report — 周报生成推送

## 用法

```bash
python3 "$SKILL_DIR/weekly_report.py"          # 本周（周五）
python3 "$SKILL_DIR/weekly_report.py" 2026-08-10  # 指定周
```

- 汇总 `<BASE>/日记/YYYY-MM-DD.md` 的「完成情况」小节 + `<BASE>/ongoing.md` 长期任务进展
- 推送走 notify.sh（飞书 + Discord 镜像）
- 定时跑用你自己的调度器（每周五）；`BASE` = 仓库根，或环境变量 `DAILY_BASE` 覆盖

## 配置

无独立配置——依赖你的笔记结构（`日记/`、`ongoing.md`，格式见仓库根 README）与 notify skill 的凭据。

## 本目录文件

- `weekly_report.py` — 周报主脚本
- `daily_push.py` — 早清单（每日 9:30，今日日程 + 长期任务；独立于周报，放此归档）
