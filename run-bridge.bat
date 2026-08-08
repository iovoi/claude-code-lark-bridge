@echo off
REM Run the Feishu bridge (installed by install.py). Starts a detached background process
REM (no PTY, no tmux) via the `feishu-bridge` CLI.
set REPO=%USERPROFILE%\.chat_bridge\claude-code-lark-bridge
set BIN=%USERPROFILE%\.chat_bridge\venv\Scripts\feishu-bridge.exe
cd /d "%REPO%" 2>nul || (echo Bridge not installed. Run install.bat first. & exit /b 1)
"%BIN%" up %*
