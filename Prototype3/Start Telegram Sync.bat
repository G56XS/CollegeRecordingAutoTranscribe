@echo off
REM ===================================================================
REM  Start Telegram Sync -- launches telegram_sync.py minimized.
REM  Put this file, telegram_sync.py, config.json and
REM  archive_transcriber.py all in the same folder.
REM
REM  To run automatically every time you log into Windows:
REM    1. Press Win+R, type: shell:startup , press Enter.
REM    2. Copy a SHORTCUT to this .bat file into that folder.
REM       (Right-click this file -> Send to -> Desktop, then drag that
REM       shortcut into the shell:startup folder, or just copy this
REM       .bat file itself into shell:startup -- both work.)
REM ===================================================================

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not on your PATH. Install it from python.org.
    pause
    exit /b 1
)

python -c "import telethon" >nul 2>&1
if errorlevel 1 (
    echo Installing Telethon, one moment...
    python -m pip install -q telethon
)

start "Telegram Sync + Transcriber" /min cmd /k python "%~dp0telegram_sync.py"
