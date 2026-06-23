param(
    [switch]$BuildSidecar,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$SidecarDir = Join-Path $ProjectDir "python-sidecar"
$TauriDir = Join-Path $ProjectDir "src-tauri"

# 콘솔 UTF-8 강제 (한글 경로/로그 깨짐 방지)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Command,
        [string]$WorkingDirectory = $ProjectDir
    )

    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    Write-Host "    $Command" -ForegroundColor DarkGray

    if ($DryRun) { return }

    Push-Location $WorkingDirectory
    try {
        Invoke-Expression $Command
    } finally {
        Pop-Location
    }
}

function Test-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name 명령을 찾을 수 없습니다. $InstallHint"
    }
}

Write-Host "=== Team 503 AI 통합 설치 시작 ===" -ForegroundColor Green
Write-Host "프로젝트 경로: $ProjectDir"

Test-RequiredCommand -Name "node" -InstallHint "Node.js LTS 설치 후 새 터미널을 열어주세요. (https://nodejs.org)"
Test-RequiredCommand -Name "npm" -InstallHint "Node.js 설치에 npm이 포함됩니다. PATH를 확인해 주세요."
Invoke-Step -Title "Node 의존성 설치 (npm ci)" -Command "npm ci"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Invoke-Step -Title "Python 의존성 동기화 (uv sync --extra dev)" -Command "uv sync --extra dev" -WorkingDirectory $SidecarDir
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Step -Title "Python 의존성 설치 (py -m pip install -r requirements.txt)" -Command "py -m pip install -r `"$ProjectDir/requirements.txt`""
    } else {
        Test-RequiredCommand -Name "python" -InstallHint "Python 3.11+ 설치 후 재시도해 주세요. (https://python.org)"
        Invoke-Step -Title "Python 의존성 설치 (python -m pip install -r requirements.txt)" -Command "python -m pip install -r `"$ProjectDir/requirements.txt`""
    }
}

Test-RequiredCommand -Name "cargo" -InstallHint "Rust 설치 후 재시도해 주세요. (https://rustup.rs)"
Invoke-Step -Title "Rust 툴체인 확인 (cargo --version)" -Command "cargo --version"
Invoke-Step -Title "Tauri 크레이트 의존성 프리페치 (cargo fetch)" -Command "cargo fetch" -WorkingDirectory $TauriDir

if ($BuildSidecar) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-Step -Title "Python sidecar 빌드 (uv run --extra dev python build_sidecar.py)" -Command "uv run --extra dev python build_sidecar.py" -WorkingDirectory $SidecarDir
    } else {
        Invoke-Step -Title "Python sidecar 빌드 (python build_sidecar.py)" -Command "python build_sidecar.py" -WorkingDirectory $SidecarDir
    }
}

Write-Host ""
Write-Host "=== 통합 설치 완료 ===" -ForegroundColor Green
Write-Host "다음 실행 명령:"
Write-Host "  npm run tauri:dev"
