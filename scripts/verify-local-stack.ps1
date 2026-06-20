# 로컬 스택 스모크 테스트: Ollama → OpenClaw Gateway → Sidecar /agent/chat
# 사용: .\scripts\verify-local-stack.ps1 [-SidecarPort 19532]

param(
    [int]$SidecarPort = 19532
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $ProjectRoot "scripts\local-env.ps1")

function Test-Endpoint($Name, $Script) {
    Write-Host "`n== $Name =="
    try {
        & $Script
        Write-Host "OK: $Name"
        return $true
    } catch {
        Write-Host "FAIL: $Name — $($_.Exception.Message)"
        return $false
    }
}

$ok = $true
$ok = (Test-Endpoint "Ollama API" {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    if (-not $tags.models) { throw "모델 목록 없음" }
    $tags.models | ForEach-Object { "  - $($_.name)" }
}) -and $ok

$ok = (Test-Endpoint "OpenClaw Gateway" {
    openclaw gateway health | Out-Host
}) -and $ok

$ok = (Test-Endpoint "Sidecar /health" {
    Invoke-RestMethod -Uri "http://127.0.0.1:$SidecarPort/health" -TimeoutSec 5 | Format-List
}) -and $ok

$ok = (Test-Endpoint "Sidecar /agent/chat" {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$SidecarPort/agent/chat" -Method Post `
        -Body '{"message":"Reply with exactly: OK"}' -ContentType "application/json" -TimeoutSec 180
    if (-not $r.response -or $r.response.Trim().Length -eq 0) {
        throw "빈 응답: $($r | ConvertTo-Json -Compress)"
    }
    "response: $($r.response)"
    "session_id: $($r.session_id)"
}) -and $ok

if ($ok) {
    Write-Host "`n=== 전체 통과 — 워크스페이스에서 대화 가능 ===" -ForegroundColor Green
    exit 0
}
Write-Host "`n=== 일부 실패 — start-local-stack.ps1 실행 후 재시도 ===" -ForegroundColor Yellow
exit 1
