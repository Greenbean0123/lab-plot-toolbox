@echo off
chcp 65001 >nul
rem 双击运行科研绘图工具箱。首次运行会自动创建 Python 环境并安装依赖（需联网，约几分钟）。
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 首次运行：正在创建 Python 环境并安装依赖...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 ( echo 依赖安装失败，请检查网络后重试。 & pause & exit /b 1 )
)
start "" ".venv\Scripts\pythonw.exe" main.py %*
