# 重构更新

## 原wxauto作者已停止更新

作者停止更新后导致库抓不到窗口句柄，组件层次发生变化，发送消息功能损坏，后尝试迁移使用UI Automation来读取和操作微信界面，实现回复功能。

又因微信更新，层级结构频繁变动，且此种方式需要另一个微信账号登录，必须维持桌面会话，可用性较差，因此已放弃使用。`UIA/` 和 `wxauto/` 只保留为历史学习资料，不参与当前版本运行、安装或打包。

## wechat开放第三方接口，可通过openclaw-weixin直接接入

当前版本位于 `ilink_catgirl/`，直接使用 iLink 协议，更加轻量，无需安装 AstrBot 或 OpenClaw。Windows 双击 `start.bat` 运行，双击 `打包程序.bat` 只打包当前轻量版；Ubuntu 安装 `requirements.txt` 后运行 `main.py`。PyInstaller 只能为当前操作系统构建，Windows 成品不能放到 Linux 直接运行。助手人格可在根目录 `聊天助手.txt` 中定义。

>本次日志：D:\Users\12298\Desktop\catgirl\ilink_catgirl\logs\chat_2026-08-15_14-34-37.log  
本机尚未保存微信机器人登录状态，正在生成二维码……  
备用扫码链接：https://liteapp.weixin.qq.com/q/******************&bot_type=3  
微信机器人登录成功，凭据已保存在 data/session.json。  
微信机器人已上线。控制台输入 quit、exit、stop 或 退出 可安全停止。  
quit  
收到停止命令，正在安全退出……

.env.example中含有配置示例，输入相关api后将本目录重命名为.env即可使用，初期均使用低价api即可<br>
(我使用的是和风天气的api,模型使用的是deepseek-v4-flash)
