@echo off
rem Double-click this file to start the hub (local website for browsing the database) and open it in
rem your browser. Safe to run while run_next_season.bat is going. Close this window to stop the hub.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m nbadata hub
echo.
pause
