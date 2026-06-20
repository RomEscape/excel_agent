# 로컬 AI 스택 기동: OpenClaw 게이트웨이(예약 작업) + Python sidecar
# 사용: .\scripts\start-local-stack.ps1
# 검증: .\scripts\verify-local-stack.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\local-env.ps1")

# 콘솔 UTF-8 강제 (한글 경로/로그 깨짐 방지)
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "=== Office-Claw 로컬 스택 시작 ==="

# 1) OpenClaw 게이트웨이 (Windows 예약 작업 — ollama launch openclaw 가 등록함)
$taskName = "OpenClaw Gateway"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "OpenClaw 예약 작업 실행: $taskName"
    schtasks /Run /TN $taskName | Out-Null
} else {
    Write-Host "예약 작업 없음 — openclaw gateway 직접 시작"
    Start-Process -FilePath "openclaw" -ArgumentList "gateway","--port","18789" -WindowStyle Hidden
}

Start-Sleep -Seconds 8
$env:OPENCLAW_GATEWAY_TOKEN = $env:OPENCLAW_GATEWAY_TOKEN
$env:OLLAMA_API_KEY = "ollama-local"
openclaw gateway health
if ($LASTEXITCODE -ne 0) {
    Write-Warning "게이트웨이 헬스 실패. 'ollama launch openclaw --model ax4-light:latest --yes' 권장"
}

# 2) Sidecar (이미 19532 사용 중이면 스킵)
$sidecarPort = 19532
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$sidecarPort/health" -TimeoutSec 2 | Out-Null
    Write-Host "Sidecar 이미 실행 중 (포트 $sidecarPort)"
} catch {
    Write-Host "Python sidecar 시작 (포트 $sidecarPort)..."
    $sidecarDir = Join-Path $ProjectRoot "python-sidecar"
    $gw = $env:OPENCLAW_GATEWAY_TOKEN
    $sidecarJob = Start-Job -ScriptBlock {
        param($dir, $token)
        Set-Location $dir
        if ($token) { $env:OPENCLAW_GATEWAY_TOKEN = $token }
        $env:OLLAMA_API_KEY = "ollama-local"
        uv run python -m office_claw_sidecar --port 19532
    } -ArgumentList $sidecarDir, $gw
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
        throw "Sidecar 시작 실패. 수동 실행: cd python-sidecar; uv run python -m office_claw_sidecar --port 19532"
    }
}

Write-Host "`n다음: npm run tauri:dev (별 터미널) + .\scripts\verify-local-stack.ps1"
Write-Host "Gemma 4 설치(선택): ollama pull gemma4:e4b"
