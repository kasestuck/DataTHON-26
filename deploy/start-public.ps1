# Preparation is offline. -Publish is the explicit, separate public-access step.
param(
    [switch]$Publish,
    [ValidateRange(1024, 65535)][int]$Port = 8001
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskCloudflared = Join-Path $taskRoot '.tools\cloudflared.exe'
$taskExpectedHash = '4d2490e74082c5d48dbfedaf92576fa5bd1702379ab88eb7faa0cfbe8dc891b6'
if (-not (Test-Path -LiteralPath $taskCloudflared)) {
    throw 'Install the verified cloudflared binary in .tools first; see deploy/PUBLISHING.md.'
}
$taskHash = [Security.Cryptography.SHA256]::Create()
$taskBinary = [IO.File]::OpenRead($taskCloudflared)
try { $taskActualHash = [BitConverter]::ToString($taskHash.ComputeHash($taskBinary)).Replace('-', '').ToLowerInvariant() }
finally { $taskBinary.Dispose(); $taskHash.Dispose() }
if ($taskActualHash -ne $taskExpectedHash) {
    throw 'cloudflared SHA256 differs from the reviewed release. Do not run it.'
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.venv\Scripts\python.exe'))) {
    throw 'Prepare the Python environment from README.md first.'
}
foreach ($taskModel in @('rtdetr_r18vd', 'qwen3_vl_2b')) {
    $taskModelDir = Join-Path $taskRoot "models\$taskModel"
    if (-not (Test-Path -LiteralPath (Join-Path $taskModelDir 'config.json')) -or
        -not (Get-ChildItem -LiteralPath $taskModelDir -Filter '*.safetensors' -File)) {
        throw "Local model missing: $taskModel. No mock fallback."
    }
}
if (-not $Publish) {
    Write-Output 'Prepared only: no server or tunnel started; no public URL allocated.'
    Write-Output 'After separate approval: powershell -ExecutionPolicy Bypass -File .\deploy\start-public.ps1 -Publish'
    return
}

# Never stop or reuse an unknown local server: it may have another queue on this GPU.
$taskListener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
try { $taskListener.Start() } catch { throw "Port $Port is occupied. Stop your local demo first or choose another port." }
finally { $taskListener.Stop() }
foreach ($taskConfig in @('config.yml', 'config.yaml')) {
    if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE ".cloudflared\$taskConfig")) {
        throw 'An existing cloudflared config was found. Review it before using Quick Tunnel; no files were changed.'
    }
}

$taskState = Join-Path $taskRoot ('var\public-demo\' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskState -Force | Out-Null
$taskTunnel = $null
$taskSite = $null
try {
    $taskTunnelLog = Join-Path $taskState 'cloudflared.log'
    $taskTunnel = Start-Process -FilePath $taskCloudflared -ArgumentList @(
        'tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$Port",
        '--loglevel', 'info', '--logfile', ('"{0}"' -f $taskTunnelLog)
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskState 'tunnel.stdout.log') `
      -RedirectStandardError (Join-Path $taskState 'tunnel.stderr.log')
    $taskUntil = [DateTime]::UtcNow.AddSeconds(90)
    $taskOrigin = ''
    while (-not $taskOrigin) {
        $taskTunnel.Refresh()
        if ($taskTunnel.HasExited) { throw 'cloudflared exited. Inspect the private logs in var/public-demo.' }
        if ([DateTime]::UtcNow -gt $taskUntil) { throw 'No Quick Tunnel URL received within 90 seconds.' }
        foreach ($taskLog in @($taskTunnelLog, (Join-Path $taskState 'tunnel.stderr.log'))) {
            if (Test-Path -LiteralPath $taskLog) {
                $taskText = Get-Content -LiteralPath $taskLog -Raw -ErrorAction SilentlyContinue
                if ($taskText) { $taskOrigin = [regex]::Match($taskText, 'https://[a-z0-9-]+\.trycloudflare\.com').Value }
                if ($taskOrigin) { break }
            }
        }
        if (-not $taskOrigin) { Start-Sleep -Milliseconds 300 }
    }
    # The URL is allocated first while the local port is closed. Start the site
    # only with its actual HTTPS origin so browser cookies are Secure immediately.
    $taskSite = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f (Join-Path $taskRoot 'start.ps1')),
        '-Port', $Port, '-PublicOrigin', $taskOrigin
    ) -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput (Join-Path $taskState 'site.stdout.log') `
      -RedirectStandardError (Join-Path $taskState 'site.stderr.log')
    $taskUntil = [DateTime]::UtcNow.AddSeconds(30)
    $taskReady = $false
    while (-not $taskReady) {
        $taskSite.Refresh()
        if ($taskSite.HasExited) { throw 'Site exited; inspect the private site log.' }
        try { $taskReady = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok' } catch {}
        if ([DateTime]::UtcNow -gt $taskUntil) { throw 'Local site startup timeout.' }
        if (-not $taskReady) { Start-Sleep -Milliseconds 200 }
    }
    Write-Host "Forte Vision HTTPS: $taskOrigin"
    Write-Host 'Keep this window open. Ctrl+C stops only this site and tunnel. The URL changes on restart.'
    while ($true) {
        Start-Sleep -Seconds 1
        $taskSite.Refresh(); $taskTunnel.Refresh()
        if ($taskSite.HasExited -or $taskTunnel.HasExited) { throw 'Site or tunnel stopped; public demo ended.' }
    }
} finally {
    foreach ($taskOwnedProcess in @($taskTunnel, $taskSite)) {
        if ($null -ne $taskOwnedProcess) {
            $taskOwnedProcess.Refresh()
            if (-not $taskOwnedProcess.HasExited) {
                & taskkill.exe /PID $taskOwnedProcess.Id /T /F 2>$null | Out-Null
                $taskOwnedProcess.WaitForExit(10000) | Out-Null
            }
        }
    }
}
