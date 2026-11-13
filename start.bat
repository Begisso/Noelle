@echo off
rem Noelle HMI - start editor + runtime.   start.bat [project folder] [--kiosk] [--host 0.0.0.0]
cd /d "%~dp0"
if not exist .venv call setup.bat
set PROJ=%~1
if "%PROJ%"=="" set PROJ=projects\Demo
.venv\Scripts\python -m noelle "%PROJ%" %2 %3 %4 %5
