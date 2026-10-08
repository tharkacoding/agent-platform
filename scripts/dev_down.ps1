# Stop the AgentOS dev stack started by scripts\dev_up.ps1 (leaves your Chrome window open).
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "data\logs\dev.pids.json"
if (-not (Test-Path $pidFile)) { Write-Host "AgentOS dev stack is not running (no PID file)"; return }
$pids = Get-Content $pidFile -Raw | ConvertFrom-Json
foreach ($name in @("gateway", "runner", "browser", "bridge")) {
    $id = $pids.$name
    if ($id) {
        # also stop children (uvicorn may spawn none, but be safe)
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Write-Host "stopped $name ($id)"
    }
}
Remove-Item $pidFile -Force
