@echo off
REM Run the Feishu bridge (installed by install.py). Launches a detached headless session.
set REPO=%USERPROFILE%\.chat_bridge\claude-code-lark-bridge
set PY=%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe
cd /d "%REPO%" 2>nul || (echo Bridge not installed. Run install.bat first. & exit /b 1)
"%PY%" -m mcp_channel.launcher up %*
