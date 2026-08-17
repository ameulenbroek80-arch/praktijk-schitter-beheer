@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Start de applicatie eerst eenmaal zodat de Python-omgeving wordt aangemaakt.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" SELFTEST.py
echo.
pause
