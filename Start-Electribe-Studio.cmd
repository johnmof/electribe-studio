@echo off
cd /d "%~dp0"
if exist "dist\ElectribeStudio\ElectribeStudio.exe" (
  start "" "dist\ElectribeStudio\ElectribeStudio.exe" %*
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m e2sample_manager.app %*
  exit /b
)
echo Bitte zuerst den Windows-Build herunterladen oder tools\build-windows.ps1 ausfuehren.
pause
