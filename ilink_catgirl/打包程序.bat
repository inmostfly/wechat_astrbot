@echo off
chcp 65001 >nul
cd /d "%~dp0"

if defined CATGIRL_PYTHON goto :python_ready
if exist "%~dp0.venv\Scripts\python.exe" set "CATGIRL_PYTHON=%~dp0.venv\Scripts\python.exe"
if defined CATGIRL_PYTHON goto :python_ready
where python >nul 2>nul
if not errorlevel 1 set "CATGIRL_PYTHON=python"

:python_ready
if not defined CATGIRL_PYTHON (
    echo 找不到 Python。请创建 ilink_catgirl\.venv，或设置 CATGIRL_PYTHON 环境变量。
    pause
    exit /b 1
)

echo 正在确认轻量版运行与打包依赖...
"%CATGIRL_PYTHON%" -m pip install -r "requirements.txt" -r "requirements-build.txt"
if errorlevel 1 goto :failed

echo 正在打包，请稍候...
"%CATGIRL_PYTHON%" -m PyInstaller --noconfirm --clean "ilink_catgirl.spec"
if errorlevel 1 goto :failed

set "CATGIRL_OUTPUT=dist\Catgirl微信机器人"
copy /Y "主动问候语.txt" "%CATGIRL_OUTPUT%\主动问候语.txt" >nul
copy /Y "..\聊天助手.txt" "%CATGIRL_OUTPUT%\聊天助手.txt" >nul
copy /Y ".env.example" "%CATGIRL_OUTPUT%\.env.example" >nul
copy /Y "README.md" "%CATGIRL_OUTPUT%\README.md" >nul
copy /Y "SQLite定时提醒技术文档.md" "%CATGIRL_OUTPUT%\SQLite定时提醒技术文档.md" >nul
copy /Y "独立机器人轻量客户端技术文档.md" "%CATGIRL_OUTPUT%\独立机器人轻量客户端技术文档.md" >nul
copy /Y "THIRD_PARTY_NOTICES.md" "%CATGIRL_OUTPUT%\THIRD_PARTY_NOTICES.md" >nul

if exist ".env" (
    copy /Y ".env" "%CATGIRL_OUTPUT%\.env" >nul
    echo 已把 ilink_catgirl\.env 复制到成品目录。
) else if exist "..\.env" (
    copy /Y "..\.env" "%CATGIRL_OUTPUT%\.env" >nul
    echo 已把项目根目录的 .env 复制到成品目录。
) else (
    echo 未找到 .env；使用前请参照成品目录中的 .env.example 创建。
)

echo 注意：.env 和 data\session.json 包含密钥或登录凭据，不要公开分享。
echo.
echo 打包完成：%CATGIRL_OUTPUT%\Catgirl微信机器人.exe
pause
exit /b 0

:failed
echo.
echo 打包失败，请查看上面的错误。
pause
exit /b 1
