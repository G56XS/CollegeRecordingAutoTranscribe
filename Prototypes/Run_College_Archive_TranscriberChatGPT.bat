@echo off
setlocal EnableExtensions
cd /d "C:\Users\Iven\Documents\College Archive"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python was not found in PATH.
    echo Install Python 3.10+ and try again.
    pause
    exit /b 1
)

python -c "import rich" >nul 2>&1
if errorlevel 1 (
    echo Installing the Rich UI package for the polished interface...
    python -m pip install -q rich
    if errorlevel 1 (
        echo.
        echo [WARNING] Rich could not be installed.
        echo The transcriber will still run with the fallback interface.
        timeout /t 2 >nul
    )
)

python "%~dp0college_archive_transcriber.py"
if errorlevel 1 (
    echo.
    echo [!] Transcriber exited with an error. See the message above.
)
endlocal
