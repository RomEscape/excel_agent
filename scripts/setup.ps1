param(
    [switch]$BuildSidecar,
    [switch]$DryRun,
    [switch]$NoAutoInstallTools,
    [switch]$SkipBuild
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

function Add-PathIfExists {
    param([Parameter(Mandatory = $true)][string]$PathEntry)
    if (-not (Test-Path $PathEntry)) { return }
    $pathParts = $env:PATH -split ";"
    if ($pathParts -contains $PathEntry) { return }
    $env:PATH = "$PathEntry;$env:PATH"
}

function Initialize-ToolPaths {
    Add-PathIfExists -PathEntry "$env:USERPROFILE\.cargo\bin"
    Add-PathIfExists -PathEntry "$env:ProgramFiles\nodejs"
    Add-PathIfExists -PathEntry "${env:ProgramFiles(x86)}\nodejs"
    Add-PathIfExists -PathEntry "$env:APPDATA\npm"
}

function Install-CommandIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WingetId,
        [Parameter(Mandatory = $true)][string]$Title
    )
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    if ($NoAutoInstallTools) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }

    Invoke-Step -Title $Title -Command "winget install -e --id $WingetId --accept-package-agreements --accept-source-agreements"
    Initialize-ToolPaths
}

Write-Host "=== Team 503 AI 통합 설치 시작 ===" -ForegroundColor Green
Write-Host "프로젝트 경로: $ProjectDir"
Initialize-ToolPaths
Install-CommandIfMissing -Name "node" -WingetId "OpenJS.NodeJS.LTS" -Title "Node.js 자동 설치 (winget)"
Install-CommandIfMissing -Name "cargo" -WingetId "Rustlang.Rustup" -Title "Rust 자동 설치 (winget)"
Install-CommandIfMissing -Name "py" -WingetId "Python.Python.3.12" -Title "Python 자동 설치 (winget)"
Initialize-ToolPaths

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

if (-not $SkipBuild) {
    Invoke-Step -Title "프론트엔드 빌드 (npm run build)" -Command "npm run build"
    Invoke-Step -Title "Rust 체크 빌드 (cargo check)" -Command "cargo check" -WorkingDirectory $TauriDir
}

if ($BuildSidecar -or (-not $SkipBuild)) {
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
