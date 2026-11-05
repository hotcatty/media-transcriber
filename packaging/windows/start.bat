@echo off
setlocal
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "MT_LAUNCHER=%~dp0launcher.ps1"
cd /d "%~dp0"
if not exist "%PS%" goto fail
if not exist "%MT_LAUNCHER%" goto fail
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "try { Unblock-File -LiteralPath $env:MT_LAUNCHER } catch {}" >nul 2>&1
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%MT_LAUNCHER%" %*
if not errorlevel 1 exit /b 0
:fail
echo.
if not exist "%PS%" echo powershell.exe not found
if not exist "%MT_LAUNCHER%" echo launcher.ps1 not found
if exist "%PS%" "%PS%" -NoProfile -ExecutionPolicy Bypass -Command "Write-Host ([string]([char]0x542F+[char]0x52A8+[char]0x6CA1+[char]0x6709+[char]0x5B8C+[char]0x6210+[char]0x3002))"
echo.
pause
exit /b 1
