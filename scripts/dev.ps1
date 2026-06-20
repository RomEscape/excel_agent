# Development script for Windows: run Python sidecar + Tauri dev
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot

Write-Host "=== Starting Office Claw Development ==="

# Start Python sidecar in background
Write-Host "Starting Python sidecar..."
$envScript = Join-Path $ProjectDir "scripts\local-env.ps1"
if (Test-Path $envScript) { . $envScript }

$sidecarJob = Start-Job -ScriptBlock {
    param($dir, $gwToken)
    Set-Location "$dir/python-sidecar"
    if ($gwToken) { $env:OPENCLAW_GATEWAY_TOKEN = $gwToken }
    $env:OLLAMA_API_KEY = "ollama-local"
    uv run python -m office_claw_sidecar --port 19532
} -ArgumentList $ProjectDir, $env:OPENCLAW_GATEWAY_TOKEN

Write-Host "Sidecar Job ID: $($sidecarJob.Id)"

# Wait for sidecar
Write-Host "Waiting for sidecar..."
for ($i = 0; $i -lt 20; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:19532/health" -TimeoutSec 1 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host "Sidecar is ready!"
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

# Start Tauri dev
Write-Host "Starting Tauri dev server..."
Set-Location $ProjectDir
cargo tauri dev

# Cleanup
Stop-Job -Job $sidecarJob -ErrorAction SilentlyContinue
Remove-Job -Job $sidecarJob -ErrorAction SilentlyContinue
Write-Host "=== Development session ended ==="
