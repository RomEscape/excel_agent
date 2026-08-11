# Development script for Windows: run Python sidecar + Tauri dev
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$LocalAppDataRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path $env:USERPROFILE "AppData\Local"
} else {
    $env:LOCALAPPDATA
}
$SidecarVenvPath = if (-not [string]::IsNullOrWhiteSpace($env:UV_PROJECT_ENVIRONMENT)) {
    $env:UV_PROJECT_ENVIRONMENT.Trim()
} else {
    Join-Path $LocalAppDataRoot "officeclaw\venvs\python-sidecar"
}
$SidecarVenvParent = Split-Path -Parent $SidecarVenvPath
if ($SidecarVenvParent -and -not (Test-Path $SidecarVenvParent)) {
    New-Item -ItemType Directory -Force -Path $SidecarVenvParent | Out-Null
}

# 콘솔 UTF-8 강제 (한글 경로/로그 깨짐 방지)
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:UV_PROJECT_ENVIRONMENT = $SidecarVenvPath

Write-Host "=== Starting Office Claw Development ==="
Write-Host "Python venv path: $SidecarVenvPath"

# Start Python sidecar in background
Write-Host "Starting Python sidecar..."

$sidecarJob = Start-Job -ScriptBlock {
    param($dir, $venvPath)
    Set-Location "$dir/python-sidecar"
    $env:UV_PROJECT_ENVIRONMENT = $venvPath
    uv run python -m office_claw_sidecar --port 19532
} -ArgumentList $ProjectDir, $SidecarVenvPath

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
