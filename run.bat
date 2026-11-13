@echo off
cd /d "%~dp0"
set NODE_OPTIONS=--use-system-ca
if not exist ".venv\Scripts\python.exe" (
  echo Setting up the local browser automation runtime. This may take a few minutes the first time.
  python -m venv .venv
  if errorlevel 1 goto setup_failed
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto setup_failed
  .venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 goto setup_failed
)
.venv\Scripts\python.exe server.py
pause
goto :eof

:setup_failed
echo Setup failed. Check that Python and internet access are available, then run run.bat again.
pause
