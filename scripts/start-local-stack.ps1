# 로컬 AI 스택 기동: Ollama + Python sidecar
# 사용: .\scripts\start-local-stack.ps1
# 검증: .\scripts\verify-local-stack.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
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

Write-Host "=== Office-Claw 로컬 스택 시작 (Ollama + Sidecar) ==="
Write-Host "Python venv path: $SidecarVenvPath"

# 1) Ollama (11434 미응답 시 백그라운드 기동)
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
    Write-Host "Ollama 이미 실행 중 (포트 11434)"
} catch {
    Write-Host "Ollama 시작..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    $ollamaReady = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
            $ollamaReady = $true
            break
        } catch { }
    }
    if (-not $ollamaReady) {
        Write-Warning "Ollama가 10초 내 올라오지 않았습니다. 'ollama serve'를 수동 실행해 주세요."
    }
}

# 2) Sidecar (이미 19532 사용 중이면 스킵)
$sidecarPort = 19532
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$sidecarPort/health" -TimeoutSec 2 | Out-Null
    Write-Host "Sidecar 이미 실행 중 (포트 $sidecarPort)"
} catch {
    Write-Host "Python sidecar 시작 (포트 $sidecarPort)..."
    $sidecarDir = Join-Path $ProjectRoot "python-sidecar"
    $sidecarJob = Start-Job -ScriptBlock {
        param($dir, $venvPath)
        Set-Location $dir
        $env:UV_PROJECT_ENVIRONMENT = $venvPath
        uv run python -m office_claw_sidecar --port 19532
    } -ArgumentList $sidecarDir, $SidecarVenvPath
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$sidecarPort/health" -TimeoutSec 2 | Out-Null
            Write-Host "Sidecar 준비 완료"
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) {
        Write-Warning "Sidecar가 15초 내 올라오지 않았습니다. 백그라운드 로그를 확인합니다."
        try {
            $jobOutput = Receive-Job -Id $sidecarJob.Id -Keep -ErrorAction SilentlyContinue
            if ($jobOutput) {
                Write-Host "---- Sidecar Job Output ----"
                $jobOutput | Out-Host
                Write-Host "----------------------------"
            }
        } catch { }
        throw "Sidecar 시작 실패. 수동 실행: `$env:UV_PROJECT_ENVIRONMENT='$SidecarVenvPath'; cd services/sidecar; uv run python -m office_claw_sidecar --port 19532"
    }
}

Write-Host "`n다음: npm run tauri:dev (별 터미널) + .\scripts\verify-local-stack.ps1"
Write-Host "모델 설치(최초 1회): ollama pull qwen3:4b"
