@echo off
REM ===================================================================
REM  Telegram Archive Watcher — launcher
REM
REM  Double-click to run in the foreground (you'll see the log).
REM  This is also the file the auto-start task runs at every login —
REM  see SETUP.md for how to register that.
REM ===================================================================

title Telegram Archive Watcher
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python is not on your PATH.
    echo  Install Python 3.9+ from python.org and tick "Add to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import telethon" >nul 2>&1
if errorlevel 1 (
    echo  Installing Telethon, one moment...
    python -m pip install -q telethon
)

if not exist "%~dp0config.json" (
    echo.
    echo  [ERROR] config.json not found.
    echo  Copy config.example.json to config.json and fill in your
    echo  api_id, api_hash and phone number first. See SETUP.md.
    echo.
    pause
    exit /b 1
)

python "%~dp0telegram_watcher.py"

if errorlevel 1 (
    echo.
    echo  [!] The watcher exited with an error. See watcher.log for details.
    pause
)
