@echo off
REM ===================================================================
REM  College Archive Transcriber — double-click launcher
REM
REM  Put this file and archive_transcriber.py in the SAME folder.
REM  By default it transcribes the folder they both live in. To point
REM  it somewhere else, edit the ARCHIVE line below.
REM ===================================================================

title College Archive Transcriber
cd /d "%~dp0"

set "ARCHIVE=%~dp0"
REM set "ARCHIVE=C:\Users\Iven\Documents\College Archive"

REM %~dp0 always ends with a trailing backslash. Left in place, the closing
REM quote below gets escaped by that backslash instead of ending the string,
REM so Python receives the path with a stray " stuck on the end. Strip it.
if "%ARCHIVE:~-1%"=="\" set "ARCHIVE=%ARCHIVE:~0,-1%"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python is not on your PATH.
    echo  Install Python 3.9+ from python.org and tick "Add to PATH".
    echo.
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] ffmpeg is not on your PATH.
    echo  Whisper needs it to read audio. Get it from ffmpeg.org,
    echo  then add its \bin folder to PATH and reopen this window.
    echo.
    pause
    exit /b 1
)

python -c "import rich" >nul 2>&1
if errorlevel 1 (
    echo  Installing the Rich interface package, one moment...
    python -m pip install -q rich
)

python -c "import whisper" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Whisper is not installed. Run this once:
    echo      pip install -U openai-whisper
    echo  and install a CUDA build of PyTorch from pytorch.org
    echo  if you want GPU speed.
    echo.
    pause
    exit /b 1
)

python "%~dp0archive_transcriber.py" "%ARCHIVE%"

if errorlevel 1 (
    echo.
    echo  [!] The transcriber exited with an error. See above.
    pause
)