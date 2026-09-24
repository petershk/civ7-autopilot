@echo off
title Civ VII Autopilot
rem Autonomous Civ VII player. Continues the current game (add --new to start a fresh one).
cd /d "%~dp0"
rem live dashboard in the background + open it in the browser
start "Civ VII dashboard" /min python dashboard.py
timeout /t 2 /nobreak >nul
start "" http://localhost:8777
echo Civ VII autopilot running - dashboard at http://localhost:8777 - close this window (or run stop_autopilot.bat) to stop.
python -u autopilot.py %*
pause
