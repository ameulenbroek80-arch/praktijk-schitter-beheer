@echo off
setlocal
cd /d "%~dp0"
echo Praktijk Schitter Beheer - lokale servers stoppen
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[IO.Path]::GetFullPath('%~dp0').TrimEnd('\');" ^
  "$all=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match '(^|[ ''\"])(app\.py|.*[\\/]app\.py)([ ''\"]|$)' };" ^
  "$own=@($all | Where-Object { $_.ExecutablePath -like ($root + '*') -or $_.CommandLine -like ('*' + $root + '*') });" ^
  "$psb=@($all | Where-Object { try { $c=Get-NetTCPConnection -State Listen -OwningProcess $_.ProcessId -ErrorAction Stop; $c.LocalPort | Where-Object { $_ -ge 5050 -and $_ -le 5099 } } catch {} });" ^
  "$targets=@($own + $psb | Sort-Object ProcessId -Unique);" ^
  "if($targets.Count -eq 0){ Write-Host 'Geen lokale Praktijk Schitter-server gevonden.'; exit 0 };" ^
  "foreach($p in $targets){ Write-Host ('Stop PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host ('Klaar: ' + $targets.Count + ' server(s) gestopt.')"

del "storage\.server.pid" >nul 2>&1
del "storage\.server.port" >nul 2>&1
echo.
pause
endlocal
