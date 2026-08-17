@echo off
rem UI Automation 归档版启动脚本
chcp 65001 >nul
cd /d "%~dp0"

if defined CATGIRL_PYTHON goto :python_ready
if exist "%~dp0.venv\Scripts\python.exe" set "CATGIRL_PYTHON=%~dp0.venv\Scripts\python.exe"
if defined CATGIRL_PYTHON goto :python_ready
where python >nul 2>nul
if not errorlevel 1 set "CATGIRL_PYTHON=python"

:python_ready
if not defined CATGIRL_PYTHON (
    echo 找不到 Python。请创建 UIA\.venv，或设置 CATGIRL_PYTHON 环境变量。
    pause
    exit /b 1
)

"%CATGIRL_PYTHON%" "%~dp0my_catgirl.py"
if errorlevel 1 (
    echo.
    echo 程序异常退出，请查看上面的错误和 chat_logs 日志。
    pause
)
