# OpenClaw + Ollama 로컬 스택용 환경 변수 로드 (~/.openclaw/openclaw.json)
# 사용: . .\scripts\local-env.ps1

$ErrorActionPreference = "Stop"
$OpenClawConfig = Join-Path $env:USERPROFILE ".openclaw\openclaw.json"

if (-not (Test-Path $OpenClawConfig)) {
    Write-Warning "OpenClaw 설정 없음: $OpenClawConfig — 먼저 'ollama launch openclaw --yes' 또는 'openclaw onboard' 실행"
    return
}

$cfg = Get-Content $OpenClawConfig -Raw | ConvertFrom-Json
$token = $cfg.gateway.auth.token
if ($token) {
    $env:OPENCLAW_GATEWAY_TOKEN = $token
}
# 로컬 Ollama용 마커 키 (OpenClaw 공식 문서)
$env:OLLAMA_API_KEY = "ollama-local"

Write-Host "OPENCLAW_GATEWAY_TOKEN 설정됨 (길이 $($env:OPENCLAW_GATEWAY_TOKEN.Length))"
Write-Host "OLLAMA_API_KEY=ollama-local"
