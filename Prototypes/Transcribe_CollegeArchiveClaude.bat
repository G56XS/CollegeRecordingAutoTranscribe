@echo off
REM Double-click this file any time you add new recordings to ANY subject
REM folder inside College Archive. It scans every subject, transcribes
REM whatever's new, and files it away automatically.

python "%~dp0auto_transcribe.py" "C:\Users\Iven\Documents\College Archive"
pause
