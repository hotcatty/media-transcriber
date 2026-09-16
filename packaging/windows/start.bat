@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0launcher.ps1" (
  powershell -NoProfile -Command "try { Unblock-File -LiteralPath '%~dp0launcher.ps1' } catch {}" >nul 2>&1
)
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launcher.ps1" %*
exit /b 0
