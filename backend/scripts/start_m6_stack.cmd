@echo off
rem ---------------------------------------------------------------------------
rem  One-click wrapper: bypasses PowerShell execution policy and calls
rem  start_m6_stack.ps1 next to this file.
rem
rem  Usage (from anywhere):
rem    start_m6_stack.cmd -Key sk-your-dashscope-key
rem    start_m6_stack.cmd -SkipValidate
rem    start_m6_stack.cmd -Stop
rem ---------------------------------------------------------------------------
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_m6_stack.ps1" %*
set RC=%ERRORLEVEL%
endlocal & exit /b %RC%
