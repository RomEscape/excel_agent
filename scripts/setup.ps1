param(
    [switch]$BuildSidecar,
    [switch]$DryRun,
    [switch]$NoAutoInstallTools,
    [switch]$SkipBuild,
    # 플래너 모델을 올려 둔 Hugging Face 저장소(예: "myaccount/ax7bplanner-v3-GGUF").
    # 생략하면 환경변수 OFFICECLAW_PLANNER_HF_REPO를 본다.
    [string]$PlannerHfRepo = ""
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
# 모노레포 이행(2026-08-30): python-sidecar → services/sidecar, src-tauri → apps/desktop/src-tauri.
# bash 쪽만 고쳐져 있어 윈도우 셋업이 통째로 깨져 있었다(2026-09-05 실측).
$SidecarDir = Join-Path $ProjectDir "services\sidecar"
$TauriDir = Join-Path $ProjectDir "apps\desktop\src-tauri"
$AppDir = Join-Path $ProjectDir "apps\desktop"
$OpenClawHome = Join-Path $env:USERPROFILE ".openclaw"
$CargoHome = Join-Path $env:USERPROFILE ".cargo"
$RustupHome = Join-Path $env:USERPROFILE ".rustup"
$NpmGlobalPrefix = Join-Path $env:USERPROFILE ".npm-global"
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

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$PathValue)
    if (-not (Test-Path $PathValue)) {
        New-Item -ItemType Directory -Force -Path $PathValue | Out-Null
    }
}

function Initialize-ToolHomes {
    Ensure-Directory -PathValue $OpenClawHome
    Ensure-Directory -PathValue $CargoHome
    Ensure-Directory -PathValue $RustupHome
    Ensure-Directory -PathValue $NpmGlobalPrefix

    $env:OPENCLAW_HOME = $OpenClawHome
    $env:CARGO_HOME = $CargoHome
    $env:RUSTUP_HOME = $RustupHome
    $env:NPM_CONFIG_PREFIX = $NpmGlobalPrefix
}

function Initialize-ToolPaths {
    Add-PathIfExists -PathEntry "$CargoHome\bin"
    Add-PathIfExists -PathEntry "$env:ProgramFiles\nodejs"
    Add-PathIfExists -PathEntry "${env:ProgramFiles(x86)}\nodejs"
    Add-PathIfExists -PathEntry "$env:APPDATA\npm"
    Add-PathIfExists -PathEntry $NpmGlobalPrefix
    Add-PathIfExists -PathEntry (Join-Path $NpmGlobalPrefix "bin")
}

function Add-MsvcLinkerPaths {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { return }

    try {
        $installPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if (-not $installPath) { return }

        $msvcRoot = Join-Path $installPath "VC\Tools\MSVC"
        if (-not (Test-Path $msvcRoot)) { return }
        $latestMsvc = Get-ChildItem -Path $msvcRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
        if (-not $latestMsvc) { return }

        Add-PathIfExists -PathEntry (Join-Path $latestMsvc.FullName "bin\Hostx64\x64")

        $kitsBin = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
        if (Test-Path $kitsBin) {
            $latestKit = Get-ChildItem -Path $kitsBin -Directory | Sort-Object Name -Descending | Select-Object -First 1
            if ($latestKit) {
                Add-PathIfExists -PathEntry (Join-Path $latestKit.FullName "x64")
            }
        }
    } catch {
        # ignore
    }
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

function Ensure-WindowsCppToolchain {
    if (Get-Command link.exe -ErrorAction SilentlyContinue) { return }
    Add-MsvcLinkerPaths
    if (Get-Command link.exe -ErrorAction SilentlyContinue) { return }
    if ($NoAutoInstallTools) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }

    $vsArgs = "--wait --quiet --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
    Invoke-Step -Title "MSVC C++ 빌드 도구 자동 설치 (winget)" -Command "winget install -e --id Microsoft.VisualStudio.2022.BuildTools --accept-package-agreements --accept-source-agreements --override `"$vsArgs`""
    Add-MsvcLinkerPaths
}

Write-Host "=== Team 503 AI 통합 설치 시작 ===" -ForegroundColor Green
Write-Host "프로젝트 경로: $ProjectDir"
$SidecarVenvParent = Split-Path -Parent $SidecarVenvPath
if ($SidecarVenvParent) {
    Ensure-Directory -PathValue $SidecarVenvParent
}
$env:UV_PROJECT_ENVIRONMENT = $SidecarVenvPath
Write-Host "Python venv path: $SidecarVenvPath"
Initialize-ToolHomes
Initialize-ToolPaths
Install-CommandIfMissing -Name "node" -WingetId "OpenJS.NodeJS.LTS" -Title "Node.js 자동 설치 (winget)"
Install-CommandIfMissing -Name "cargo" -WingetId "Rustlang.Rustup" -Title "Rust 자동 설치 (winget)"
Install-CommandIfMissing -Name "ollama" -WingetId "Ollama.Ollama" -Title "Ollama 자동 설치 (winget)"
Install-CommandIfMissing -Name "py" -WingetId "Python.Python.3.12" -Title "Python 자동 설치 (winget)"
Ensure-WindowsCppToolchain
Initialize-ToolPaths
Add-MsvcLinkerPaths

# npm 전역 설치 경로를 사용자 홈으로 고정 (권한/디렉토리 이슈 방지)
Invoke-Step -Title "npm 전역 prefix 고정 (사용자 홈)" -Command "npm config set prefix `"$NpmGlobalPrefix`""
Initialize-ToolPaths

Test-RequiredCommand -Name "node" -InstallHint "Node.js LTS 설치 후 새 터미널을 열어주세요. (https://nodejs.org)"
Test-RequiredCommand -Name "npm" -InstallHint "Node.js 설치에 npm이 포함됩니다. PATH를 확인해 주세요."
Invoke-Step -Title "Node 의존성 설치 (npm ci)" -Command "npm ci" -WorkingDirectory $AppDir

if (-not (Get-Command openclaw -ErrorAction SilentlyContinue)) {
    Write-Warning "[SETUP_OPENCLAW_MISSING_OR_PATH] openclaw 명령을 찾지 못했습니다. 설치 후 새 터미널에서 npm prefix/PATH를 다시 확인해 주세요."
}

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
Test-RequiredCommand -Name "link.exe" -InstallHint "Visual Studio C++ Build Tools가 필요합니다. (winget: Microsoft.VisualStudio.2022.BuildTools)"
Invoke-Step -Title "Rust 툴체인 확인 (cargo --version)" -Command "cargo --version"
Invoke-Step -Title "MSVC 링커 확인 (link.exe)" -Command "link.exe /? | Out-Null"
# externalBin 자리채움 — tauri.conf.json이 이 파일의 **존재**를 요구한다(dev도 마찬가지).
# .gitignore 대상이라 클론 직후엔 없다. dev 모드 사이드카는 venv 소스로 뜨므로 빈 파일로 충분하다.
$BinDir = Join-Path $TauriDir "binaries"
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Force -Path $BinDir | Out-Null }
$Placeholder = Join-Path $BinDir "office-claw-sidecar-x86_64-pc-windows-msvc.exe"
if (-not (Test-Path $Placeholder)) { New-Item -ItemType File -Path $Placeholder | Out-Null }

Invoke-Step -Title "Tauri 크레이트 의존성 프리페치 (cargo fetch)" -Command "cargo fetch" -WorkingDirectory $TauriDir

if (-not $SkipBuild) {
    Invoke-Step -Title "프론트엔드 빌드 (npm run build)" -Command "npm run build" -WorkingDirectory $AppDir
    Invoke-Step -Title "Rust 체크 빌드 (cargo check)" -Command "cargo check" -WorkingDirectory $TauriDir
}

if ($BuildSidecar -or (-not $SkipBuild)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-Step -Title "Python sidecar 빌드 (uv run --extra dev python build_sidecar.py)" -Command "uv run --extra dev python build_sidecar.py" -WorkingDirectory $SidecarDir
    } else {
        Invoke-Step -Title "Python sidecar 빌드 (python build_sidecar.py)" -Command "python build_sidecar.py" -WorkingDirectory $SidecarDir
    }
}

# ── 모델 준비 ────────────────────────────────────────────────────────────────
# 앱은 모델 둘을 쓴다: 범용 대화(model)와 Excel 계획 수립(planner_model).
# 범용은 공개 레지스트리에 있지만, 플래너는 이 저장소에서 파인튜닝한 것이라
# 어느 레지스트리에도 없다. 가중치는 git으로 못 옮기므로(4.4GB) Hugging Face에
# 올려 두고 받아 온다 — 올릴 파일은 scripts\export-planner-model.ps1이 만든다.
$GeneralModel = "skt/A.X-4.0-Light:latest"
$PlannerModel = "ax7bplanner-v3:latest"
$HfRepo = if (-not [string]::IsNullOrWhiteSpace($PlannerHfRepo)) {
    $PlannerHfRepo.Trim()
} else {
    "$($env:OFFICECLAW_PLANNER_HF_REPO)".Trim()
}

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $installedModels = if ($DryRun) { "" } else { (& ollama list 2>$null | Out-String) }

    if ($installedModels -notmatch [regex]::Escape($GeneralModel.Split(":")[0])) {
        Invoke-Step -Title "범용 모델 내려받기 ($GeneralModel)" -Command "ollama pull $GeneralModel"
    } else {
        Write-Host "[건너뜀] 범용 모델이 이미 있습니다 ($GeneralModel)"
    }

    if ($installedModels -notmatch [regex]::Escape($PlannerModel.Split(":")[0])) {
        $LocalGguf = Join-Path $ProjectDir "artifacts\ax7b-planner-v3-f16.gguf"
        if (-not [string]::IsNullOrWhiteSpace($HfRepo)) {
            Invoke-Step -Title "플래너 모델 내려받기 (hf.co/$HfRepo)" -Command "ollama pull hf.co/$HfRepo"
            # 앱 설정은 'ax7bplanner-v3:latest'를 기대한다 — 받은 이름을 그쪽으로 맞춘다.
            Invoke-Step -Title "플래너 모델 이름 맞추기 ($PlannerModel)" -Command "ollama cp hf.co/${HfRepo}:latest $PlannerModel"
        } elseif (Test-Path $LocalGguf) {
            Invoke-Step -Title "플래너 모델 생성 (로컬 GGUF)" -Command "ollama create ax7bplanner-v3 -f deploy\ollama\Modelfile.ax7b-planner-v3"
        } else {
            Write-Host ""
            Write-Host "[주의] 플래너 모델($PlannerModel)이 없습니다." -ForegroundColor Yellow
            Write-Host "       Excel 계획 수립 품질이 떨어집니다. 받을 곳을 알려주세요:" -ForegroundColor Yellow
            Write-Host "         powershell scripts\setup.ps1 -PlannerHfRepo `"<계정>/ax7bplanner-v3-GGUF`"" -ForegroundColor Yellow
            Write-Host "       (모델을 가진 사람은 scripts\export-planner-model.ps1로 올릴 파일을 만듭니다)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[건너뜀] 플래너 모델이 이미 있습니다 ($PlannerModel)"
    }
} else {
    Write-Host "[주의] ollama를 찾지 못해 모델 준비를 건너뜁니다." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 통합 설치 완료 ===" -ForegroundColor Green
Write-Host "OPENCLAW_HOME=$env:OPENCLAW_HOME"
Write-Host "CARGO_HOME=$env:CARGO_HOME"
Write-Host "NPM_CONFIG_PREFIX=$env:NPM_CONFIG_PREFIX"
Write-Host "UV_PROJECT_ENVIRONMENT=$env:UV_PROJECT_ENVIRONMENT"
if (Get-Command openclaw -ErrorAction SilentlyContinue) {
    Write-Host "OPENCLAW_CLI=detected"
} else {
    Write-Host "OPENCLAW_CLI=missing (reason_code=SETUP_OPENCLAW_MISSING_OR_PATH)"
}
Write-Host "다음 실행 명령:"
Write-Host "  npm run tauri:dev"
