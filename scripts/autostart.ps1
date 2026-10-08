# AgentOS autostart (MISTAKES.txt M10): runs at Windows log-on via the "AgentOS Autostart" scheduled task.
# Starts OmniRoute (if not already listening on :20128), then the AgentOS stack (gateway incl. phone messenger,
# runner, browser service, host bridge) via dev_up.ps1 — only if the gateway isn't already up.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root "data\logs\autostart.log"
New-Item -ItemType Directory -Force (Split-Path $Log) | Out-Null
function Say($m) { "$(Get-Date -Format s)  $m" | Out-File -FilePath $Log -Append -Encoding utf8 }

function Test-Port($port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $c.Close(); return $true } catch { return $false }
}

Say "autostart begin"
Start-Sleep -Seconds 20          # let the desktop, network and Docker/WSL settle after log-on

# 1) OmniRoute (the model router AgentOS talks to)
if (-not (Test-Port 20128)) {
    $omni = Join-Path $env:APPDATA "npm\omniroute.ps1"
    if (Test-Path $omni) {
        Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$omni`"" `
            -RedirectStandardOutput (Join-Path $Root "data\logs\omniroute.out.log") -RedirectStandardError (Join-Path $Root "data\logs\omniroute.err.log")
        Say "started OmniRoute"
        for ($i = 0; $i -lt 60 -and -not (Test-Port 20128); $i++) { Start-Sleep -Seconds 2 }
        Say ("OmniRoute listening: " + (Test-Port 20128))
    } else { Say "OmniRoute launcher not found at $omni" }
} else { Say "OmniRoute already running" }

# 2) AgentOS stack
if (-not (Test-Port 7860)) {
    & (Join-Path $PSScriptRoot "dev_up.ps1") *>> $Log
    Say ("AgentOS gateway up: " + (Test-Port 7860))
} else { Say "AgentOS already running" }

# 3) Test VM (WSL2 "agentos-vm", 6 GB): its own hidden Chrome for logged-in web work without the owner's screen (M7)
if (-not (Test-Port 7870)) {
    $p = Join-Path $PSScriptRoot "vm_start.sh"
    $vmStart = "/mnt/" + $p.Substring(0, 1).ToLower() + $p.Substring(2).Replace('\', '/')   # C:\x\y → /mnt/c/x/y
    Start-Process wsl.exe -WindowStyle Hidden -ArgumentList "-d", "agentos-vm", "-u", "root", "--", "bash", $vmStart
    for ($i = 0; $i -lt 90 -and -not (Test-Port 7870); $i++) { Start-Sleep -Seconds 2 }
    Say ("AgentOS VM up: " + (Test-Port 7870) + "  (screen: http://localhost:6081/vnc.html)")
} else { Say "AgentOS VM already running" }
Say "autostart end"
