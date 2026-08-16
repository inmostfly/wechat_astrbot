# 重构更新

## 原wxauto作者已停止更新

作者停止更新后导致库抓不到窗口句柄，组件层次发生变化，发送消息功能损坏，后尝试迁移使用UI Automation来读取和操作微信界面，实现回复功能。

又因微信更新，层级结构频繁变动，且此种方式需要另一个微信账号登陆（服务器登陆有被官方停止登陆其他设备功能的风险）,必须维持在前台，可用性较差，放弃使用，参考UIA文件夹;UIA中含有对应技术路线，供以参考。

## wechat开放第三方接口，可通过openclaw-weixin直接接入

仿astrbot路线实现，更加轻量化，无需额外下载astrbot客户端或者大量依赖，目前点击使用start打包仅适用于windows系统，linux系统无需安装qrcode[pil]>=7.4,<9,安装依赖后直接运行main.py即可使用；助手人格可直接在聊天助手.txt中定义使用

>本次日志：D:\Users\12298\Desktop\catgirl\ilink_catgirl\logs\chat_2026-08-15_14-34-37.log  
本机尚未保存微信机器人登录状态，正在生成二维码……  
备用扫码链接：https://liteapp.weixin.qq.com/q/******************&bot_type=3  
微信机器人登录成功，凭据已保存在 data/session.json。  
微信机器人已上线。控制台输入 quit、exit、stop 或 退出 可安全停止。  
quit  
收到停止命令，正在安全退出……

.env.example中含有配置示例，输入相关api后将本目录重命名为.env即可使用，初期均使用低价api即可<br>
(我使用的是和风天气的api,模型使用的是deepseek-v4-flash)
