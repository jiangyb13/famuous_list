@echo off
chcp 65001 >nul
if "%~1"=="" (
  echo Drag the PACKED FOLDER onto this file to restore it.
  pause
  exit /b 1
)
python "%~dp0unpack_folder.py" --src "%~1" --force
echo.
pause