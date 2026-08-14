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

## 目录

```text
ilink_catgirl/
├─ main.py                 # 启动、崩溃记录和窗口停留
├─ bot.py                  # 模型、天气 MCP、消息循环和安全退出
├─ weixin_ilink.py         # iLink 登录、长轮询、发送和状态持久化
├─ requirements.txt        # 独立的轻量依赖
├─ .env.example            # 可选覆盖配置
├─ start.bat               # Windows 双击启动入口
└─ 独立机器人轻量客户端技术文档.md
```

完整原理、配置、限制和服务器部署说明见 [独立机器人轻量客户端技术文档.md](独立机器人轻量客户端技术文档.md)。

## 重要边界

- 当前只实现一对一文本收发；图片、语音、文件和视频需要额外的 CDN 加密流程。
- 这是根据腾讯公开项目中的协议说明编写的独立 Python 客户端，不是腾讯官方 Python SDK。
- `data/session.json` 内含机器人令牌，不应上传或分享；本目录 `.gitignore` 已忽略它。
- 腾讯可能更新协议。若未来失效，应先对照官方仓库的 `README.zh_CN.md`、`src/auth/login-qr.ts`、`src/api/api.ts` 和 `src/api/types.ts`。

