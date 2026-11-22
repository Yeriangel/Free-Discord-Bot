@echo off
rem First-time setup: creates .venv and installs packages
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
rem Start menu + desktop shortcut "Free Discord Bot" with the app logo
".venv\Scripts\python.exe" -m botd.shortcut
echo.
echo Setup done. Open "Free Discord Bot" from the desktop or Start menu (or run start.cmd)
pause
