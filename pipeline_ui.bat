@echo off
setlocal
cd /d "%~dp0"
title 2D to 3D to LEGO - Local UI
if not exist ".venv-hunyuan\Scripts\python.exe" (
  echo Missing .venv-hunyuan. See docs\SETUP.md for setup instructions.
  pause
  exit /b 1
)
".venv-hunyuan\Scripts\python.exe" "pipeline_ui.py" %*
endlocal
