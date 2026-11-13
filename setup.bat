@echo off
rem Noelle HMI - one time setup (creates .venv and installs dependencies)
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install -r requirements.txt
if not exist projects\Demo\project.nll .venv\Scripts\python tools\make_demo.py
echo.
echo Setup done. Start with start.bat
pause
