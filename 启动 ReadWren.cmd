@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" cli_interview.py %*
) else (
    python cli_interview.py %*
)
set "READWREN_EXIT=%ERRORLEVEL%"
if not "%READWREN_EXIT%"=="0" pause
exit /b %READWREN_EXIT%
