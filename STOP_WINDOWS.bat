@echo off
setlocal
cd /d "%~dp0"
if not exist "storage\.server.pid" (
  echo Geen server-PID gevonden voor deze map.
  pause
  exit /b 0
)
set /p SERVERPID=<"storage\.server.pid"
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%SERVERPID%' -ErrorAction SilentlyContinue; if($p -and $p.Name -match '^python(w)?\.exe$' -and $p.CommandLine -match 'app\.py'){ Stop-Process -Id %SERVERPID% -Force; Write-Host 'Server gestopt.' } else { Write-Host 'Opgeslagen PID hoort niet meer bij Praktijk Schitter; niets gestopt.' }"
del "storage\.server.pid" >nul 2>&1
del "storage\.server.port" >nul 2>&1
pause
endlocal
