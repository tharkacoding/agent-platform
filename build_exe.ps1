# Builds dist\agentos.exe (one-file console app: CLI + TUI + host bridge).
# Usage: powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install --user --quiet "pyinstaller>=6" typer rich textual httpx fastapi uvicorn pydantic mss pywinauto pyautogui
python -m PyInstaller --noconfirm --clean agentos.spec
if (-not (Test-Path dist\agentos.exe)) { throw "build failed: dist\agentos.exe missing" }
$size = [math]::Round((Get-Item dist\agentos.exe).Length / 1MB, 1)
Write-Host "Built dist\agentos.exe ($size MB)"
& dist\agentos.exe --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "agentos.exe --help failed" }
Write-Host "Smoke test OK. Copy agentos.exe next to docker-compose.yml (or run 'agentos setup' from the repo once)."
