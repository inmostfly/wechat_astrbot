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
- 控制台输入 `quit`、`exit`、`stop`、`退出` 或 `停止`，安全下线
- 致命错误写入 `logs/crash_日期_时间.log`，Windows 窗口等待用户确认后关闭

## 快速开始

在本目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以双击 `start.bat`。

程序优先复用父目录 `catgirl/.env`，本目录的 `.env` 只用于覆盖。第一次运行会生成二维码图片 `data/weixin-login.png` 和备用扫码链接；扫码确认后才会开始收发消息。

联网搜索需要在 `.env` 中配置 `TAVILY_API_KEY`，或填写自建的 `SEARXNG_URL`。直接读取公开网址不需要搜索 API Key。配置后重启机器人，可以发送“联网搜索某个内容”或直接发送完整网址。

网页读取默认拒绝内网地址。个人可信环境需要读取内网页面，或 Clash Fake-IP 模式返回 `198.18.0.0/15` 时，可以在父目录 `.env` 或本目录 `.env` 中设置 `WEB_ALLOW_PRIVATE_ADDRESS=true`，然后重启机器人。本目录配置会覆盖父目录；即使开启，程序仍拒绝本机回环、链路本地和已知云元数据地址。

定时提醒不需要额外数据库软件。你可以直接发送“10分钟后提醒我休息”“每天早上8点把邓州最新天气发给我”“查看我的提醒”或“取消提醒3”。普通提醒到点发送保存的文字；天气任务到点后才调用和风天气 MCP，因此每天得到的是最新数据。任务保存在 `data/reminders.sqlite3`，服务器重启后仍在。主动下发受微信限制：最近24小时内必须由用户主动发过消息，并且下发次数不能超过当前额度。

## 目录

```text
ilink_catgirl/
├─ main.py                 # 启动、崩溃记录和窗口停留
├─ bot.py                  # 模型、天气 MCP、消息循环和安全退出
├─ weixin_ilink.py         # iLink 登录、长轮询、发送和状态持久化
├─ reminders.py            # SQLite 提醒工具、资格计数和后台调度器
├─ requirements.txt        # 独立的轻量依赖
├─ .env.example            # 可选覆盖配置
├─ start.bat               # Windows 双击启动入口
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
