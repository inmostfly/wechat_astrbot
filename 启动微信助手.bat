@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "CATGIRL_PYTHON=D:\12298\software\envs\myenv\python.exe"
if not exist "%CATGIRL_PYTHON%" (
    echo 找不到虚拟环境：%CATGIRL_PYTHON%
    echo 请修改本文件中的 CATGIRL_PYTHON 路径。
    pause
    exit /b 1
)

"%CATGIRL_PYTHON%" "%~dp0my_catgirl.py"
if errorlevel 1 (
    echo.
    echo 程序异常退出，请查看上面的错误和 chat_logs 日志。
    pause
)
