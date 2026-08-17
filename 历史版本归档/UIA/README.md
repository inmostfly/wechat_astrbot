# UIA 归档版

这里保存已经停止继续开发的桌面微信 UI Automation 版本，包括：

- 微信 UIA 适配层与原机器人入口；
- 消息过滤、崩溃处理和置顶功能测试；
- PyInstaller 打包配置、打包脚本和 EXE 使用说明；
- 该阶段的完整技术文档。

此版本仍从项目父目录复用以下共享组件，不在本目录复制：

- `chat_logger.py`
- `weather_mcp_client.py`
- `weather_mcp_server.py`
- `聊天助手.txt`
- `.env`

源码启动：

```powershell
python UIA/my_catgirl.py
```

或者进入本目录后双击 `启动微信助手.bat`。打包时双击 `打包EXE.bat`，成品位于 `UIA/dist/Catgirl微信助手`。

两个批处理优先使用 `UIA/.venv/Scripts/python.exe`，否则使用 PATH 中的 `python`。如需指定已有虚拟环境，可先设置 `CATGIRL_PYTHON` 环境变量。

该方案依赖 Windows 桌面会话、微信窗口和可访问性控件树。微信界面升级后可能失效，因此只作为历史实现和学习资料保留。当前使用的独立机器人方案位于 `../ilink_catgirl`。
