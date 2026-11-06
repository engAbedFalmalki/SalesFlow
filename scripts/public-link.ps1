# Share SalesFlow through a public https link (Cloudflare quick tunnel) while this PC is on.
#
#   powershell -ExecutionPolicy Bypass -File scripts\public-link.ps1           # build if needed, then share
#   powershell -ExecutionPolicy Bypass -File scripts\public-link.ps1 -Rebuild  # rebuild the site first
#
# Visitors get a read-only copy: SALESFLOW_ENV=production and READ_ONLY=true, so every
# write endpoint (runs, reprocess, review, test purchases) answers 403. It uses its own
# ports (API 8001, site 3001) and build folder (frontend\.next-public), so the local
# development servers on 8000/3000 keep working. The link changes on every start.
# Press Ctrl+C to stop sharing; the API and site started here stop too.

param([switch]$Rebuild)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) { $cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe" }
if (-not (Test-Path $cloudflared)) { throw "cloudflared is not installed: winget install --id Cloudflare.cloudflared" }

$apiPort = 8001
$webPort = 3001

# Read-only API for visitors (environment variables override the repository .env).
$env:SALESFLOW_ENV = "production"
$env:SALESFLOW_READ_ONLY = "true"
$env:SALESFLOW_EMBEDDED_WORKER = "false"
$env:SALESFLOW_API_HOST = "127.0.0.1"
$env:SALESFLOW_API_PORT = "$apiPort"

# Single-origin site build: the browser calls /api on the public address, Next.js forwards it.
$env:NEXT_PUBLIC_API_BASE_URL = "/"   # "/" = same origin (an empty value would unset the variable)
$env:SALESFLOW_API_PROXY = "http://127.0.0.1:$apiPort"
$env:NEXT_DIST_DIR = ".next-public"
$env:NEXT_TELEMETRY_DISABLED = "1"

$started = @()
try {
    if ($Rebuild -or -not (Test-Path (Join-Path $frontend ".next-public\BUILD_ID"))) {
        Write-Host "Building the public site..." -ForegroundColor Cyan
        & npm --prefix $frontend run build
        if ($LASTEXITCODE -ne 0) { throw "Site build failed." }
    }

    Write-Host "Starting the read-only API on port $apiPort..." -ForegroundColor Cyan
    $started += Start-Process -FilePath $python -ArgumentList (Join-Path $root "backend\serve.py") -WorkingDirectory $root -WindowStyle Hidden -PassThru

    Write-Host "Starting the site on port $webPort..." -ForegroundColor Cyan
    $next = Join-Path $frontend "node_modules\next\dist\bin\next"
    $started += Start-Process -FilePath "node" -ArgumentList "`"$next`" start -p $webPort -H 127.0.0.1" -WorkingDirectory $frontend -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds(60)
    foreach ($url in "http://127.0.0.1:$apiPort/api/health", "http://127.0.0.1:$webPort/") {
        while ($true) {
            try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 5 | Out-Null; break }
            catch { if ((Get-Date) -gt $deadline) { throw "Timed out waiting for $url" }; Start-Sleep -Milliseconds 500 }
        }
    }

    Write-Host ""
    Write-Host "Opening the public link. Look for the https://...trycloudflare.com address below." -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop sharing." -ForegroundColor Green
    Write-Host ""
    & $cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$webPort"
}
finally {
    foreach ($p in $started) {
        if ($p -and -not $p.HasExited) { & taskkill /PID $p.Id /T /F | Out-Null }
    }
    Write-Host "Stopped sharing." -ForegroundColor Yellow
}
