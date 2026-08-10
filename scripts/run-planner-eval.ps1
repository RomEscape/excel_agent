# 플래너 회귀 평가 — v3(기준선) vs 새 후보 모델을 같은 154건으로 재고 승격 여부를 판정한다.
#
#   .\scripts\run-planner-eval.ps1 -Candidate ax7bplanner-v5r:latest
#
# 전제: Ollama가 떠 있고 두 모델이 모두 등록돼 있어야 한다.
#       학습 중에는 돌리지 말 것 — Ollama가 VRAM을 가져가 학습이 CPU로 밀린다.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [string]$Baseline = "ax7bplanner-v3:latest",
    [string]$Tag = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sidecar = Join-Path $root "python-sidecar"
$python = Join-Path $sidecar ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not $Tag) { $Tag = ($Candidate -replace "[:.]", "-") }
$evalSet = Join-Path $root "datasets\eval\planner_eval_v1.jsonl"
$shadowOut = Join-Path $root "logs\eval_shadow_$Tag.json"
$gateOut = Join-Path $root "logs\eval_gate_$Tag.json"
$thresholds = Join-Path $sidecar "config\planner_gate_thresholds.json"

Push-Location $sidecar
try {
    if (-not $SkipBuild) {
        Write-Host "[1/3] 평가셋 생성" -ForegroundColor Cyan
        & $python scripts\build_planner_eval_set.py --output $evalSet
        if ($LASTEXITCODE -ne 0) { throw "평가셋 생성 실패" }
    }

    Write-Host "[2/3] 그림자 평가 ($Baseline vs $Candidate)" -ForegroundColor Cyan
    Write-Host "      154건 x 2모델 — 20분 안팎 걸립니다."
    & $python scripts\eval_ax7b_shadow.py `
        --input-jsonl $evalSet `
        --output-json $shadowOut `
        --baseline-model $Baseline `
        --candidate-model $Candidate
    if ($LASTEXITCODE -ne 0) { throw "그림자 평가 실패" }

    Write-Host "[3/3] 승격 게이트" -ForegroundColor Cyan
    & $python scripts\eval_release_gate.py `
        --shadow-report $shadowOut `
        --output-json $gateOut `
        --thresholds-json $thresholds
    if ($LASTEXITCODE -ne 0) { throw "게이트 실행 실패" }

    $verdict = (Get-Content $gateOut -Raw | ConvertFrom-Json).passed
    if ($verdict) {
        Write-Host "`n결과: 승격 가능 — $Candidate" -ForegroundColor Green
    }
    else {
        Write-Host "`n결과: 승격 불가 — v3를 유지하세요" -ForegroundColor Red
        Write-Host "실패 항목은 $gateOut 참조"
    }
}
finally {
    Pop-Location
}
