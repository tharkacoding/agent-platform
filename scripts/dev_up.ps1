# AgentOS dev mode (no Docker): host bridge (Chrome only) + local browser service + gateway, as hidden
# background processes. Logs: data\logs\*.log   PIDs: data\logs\dev.pids.json   Stop: scripts\dev_down.ps1
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev_up.ps1            # browser agents use YOUR Chrome
#   powershell -ExecutionPolicy Bypass -File scripts\dev_up.ps1 -Sandbox   # browser agents use a separate Chromium
#
# Your Chrome runs on a dedicated AgentOS profile (%APPDATA%\AgentOS\chrome-profiles\default). Chrome's normal
# profile is refused on purpose. Sign in to your sites once in that window and the logins persist for later runs.
param([switch]$Sandbox, [int]$Port = 7860)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logs = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$pidFile = Join-Path $logs "dev.pids.json"
$python = (Get-Command python).Source

if (Test-Path $pidFile) {
    Write-Host "AgentOS dev stack seems to be running already - stopping it first"
    & (Join-Path $PSScriptRoot "dev_down.ps1")
}

function Stop-PortOwner([int]$p) {
    Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
foreach ($p in @($Port, 8001, 8002, 8765)) { Stop-PortOwner $p }

# host workspace for the runner (agents see /workspace, /shared, /tmp; NO sandbox in dev mode — see PROJECT_STATE §7)
$aos = Join-Path $env:USERPROFILE "AgentOS"
$hostWs = Join-Path $aos "workspace"; $hostShared = Join-Path $aos "shared"; $hostTmp = Join-Path $aos "tmp"
foreach ($d in @($hostWs, $hostShared, $hostTmp)) { New-Item -ItemType Directory -Force $d | Out-Null }

# bridge token + gateway settings (python writes UTF-8 JSON)
$mode = if ($Sandbox) { "sandbox" } else { "host" }
$env:PYTHONIOENCODING = "utf-8"
& $python -c "from bridge.security import load_or_create_token; from aap import settings; settings.update({'bridge_token': load_or_create_token(), 'browser_default_mode': '$mode'}); print('settings ok')"

function Start-Hidden([string]$name, [string[]]$pyArgs, [hashtable]$envs) {
    foreach ($k in $envs.Keys) { Set-Item -Path "env:$k" -Value $envs[$k] }
    # Start-Process joins arguments with spaces and does not quote them: quote anything containing spaces
    $argLine = ($pyArgs | ForEach-Object { if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' '
    $p = Start-Process -FilePath $python -ArgumentList $argLine -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$name.out.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
    foreach ($k in $envs.Keys) { Remove-Item -Path "env:$k" -ErrorAction SilentlyContinue }
    return $p
}

$pids = [ordered]@{}
# bridge: Chrome control + desktop (screenshots are read-only; every desktop click/type still needs approval)
$pids.bridge = (Start-Hidden "bridge" @("-c", "from bridge.server import serve; serve(chrome=True, desktop=True, gateway_url='http://127.0.0.1:$Port')") @{
    BRIDGE_CDP_PUBLIC = "local" }).Id
$pids.runner = (Start-Hidden "runner" @("-m", "uvicorn", "runner.app:app", "--host", "127.0.0.1", "--port", "8001") @{
    # /laptop = your home folder, READ-ONLY for agents (the gateway refuses writes; Settings → laptop_access off|read)
    RUNNER_ROOTS = "$hostWs;$hostShared;$hostTmp;$env:USERPROFILE"
    RUNNER_PATH_MAP = "/workspace=$hostWs;/shared=$hostShared;/tmp=$hostTmp;/laptop=$env:USERPROFILE" }).Id
$pids.browser = (Start-Hidden "browser" @("-m", "uvicorn", "browser_svc.app:app", "--host", "127.0.0.1", "--port", "8002") @{
    PROFILES_DIR = (Join-Path $root "data\browser_profiles"); BROWSER_HEADLESS = "true" }).Id
$pids.gateway = (Start-Hidden "gateway" @("-m", "uvicorn", "aap.api.app:app", "--host", "127.0.0.1", "--port", "$Port") @{
    AAP_BROWSER_URL = "http://127.0.0.1:8002"; AAP_BRIDGE_URL = "http://127.0.0.1:8765"; AAP_BRIDGE_CDP_PUBLIC = "local"
    AAP_RUNNER_URL = "http://127.0.0.1:8001" }).Id
$pids | ConvertTo-Json | Out-File -FilePath $pidFile -Encoding ascii

function Wait-Url([string]$url, [string]$name) {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null; Write-Host "  [ok] $name"; return } catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode.value__ -eq 401) { Write-Host "  [ok] $name"; return } }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  [!!] $name did not answer - see data\logs\$name.err.log"
}
Write-Host "Starting AgentOS dev stack (browser default: $mode)"
Wait-Url "http://127.0.0.1:8765/health" "bridge"
Wait-Url "http://127.0.0.1:8001/health" "runner"
Wait-Url "http://127.0.0.1:8002/health" "browser"
Wait-Url "http://127.0.0.1:$Port/api/health" "gateway"
Write-Host "AgentOS: http://127.0.0.1:$Port   (stop: scripts\dev_down.ps1)"
