@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-InfoMancer.ps1"
set "IM_EXIT=%ERRORLEVEL%"
echo.
if not "%IM_EXIT%"=="0" (
  echo InfoMancer Server setup exited with an error.
)
pause
exit /b %IM_EXIT%
