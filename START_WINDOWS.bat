@echo off
setlocal
cd /d "%~dp0"

set "EXPECTED_VERSION=1.3.1"
set "LOOPBACK=127.0.0.1"

if not exist ".venv\Scripts\python.exe" (
  echo Eerste start: virtuele Python-omgeving wordt aangemaakt...
  py -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installatie mislukt.
    pause
    exit /b 1
  )
)

if not exist "storage" mkdir storage

rem Ruim oude Praktijk Schitter Python-processen op die nog op onze lokale poorten luisteren.
rem Alleen python/pythonw + app.py + poort 5050-5099 wordt gestopt; andere programma's blijven met rust.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$all=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.py' };" ^
  "foreach($p in $all){ try { $c=Get-NetTCPConnection -State Listen -OwningProcess $p.ProcessId -ErrorAction Stop; if($c.LocalPort | Where-Object { $_ -ge 5050 -and $_ -le 5099 }){ Write-Host ('Oude Praktijk Schitter-server gestopt: PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } } catch {} }"

for /f %%P in ('powershell -NoProfile -Command "$p=5050; while($p -lt 5100 -and (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue)){ $p++ }; $p"') do set APPPORT=%%P

if "%APPPORT%"=="" (
  echo Geen vrije poort gevonden tussen 5050 en 5099.
  pause
  exit /b 1
)

if not "%APPPORT%"=="5050" (
  echo.
  echo LET OP: poort 5050 is al bezet, waarschijnlijk door een oudere versie.
  echo Deze versie start daarom op poort %APPPORT%.
  echo.
)

set "PSB_PORT=%APPPORT%"
set "PSB_LOCAL_LAUNCH=1"

echo Praktijk Schitter Beheer v%EXPECTED_VERSION% wordt gestart vanuit:
echo %~dp0
echo.

for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$env:PSB_PORT='%APPPORT%'; $env:PSB_LOCAL_LAUNCH='1'; (Start-Process -FilePath '.venv\Scripts\pythonw.exe' -ArgumentList 'app.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden -PassThru).Id"`) do set SERVERPID=%%i

if "%SERVERPID%"=="" (
  echo Kon de server niet starten.
  pause
  exit /b 1
)

> "storage\.server.pid" echo %SERVERPID%
> "storage\.server.port" echo %APPPORT%

rem Geef Flask even tijd. Daarna maximaal 10 seconden proberen.
powershell -NoProfile -Command ^
  "$u='http://%LOOPBACK%:%APPPORT%/api/health'; $ok=$false; 1..10 | ForEach-Object { if(-not $ok){ try { $r=Invoke-RestMethod -Uri $u -TimeoutSec 2; if($r.version -eq '%EXPECTED_VERSION%'){ $ok=$true; Write-Host ('Actief: v'+$r.version+' op poort %APPPORT%') } elseif($r.version){ Write-Host ('VERKEERDE VERSIE ACTIEF: '+$r.version); exit 2 } } catch { Start-Sleep -Seconds 1 } } }; if(-not $ok){ Write-Host 'De nieuwe server reageert niet.'; exit 1 }"

if errorlevel 1 (
  echo.
  echo De versiecontrole is mislukt. De browser wordt NIET geopend.
  echo.
  echo Mogelijke oorzaak: de nieuwe server is niet gestart.
  echo Bekijk eventueel storage\app.log voor de foutmelding.
  echo.
  pause
  exit /b 1
)

start "" http://%LOOPBACK%:%APPPORT%
endlocal
