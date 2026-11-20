@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 cli_interview.py --prompt-deepseek-key %*
) else (
    python -X utf8 cli_interview.py --prompt-deepseek-key %*
)
set "READIALOG_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %READIALOG_EXIT%
