@echo off
rem Stops the autonomous player and dashboard (the game itself keeps running).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -match 'autopilot\.py|dashboard\.py' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F }"
