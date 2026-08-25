---
name: calendar
description: 操作 macOS「Agent」日历（AppleScript）：加日程/查当日日程/删事件。用户在会话里说「加日程 X 时间」「今天有什么日程」时调用。事件会同步 iCloud（iPhone）。
---

# calendar — macOS 日历操作（AppleScript）

> 日历名默认 `Agent`（本仓库作者的习惯命名）——**改成你自己的日历名**：改 `add_event.scpt` 及下面模板中的 `calendar "Agent"`。

## 可靠模式（踩坑后总结，勿改）

1. **先 `open -a Calendar && sleep 4`** 再执行 osascript（否则 Calendar 未运行时报错）
2. **加事件**：用 `current date` 构造（set year/month/day/hours/minutes/seconds），模板见 `add_event.scpt`；事件默认 1 小时
3. **查事件**：`whose summary contains "关键词"`（**不要用 `is` 精确匹配中文，会 -1728 失败**）；日期下界必须手动把 hours/minutes/seconds 重置为 0（`current date` 保留当前时刻会漏掉当天早上的事件）
4. **删事件**：列出 uid 后 `delete (first event of calendar "Agent" whose uid is "..." )`——循环内对 `whose` 过滤列表 delete 会使迭代器失效（-1728 "can't get item 2"）

## 模板

```applescript
tell application "Calendar"
	tell calendar "Agent"
		set startDate to current date
		set year of startDate to 2026
		set month of startDate to 8
		set day of startDate to 21
		set hours of startDate to 10
		set minutes of startDate to 0
		set seconds of startDate to 0
		set endDate to startDate + (1 * hours)
		make new event with properties {summary:"事件名", start date:startDate, end date:endDate}
	end tell
end tell
```

## 查询当日日程（中文 locale 安全写法）

```applescript
tell application "Calendar"
	tell calendar "Agent"
		set lb to current date
		set hours of lb to 0
		set minutes of lb to 0
		set seconds of lb to 0
		set ub to lb + (1 * days)
		set out to ""
		repeat with ev in (every event whose start date ≥ lb and start date < ub)
			set out to out & (time string of (start date of ev)) & " " & (summary of ev) & linefeed
		end repeat
		return out
	end tell
end tell
```

注意：`«class isot»` 转文本会失败，用 `time string`（中文 locale 安全）。

## 本目录文件

- `add_event.scpt` — 加事件模板（改日期/时间/标题后 `osascript add_event.scpt`）
