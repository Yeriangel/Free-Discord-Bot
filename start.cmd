@echo off
rem Opens the manager window (starts the bot service if needed)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (echo Run setup.cmd first & pause & exit /b 1)
start "" ".venv\Scripts\pythonw.exe" app.py
