@echo off
setlocal
cd /d "%~dp0"
if not exist "legolization\.venv\Scripts\python.exe" (
  echo Missing legolization environment. See docs\SETUP.md.
  exit /b 1
)
"legolization\.venv\Scripts\python.exe" "pipeline_2d_to_lego.py" %*
endlocal
