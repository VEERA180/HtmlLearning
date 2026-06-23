@echo off
REM ============================================================
REM  run_bot.bat - Task Scheduler entry point
REM ============================================================
REM Task Scheduler does not inherit your normal terminal's working
REM directory or PATH setup reliably, so this script:
REM   1. Forces the working directory to this script's own folder
REM      (where config.py, main.py, credentials.txt all live)
REM   2. Logs a timestamped marker so you can see in
REM      logs\scheduler_runs.log exactly when Task Scheduler fired
REM   3. Launches main.py and lets Python's own logging (to
REM      logs\bot_YYYYMMDD.log) take over from there
REM
REM EDIT THIS LINE if "python" is not on PATH for the Task Scheduler
REM service account - use the FULL path to python.exe instead, e.g.:
REM   "C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe"
REM Find your path by running this in PowerShell:  (Get-Command python).Source

set PYTHON_EXE=python

REM cd to the folder this .bat file lives in, whatever drive/path that is
cd /d "%~dp0"

echo. >> logs\scheduler_runs.log
echo [%date% %time%] Task Scheduler triggered run_bot.bat >> logs\scheduler_runs.log

%PYTHON_EXE% main.py >> logs\scheduler_runs.log 2>&1

echo [%date% %time%] main.py exited with code %errorlevel% >> logs\scheduler_runs.log
