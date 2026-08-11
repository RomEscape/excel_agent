# 로컬 스택 스모크 테스트: Ollama → Sidecar → tool-calling 채팅 경로
# 사용: .\scripts\verify-local-stack.ps1 [-SidecarPort 19532]

param(
    [int]$SidecarPort = 19532
)

$ErrorActionPreference = "Stop"

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
    if (-not $tags.models) { throw "모델 목록 없음 — 'ollama pull qwen3:4b' 실행 필요" }
    $tags.models | ForEach-Object { "  - $($_.name)" }
}) -and $ok

$ok = (Test-Endpoint "Ollama OpenAI 호환 API (/v1/models)" {
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:11434/v1/models" -TimeoutSec 5
    if (-not $models.data) { throw "OpenAI 호환 모델 목록 없음" }
}) -and $ok

$ok = (Test-Endpoint "Sidecar /health" {
    Invoke-RestMethod -Uri "http://127.0.0.1:$SidecarPort/health" -TimeoutSec 5 | Format-List
}) -and $ok

$ok = (Test-Endpoint "Sidecar /llm/chat (Ollama 직행)" {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$SidecarPort/llm/chat" -Method Post `
        -Body '{"message":"Reply with exactly: OK"}' -ContentType "application/json" -TimeoutSec 180
    if (-not $r.response -or $r.response.Trim().Length -eq 0) {
        throw "빈 응답: $($r | ConvertTo-Json -Compress)"
    }
    "response: $($r.response)"
}) -and $ok

$ok = (Test-Endpoint "Sidecar /excel-live/command (tool-calling)" {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$SidecarPort/excel-live/command" -Method Post `
        -Body '{"message":"안녕하세요. 지금은 연결 확인 중이에요."}' -ContentType "application/json" -TimeoutSec 180
    if (-not $r.assistant_text -and -not $r.result) {
        throw "빈 응답: $($r | ConvertTo-Json -Compress)"
    }
    "assistant_text: $($r.assistant_text)"
}) -and $ok

if ($ok) {
    Write-Host "`n=== 전체 통과 — 워크스페이스에서 대화 가능 ===" -ForegroundColor Green
    exit 0
}
Write-Host "`n=== 일부 실패 — start-local-stack.ps1 실행 후 재시도 ===" -ForegroundColor Yellow
exit 1
