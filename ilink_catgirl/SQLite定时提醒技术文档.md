# SQLite 定时提醒技术文档

## 1. SQLite 是什么

SQLite 是一个“数据库文件 + 程序库”。它不需要单独启动数据库服务器，也没有账号、端口和后台服务。Python 自带 `sqlite3` 模块，项目第一次启动时会自动创建：

```text
ilink_catgirl/data/reminders.sqlite3
```

这个文件同时保存提醒任务、最近一次用户消息时间、最新 `context_token` 和本地发送计数。`data/` 已被 `.gitignore` 忽略，因为数据库包含微信用户标识和会话令牌，不应提交到 Git。

## 2. 从聊天到定时发送

```mermaid
flowchart TD
    User["用户：10分钟后提醒我休息"] --> AI["模型识别提醒意图"]
    AI --> Tool["create_reminder / create_weather_schedule"]
    Tool --> SQL1["INSERT 写入 SQLite"]
    Scheduler["后台调度线程"] --> SQL2["SELECT 查询到期任务"]
    SQL2 --> Window{"24小时内且未满10条？"}
    Window -->|是| Send["iLink sendmessage"]
    Window -->|天气任务| Weather["到点调用和风天气 MCP"]
    Weather --> Send
    Send --> SQL3["UPDATE 为 sent / 下一次时间"]
    Window -->|否| Wait["UPDATE 为 waiting_reactivation"]
    User2["用户再次发消息"] --> Reset["刷新 token、时间和计数"]
    Reset --> Wait
```

定时器不是操作系统突然进入 Python 函数，而是一个后台线程不断执行：

```python
while not stop_event.is_set():
    due = store.claim_due(time.time())
    发送到期任务(due)
    stop_event.wait(1)
```

`stop_event.wait(1)` 表示最多等待一秒；程序退出时可以立即唤醒线程，比固定 `time.sleep(1)` 更容易安全停止。

## 3. 两张表

### 3.1 recipients：发送资格

```sql
CREATE TABLE recipients (
    user_id TEXT PRIMARY KEY,
    context_token TEXT NOT NULL,
    last_inbound_at REAL NOT NULL,
    last_checkin_at REAL,
    outbound_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
```

可以把一张表理解成 Excel 工作表：列定义数据含义，每一行是一条记录。

| 列 | 含义 |
| --- | --- |
| `user_id` | iLink 用户标识，也是主键，不允许重复 |
| `context_token` | 最近一条用户消息携带的会话令牌 |
| `last_inbound_at` | 最近主动消息的 Unix 时间戳 |
| `last_checkin_at` | 最近一次主动问候成功发送的时间；还没问候时为 `NULL` |
| `outbound_count` | 这次激活以后已发送的消息分片数 |
| `updated_at` | 最后更新时间 |

### 3.2 reminders：提醒任务

```sql
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    run_at REAL NOT NULL,
    timezone TEXT NOT NULL,
    repeat_kind TEXT NOT NULL DEFAULT 'once',
    action_kind TEXT NOT NULL DEFAULT 'message',
    action_args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    sent_at REAL,
    last_error TEXT,
    FOREIGN KEY (user_id) REFERENCES recipients(user_id)
);
```

`id` 会自动从1递增；`user_id` 是外键，表示任务属于 `recipients` 中的哪一个用户。

`action_kind` 区分任务行为：`message` 表示发送固定文字，`weather` 表示触发时调用和风天气。`action_args` 使用 JSON 保存动作参数，例如：

```json
{"location": "河南省邓州市", "forecast_days": 3}
```

天气数据不会提前写进数据库。调度器到点后才读取这些参数并调用天气 MCP，所以每日任务发送的是当时的新数据。

任务状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 等待到期 |
| `sending` | 已被调度器领取，正在发送 |
| `waiting_reactivation` | 24小时窗口或下发额度不可用 |
| `sent` | 单次任务发送完成 |
| `cancelled` | 用户取消 |
| `failed` | 网络或程序错误导致发送失败 |

## 4. 最常用的四类 SQL

SQL 关键字习惯大写，但小写也能执行。

### 新增：INSERT

```sql
INSERT INTO reminders (
    user_id, content, run_at, timezone, repeat_kind, status, created_at
) VALUES (?, ?, ?, ?, ?, 'pending', ?);
```

问号是参数占位符。Python 会把数据安全地放进去，不要使用字符串拼接生成 SQL，否则可能产生 SQL 注入或引号错误。

### 查询：SELECT

```sql
SELECT id, content, run_at, status
FROM reminders
WHERE user_id = ? AND status = 'pending'
ORDER BY run_at;
```

可以按这样的顺序阅读：从 `reminders` 表选择四列，只保留指定用户且等待中的任务，再按执行时间排序。

### 修改：UPDATE

```sql
UPDATE reminders
SET status = 'sent', sent_at = ?
WHERE id = ?;
```

`WHERE` 非常重要。省略它会把整张表的任务全部改成 `sent`。

### 删除：DELETE

项目没有真正删除提醒，而是把状态改为 `cancelled`，这样以后还能排查。若确实要清理历史数据，可以执行：

```sql
DELETE FROM reminders
WHERE status IN ('sent', 'cancelled') AND sent_at < ?;
```

## 5. Python 怎样执行 SQL

项目中的核心写法是：

```python
with store._connection() as connection:
    row = connection.execute(
        "SELECT * FROM recipients WHERE user_id = ?",
        (user_id,),
    ).fetchone()
```

第二个参数必须是序列。只有一个值时要写 `(user_id,)`，末尾逗号表示这是单元素元组。

写操作离开上下文时自动提交；发生异常则回滚。连接随后显式关闭，避免 Windows 一直占用数据库文件。

## 6. 为什么需要事务和 sending 状态

调度器领取任务时执行：

```sql
BEGIN IMMEDIATE;

SELECT * FROM reminders
WHERE status = 'pending' AND run_at <= ?;

UPDATE reminders
SET status = 'sending'
WHERE id IN (...);
```

这一组操作处于同一个事务。`BEGIN IMMEDIATE` 会先取得写锁，防止两个调度线程同时领取同一条任务并重复发送。

项目还启用了 WAL：

```sql
PRAGMA journal_mode = WAL;
```

WAL（预写式日志）允许读取和写入更好地并行，适合机器人主线程与提醒线程同时访问数据库。

如果程序恰好在发送过程中崩溃，下次启动会把遗留的 `sending` 任务重新放回 `pending`。这偏向“宁可极少数情况下重复，也不要永久丢失”。

## 7. 微信24小时与10条限制

每次收到主动消息，程序使用 UPSERT：存在该用户就更新，不存在就插入。

```sql
INSERT INTO recipients (...)
VALUES (...)
ON CONFLICT(user_id) DO UPDATE SET
    context_token = excluded.context_token,
    last_inbound_at = excluded.last_inbound_at,
    outbound_count = 0;
```

定时发送前检查：

```text
当前时间 - last_inbound_at < 24小时
outbound_count < 10
```

为了保守遵守微信限制，程序把即时回复和定时发送的实际消息分片都计入 `outbound_count`。无法发送的提醒不会删除，而是进入 `waiting_reactivation`。收到下一条用户消息以后，计数归零、令牌刷新，等待任务重新变成 `pending`。

这只是本地保护。微信服务器仍是最终判断者；如果返回 `ret=-2` 或限流错误，任务会再次等待用户激活。

### 7.1 为什么在23小时主动询问

如果严格等满24小时，微信主动下发窗口可能已经关闭。因此默认阈值是最后一次入站消息后的23小时：

```text
用户发消息
    ↓ 保存 last_inbound_at，发送计数归零
等待约23小时
    ↓ 随机发送一条主动问候
保存 last_checkin_at，发送计数加一
    ↓ 用户回复
更新 last_inbound_at，下一轮重新计时
```

查询条件的关键部分是：

```sql
WHERE last_inbound_at <= ?
  AND (last_checkin_at IS NULL OR last_checkin_at < last_inbound_at)
```

第二行保证“同一次用户激活只问候一次”。用户回复后，新的 `last_inbound_at` 会晚于旧的 `last_checkin_at`，于是下一轮23小时后才重新满足条件。问候同样计入 `outbound_count`，不会绕过10条限制。

问候内容不是模型生成的。程序从 `主动问候语.txt` 读取所有非空、非注释行，再使用：

```python
text = random.choice(checkin_messages)
```

发送成功后，`ReplyEngine.record_assistant_context()` 会把同一句话作为 `assistant` 消息放入该用户的内存上下文。这样用户回答“好的”“我在”时，下一次模型调用能看到此前的主动询问。它与原有聊天历史一样保存在进程内存中，程序重启后不会恢复。

## 8. 模型工具

模型可以调用五个工具：

| 工具 | 作用 |
| --- | --- |
| `get_current_time` | 获取服务器当前时间与配置时区 |
| `create_reminder` | 创建单次或每日提醒 |
| `create_weather_schedule` | 创建到点后自动查询和风天气的单次/每日任务 |
| `list_reminders` | 查看尚未结束的任务 |
| `cancel_reminder` | 按编号取消任务 |

示例：

```text
10分钟后提醒我起来走走
今晚22点提醒我关电脑
每天早上8点把邓州最新天气发给我
查看我的提醒
取消提醒3
```

相对时间由模型传 `delay_minutes`，绝对时间使用 ISO 8601，例如：

```text
2026-08-16T22:00:00+08:00
```

每日任务也可以直接传 `08:00`。定时天气必须使用 `create_weather_schedule`；如果错误地使用普通 `create_reminder`，它只会发送固定文字，无法得到最新天气。

### 8.1 “查看我的任务”为什么能知道数据库内容

模型不能直接读取 SQLite，也不是依靠聊天历史记住任务。一次查询会经历：

```text
用户询问当前任务
→ 模型第一次请求决定调用 list_reminders
→ ReminderTools._list() 查询 SQLite
→ 返回任务 JSON
→ 模型第二次请求读取工具结果
→ 按安洁莉娜信使助理口吻回复
```

工具结果包括 `id`、`content`、`type/type_label`、`run_at/schedule_text`、`repeat` 和 `status`。其中 `presentation_hint` 要求模型保持这些事实准确，同时避免机械朗读字段。因此“查询任务”这一轮通常包含模型工具调用前后的两次模型请求，但任务数据来自 SQLite。

### 8.2 到点提醒为什么不需要模型

到点发送走另一条链路：

```text
后台调度器领取到期任务
→ 普通任务读取 content；天气任务调用和风天气 MCP
→ 从定时提醒开场白.txt 使用 random.choice 选一句
→ Python 拼接开场白与正文
→ iLink 直接发送
```

这里没有模型请求。单条任务直接显示“随机开场白 + 正文”，不再添加固定的“⏰ 定时提醒”和“1.”；同一时刻有多条任务才使用编号。发送成功后，完整消息会以 `assistant` 角色写入进程内上下文，让模型能理解用户随后针对提醒作出的简短回应。

## 9. 在 Ubuntu 查看数据库

机器人运行时不需要安装 SQLite 命令行；如果你想学习和查看，可以安装：

```bash
sudo apt update
sudo apt install sqlite3
```

进入项目后打开数据库：

```bash
cd /myfolder/wechat_astrbot/ilink_catgirl
sqlite3 data/reminders.sqlite3
```

进入 SQLite 后：

```sql
.tables
.schema recipients
.schema reminders
.headers on
.mode column
SELECT id, content, datetime(run_at, 'unixepoch', 'localtime'), status
FROM reminders
ORDER BY run_at;
.quit
```

不要公开展示 `recipients.context_token`，它属于敏感会话数据。

## 10. 配置项

在父目录 `.env` 或 `ilink_catgirl/.env` 中配置，后者优先：

```dotenv
REMINDER_TIMEZONE=Asia/Shanghai
REMINDER_ACTIVE_HOURS=24
REMINDER_OUTBOUND_LIMIT=10
REMINDER_CHECK_INTERVAL_SECONDS=1
CHECKIN_ENABLED=true
CHECKIN_AFTER_HOURS=23
```

`CHECKIN_ENABLED=false` 会彻底关闭主动问候；`CHECKIN_AFTER_HOURS` 是最后一次收到用户消息后等待的小时数。程序会自动把过大的值限制在主动下发窗口结束前，但建议保持 `23`。这些配置可以写在父目录 `catgirl/.env` 或 `ilink_catgirl/.env`，后者优先。修改后重启机器人。

## 11. 测试

提醒测试不会连接微信或调用模型：

```bash
python -m unittest -v test_reminders.py
```

测试覆盖数据库持久化、旧表自动迁移、创建/查询/取消、任务列表展示字段、到期发送、随机提醒开场白、到点消息上下文、到点调用天气、发送计数、24小时窗口、额度耗尽、每日续排、主动问候只发一次和 `ret=-2` 恢复。
