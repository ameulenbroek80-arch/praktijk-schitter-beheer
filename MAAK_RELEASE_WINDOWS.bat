@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" MAAK_RELEASE.py
) else (
  py MAAK_RELEASE.py
)
pause
