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
    echo 找不到 Python。请创建 UIA\.venv，或设置 CATGIRL_PYTHON 环境变量。
    pause
    exit /b 1
)

echo 正在确认 UIA 版运行与打包依赖...
"%CATGIRL_PYTHON%" -m pip install -r "requirements.txt" -r "requirements-build.txt"
if errorlevel 1 goto :failed

echo 正在打包，请稍候...
"%CATGIRL_PYTHON%" -m PyInstaller --noconfirm --clean "catgirl.spec"
if errorlevel 1 goto :failed

copy /Y "EXE使用说明.txt" "dist\Catgirl微信助手\EXE使用说明.txt" >nul
copy /Y "..\和风天气配置示例.txt" "dist\Catgirl微信助手\和风天气配置示例.txt" >nul
if exist ".env" (
    copy /Y ".env" "dist\Catgirl微信助手\.env" >nul
    echo 已把 UIA\.env 复制到成品目录。
    echo 注意：对外分享程序前请删除其中的 .env，避免泄露 API Key。
) else if exist "..\.env" (
    copy /Y "..\.env" "dist\Catgirl微信助手\.env" >nul
    echo 已把项目根目录的 .env 复制到成品目录。
    echo 注意：对外分享程序前请删除其中的 .env，避免泄露 API Key。
) else (
    echo UIA 和项目根目录均未找到 .env；使用前请把配置文件放到 EXE 旁边。
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
