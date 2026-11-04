@echo off
chcp 65001 >nul
if "%~1"=="" (
  echo Drag a FOLDER onto this file to pack it.
  pause
  exit /b 1
)
python "%~dp0pack_folder.py" --src "%~1" --force
echo.
pause