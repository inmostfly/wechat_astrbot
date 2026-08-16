# Catgirl iLink 轻量微信机器人

这是一个不依赖 OpenClaw、AstrBot、微信桌面窗口和 UI Automation 的轻量客户端。它直接使用腾讯 `openclaw-weixin` 项目公开的 iLink Bot HTTP 协议完成扫码登录、长轮询收消息和文本回复。

## 已实现

- 微信扫码登录，凭据保存在本机 `data/session.json`
- 长轮询接收用户文本消息
- 携带原始 `context_token` 回复消息
- 只处理 `message_type=1` 的用户消息，忽略机器人自身消息
- 保存同步游标和最近消息指纹，防止重启后重复回复
- 首次连接默认跳过初始积压消息
- 复用父项目的大模型配置、聊天人格、聊天日志和和风天气 MCP
- 支持搜索互联网，以及读取用户给出的公开网页并附带来源网址
- 使用 SQLite 持久保存单次/每日提醒，后台到期后主动发送
- 定时天气任务在触发时调用和风天气 MCP，发送当时的最新实况与预报
- 记录微信24小时会话窗口和下发次数，额度不可用时等待重新激活
- 最后一次聊天约23小时后随机发送一条原创问候，引导用户重新激活会话
- 主动问候不调用模型 API，但会作为助手消息加入本轮模型上下文
- 到点提醒使用可编辑的随机爱丽丝风格开场白，单条任务不再机械编号
- 查询任务时仍以 SQLite 为准，再由模型按“冒险任务日志”口吻组织回答
- 控制台输入 `quit`、`exit`、`stop`、`退出` 或 `停止`，安全下线
- 致命错误写入 `logs/crash_日期_时间.log`，Windows 窗口等待用户确认后关闭

## 快速开始

在本目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以双击 `start.bat`。

Windows 需要生成独立成品时，双击 `打包程序.bat`。脚本会安装 `requirements.txt` 和 `requirements-build.txt`，然后生成：

```text
ilink_catgirl/dist/Catgirl微信机器人/Catgirl微信机器人.exe
```

该成品只包含当前 iLink 轻量版、完整 MCP 包、天气/联网 MCP、日志模块以及必需的文本资源，不包含已经放弃的 `UIA/`、`wxauto/`、`pywinauto` 或 `comtypes`。PyInstaller 不能跨系统打包：Windows 构建得到 EXE；Ubuntu 应直接运行 Python，或在 Ubuntu 上执行同一份 spec 生成 Linux 可执行文件。

程序优先复用父目录 `catgirl/.env`，本目录的 `.env` 只用于覆盖。第一次运行会生成二维码图片 `data/weixin-login.png` 和备用扫码链接；扫码确认后才会开始收发消息。

联网搜索需要在 `.env` 中配置 `TAVILY_API_KEY`，或填写自建的 `SEARXNG_URL`。直接读取公开网址不需要搜索 API Key。配置后重启机器人，可以发送“联网搜索某个内容”或直接发送完整网址。

网页读取默认拒绝内网地址。个人可信环境需要读取内网页面，或 Clash Fake-IP 模式返回 `198.18.0.0/15` 时，可以在父目录 `.env` 或本目录 `.env` 中设置 `WEB_ALLOW_PRIVATE_ADDRESS=true`，然后重启机器人。本目录配置会覆盖父目录；即使开启，程序仍拒绝本机回环、链路本地和已知云元数据地址。

定时提醒不需要额外数据库软件。你可以直接发送“10分钟后提醒我休息”“每天早上8点把邓州最新天气发给我”“查看我的提醒”或“取消提醒3”。普通提醒到点发送保存的文字；天气任务到点后才调用和风天气 MCP，因此每天得到的是最新数据。任务保存在 `data/reminders.sqlite3`，服务器重启后仍在。主动下发受微信限制：最近24小时内必须由用户主动发过消息，并且下发次数不能超过当前额度。

主动问候默认开启：机器人在最后一次收到你的消息约23小时后，从 `主动问候语.txt` 使用 `random.choice` 随机抽取一条发送，同一轮会话只发一次。你回复后重新开始计时。问候不消耗模型 API，但会写入内存中的助手上下文，因此模型能理解你是在回答哪一句。开关和时间位于父目录 `.env` 或本目录 `.env`：`CHECKIN_ENABLED=true`、`CHECKIN_AFTER_HOURS=23`，本目录配置优先。话术只借鉴[天童爱丽丝人物页面](https://zh.moegirl.org.cn/天童爱丽丝)所体现的机器人、RPG、勇者和冒险等高层特征，文件中的具体句子均为原创，可直接增删。

普通提醒和定时天气到点时，不调用模型来润色，而是从 `定时提醒开场白.txt` 随机选择一句，再拼接任务正文或最新天气报告。单条任务不会显示“⏰ 定时提醒 / 1.”；多条任务恰好同时到期时才保留编号。发送成功的完整文字会加入助手上下文，方便继续理解“知道了”“稍后再做”等回复。文件中的可选编号仅方便人工编辑，加载时会自动去掉。

你询问“有哪些每日任务”时则不同：模型先调用 `list_reminders`，Python 查询 `data/reminders.sqlite3`，把任务编号、内容、类型、执行时间、重复方式和状态作为 JSON 返回；模型再根据 `聊天助手.txt` 组织成爱丽丝风格的任务日志。因此它知道任务是因为实时查询了数据库，不是凭聊天记忆猜测。

## 目录

```text
ilink_catgirl/
├─ main.py                 # 启动、崩溃记录和窗口停留
├─ bot.py                  # 模型、天气 MCP、消息循环和安全退出
├─ weixin_ilink.py         # iLink 登录、长轮询、发送和状态持久化
├─ reminders.py            # SQLite 提醒工具、资格计数和后台调度器
├─ 主动问候语.txt          # 每行一条候选问候，启动时读取并随机选择
├─ 定时提醒开场白.txt      # 到点提醒的随机开场白，不调用模型 API
├─ requirements.txt        # 独立的轻量依赖
├─ requirements-build.txt  # 仅打包时需要的 PyInstaller
├─ ilink_catgirl.spec       # 轻量版独立打包清单，不包含 UIA
├─ .env.example            # 可选覆盖配置
├─ start.bat               # Windows 双击启动入口
├─ 打包程序.bat            # 安装依赖并生成轻量版 Windows 成品
└─ 独立机器人轻量客户端技术文档.md
```

完整原理、配置、限制和服务器部署说明见 [独立机器人轻量客户端技术文档.md](独立机器人轻量客户端技术文档.md)。SQLite 入门和提醒实现细节见 [SQLite定时提醒技术文档.md](SQLite定时提醒技术文档.md)。

## 重要边界

- 当前只实现一对一文本收发；图片、语音、文件和视频需要额外的 CDN 加密流程。
- 这是根据腾讯公开项目中的协议说明编写的独立 Python 客户端，不是腾讯官方 Python SDK。
- `data/session.json` 内含机器人令牌，不应上传或分享；本目录 `.gitignore` 已忽略它。
- 网页读取只支持 HTTP/HTTPS 文本页面；默认拒绝内网 IP，开启 `WEB_ALLOW_PRIVATE_ADDRESS` 后可访问可信内网和 Clash Fake-IP，但仍拒绝 localhost、云服务器元数据地址、超大响应和非文本文件。
- 当前不执行网页 JavaScript，也不读取登录后内容；PDF、图片和视频尚未实现正文解析。
- 腾讯可能更新协议。若未来失效，应先对照官方仓库的 `README.zh_CN.md`、`src/auth/login-qr.ts`、`src/api/api.ts` 和 `src/api/types.ts`。
