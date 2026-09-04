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

$TauriDir = Join-Path $ProjectDir "apps\desktop\src-tauri"
# externalBin 자리채움 — tauri.conf.json이 이 파일의 **존재**를 요구한다(dev도 마찬가지).
# .gitignore 대상이라 클론 직후엔 없다. dev 모드 사이드카는 venv 소스로 뜨므로 빈 파일로 충분하다.
$BinDir = Join-Path $TauriDir "binaries"
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Force -Path $BinDir | Out-Null }
$Placeholder = Join-Path $BinDir "office-claw-sidecar-x86_64-pc-windows-msvc.exe"
if (-not (Test-Path $Placeholder)) { New-Item -ItemType File -Path $Placeholder | Out-Null }

Write-Host "=== Starting Office Claw Development ==="
Write-Host "Python venv path: $SidecarVenvPath"

# Start Python sidecar in background
Write-Host "Starting Python sidecar..."

$sidecarJob = Start-Job -ScriptBlock {
    param($dir, $venvPath)
    Set-Location "$dir/services/sidecar"   # 모노레포 이행: python-sidecar → services/sidecar
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

# Start Tauri dev — 모노레포에서 Tauri 프로젝트는 apps/desktop 아래다.
Write-Host "Starting Tauri dev server..."
Set-Location (Join-Path $ProjectDir "apps\desktop")
npm run tauri:dev

# Cleanup
Stop-Job -Job $sidecarJob -ErrorAction SilentlyContinue
Remove-Job -Job $sidecarJob -ErrorAction SilentlyContinue
Write-Host "=== Development session ended ==="
