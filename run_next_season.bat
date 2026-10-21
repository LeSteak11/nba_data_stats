@echo off
rem Double-click this file to pull the next season that isn't downloaded yet (newest first),
rem rebuild the database and run the checks. Keeps the computer awake while it runs.
rem Safe to stop with Ctrl+C or by closing the window: double-click again to continue.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m nbadata next
echo.
pause
