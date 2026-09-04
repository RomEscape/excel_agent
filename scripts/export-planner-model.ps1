# 플래너 모델(ax7bplanner-v3)을 Ollama 저장소에서 GGUF로 꺼낸다 — Hugging Face 배포용.
#
# 왜 필요한가:
#   플래너는 로컬에서 SFT한 모델이라 어느 공개 레지스트리에도 없다. 저장소에는
#   Modelfile만 있고 가중치(`artifacts/*.gguf`)는 git으로 배포할 수 없어서,
#   클론한 사람은 계획 수립 모델을 얻을 방법이 없었다(2026-09-05 실측).
#   Ollama는 모델을 blob으로 보관하고 그 실물이 그대로 GGUF라, 꺼내서 HF에
#   올리면 `ollama pull hf.co/<repo>` 한 줄로 배포가 끝난다.
#
# 사용:
#   powershell scripts\export-planner-model.ps1
#   powershell scripts\export-planner-model.ps1 -Model "ax7bplanner-v5r:latest"

param(
    [string]$Model = "ax7bplanner-v3:latest",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectDir = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $ProjectDir "artifacts" }

$name, $tag = $Model.Split(":", 2)
if ([string]::IsNullOrWhiteSpace($tag)) { $tag = "latest" }

$OllamaRoot = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE ".ollama\models" }
$ManifestPath = Join-Path $OllamaRoot "manifests\registry.ollama.ai\library\$name\$tag"

if (-not (Test-Path $ManifestPath)) {
    Write-Error @"
매니페스트를 찾지 못했습니다: $ManifestPath
이 컴퓨터의 Ollama에 '$Model'이 없습니다. `ollama list`로 확인해 주세요.
"@
}

# 매니페스트의 레이어 중 실제 가중치는 mediaType이 ...image.model인 것 하나다.
$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$layer = $manifest.layers | Where-Object { $_.mediaType -like "*.image.model" } | Select-Object -First 1
if (-not $layer) { Write-Error "매니페스트에 model 레이어가 없습니다: $ManifestPath" }

$digest = $layer.digest -replace ":", "-"
$BlobPath = Join-Path $OllamaRoot "blobs\$digest"
if (-not (Test-Path $BlobPath)) { Write-Error "blob이 없습니다: $BlobPath" }

# GGUF인지 확인한다 — 아니면 HF에 올려도 ollama가 못 읽는다.
$fs = [System.IO.File]::OpenRead($BlobPath)
try {
    $head = New-Object byte[] 4
    $fs.Read($head, 0, 4) | Out-Null
} finally { $fs.Close() }
if ([System.Text.Encoding]::ASCII.GetString($head) -ne "GGUF") {
    Write-Error "이 blob은 GGUF가 아닙니다(매직바이트 불일치): $BlobPath"
}

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
$OutFile = Join-Path $OutDir "$name.gguf"
$sizeGB = [math]::Round($layer.size / 1GB, 2)

Write-Host "모델   : $Model"
Write-Host "원본   : $BlobPath"
Write-Host "크기   : $sizeGB GB"
Write-Host "내보낼 곳: $OutFile"
Write-Host ""
Write-Host "복사 중... (크기가 커서 몇 분 걸릴 수 있습니다)"
Copy-Item -Path $BlobPath -Destination $OutFile -Force
Write-Host "완료: $OutFile"
Write-Host ""
Write-Host "=== 다음: Hugging Face에 올리기 (한 번만) ==="
Write-Host "  pip install -U huggingface_hub"
Write-Host "  huggingface-cli login"
Write-Host "  huggingface-cli upload <계정>/$name-GGUF `"$OutFile`" $name.gguf"
Write-Host ""
Write-Host "올린 뒤에는 이 값만 알려 주면 셋업이 자동으로 받아 갑니다:"
Write-Host "  setx OFFICECLAW_PLANNER_HF_REPO `"<계정>/$name-GGUF`""
Write-Host "  (또는 scripts\setup.ps1 -PlannerHfRepo `"<계정>/$name-GGUF`")"
