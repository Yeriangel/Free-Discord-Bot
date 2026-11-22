@echo off
rem Runs only the bot service, without a window (Ctrl+C to stop)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m botd
