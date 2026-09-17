@echo off
title College Archive Automatic Transcriber
cd /d "%~dp0"
python transcribe_archive.py
pause