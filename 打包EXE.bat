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

"%CATGIRL_PYTHON%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo 正在安装 PyInstaller...
    "%CATGIRL_PYTHON%" -m pip install -r "requirements-build.txt"
    if errorlevel 1 goto :failed
)

echo 正在打包，请稍候...
"%CATGIRL_PYTHON%" -m PyInstaller --noconfirm --clean "catgirl.spec"
if errorlevel 1 goto :failed

copy /Y "EXE使用说明.txt" "dist\Catgirl微信助手\EXE使用说明.txt" >nul
copy /Y "和风天气配置示例.txt" "dist\Catgirl微信助手\和风天气配置示例.txt" >nul
if exist ".env" (
    copy /Y ".env" "dist\Catgirl微信助手\.env" >nul
    echo 已把本机 .env 复制到成品目录。
    echo 注意：对外分享程序前请删除其中的 .env，避免泄露 API Key。
) else (
    echo 未找到 .env；使用前请把配置文件放到 EXE 旁边。
)

echo.
echo 打包完成：dist\Catgirl微信助手\Catgirl微信助手.exe
pause
exit /b 0

:failed
echo.
echo 打包失败，请查看上面的错误。
pause
exit /b 1
