$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht erstellt werden.' }
}
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen.' }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests fehlgeschlagen.' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm packaging/electribe-studio-windows.spec
if ($LASTEXITCODE -ne 0) { throw 'Windows-Build fehlgeschlagen.' }
Compress-Archive -Path dist\ElectribeStudio -DestinationPath dist\ElectribeStudio-Windows-x64.zip -Force
Write-Host 'Fertig: dist\ElectribeStudio\ElectribeStudio.exe'
