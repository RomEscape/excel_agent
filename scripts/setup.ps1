param(
    [switch]$BuildSidecar,
    [switch]$DryRun,
    [switch]$NoAutoInstallTools,
    [switch]$SkipBuild,
    # 플래너 모델을 올려 둔 Hugging Face 저장소(예: "myaccount/ax7bplanner-v3-GGUF").
    # 생략하면 환경변수 OFFICECLAW_PLANNER_HF_REPO를 본다.
    [string]$PlannerHfRepo = "",
    # 범용 대화 모델(A.X-4.0-Light)의 GGUF를 올려 둔 HF 저장소. Ollama 레지스트리에는
    # 이 모델이 없어서 HF에서 받아 `skt/A.X-4.0-Light:latest`로 이름을 맞춘다.
    # 생략하면 환경변수 OFFICECLAW_GENERAL_HF_REPO → 기본값(jayusop/…Q4_K_M-GGUF).
    [string]$GeneralHfRepo = ""
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
        $global:LASTEXITCODE = 0
        Invoke-Expression $Command
        # $ErrorActionPreference=Stop 은 네이티브 exe(npm/cargo/uv) 실패를 못 잡는다 —
        # 중간이 다 깨져도 "통합 설치 완료"가 찍혔다(2026-09-06 감사 A5).
        if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) {
            throw "단계 실패(종료코드 ${LASTEXITCODE}): $Title"
        }
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

    # winget 이 **방금** 깐 도구는 이 셸의 PATH 에 없다 — winget 은 새 프로세스에만
    # 반영되기 때문이다. 그래서 도구 0개인 새 PC 에서 uv·Ollama 를 설치하고도 곧바로
    # "찾지 못했습니다"로 빠져, 모델을 하나도 안 받은 채 "통합 설치 완료"가 찍혔다
    # (2026-09-06 새 PC 시뮬레이션 실측). 설치 위치를 직접 넣어 준다.
    Add-PathIfExists -PathEntry "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
    Add-PathIfExists -PathEntry "$env:LOCALAPPDATA\Programs\Ollama"
    Add-PathIfExists -PathEntry "$env:ProgramFiles\Ollama"
    Add-PathIfExists -PathEntry "$env:USERPROFILE\.local\bin"
    Add-PathIfExists -PathEntry "$env:LOCALAPPDATA\Programs\uv\bin"
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
# 새 셸의 `uv run`이 services/sidecar/.venv 를 따로 만드는 것을 막는다(감사 B1 — venv 이중화 실측).
if (-not $DryRun) { [Environment]::SetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", $SidecarVenvPath, "User") }
Write-Host "Python venv path: $SidecarVenvPath"
Initialize-ToolHomes
Initialize-ToolPaths
Install-CommandIfMissing -Name "node" -WingetId "OpenJS.NodeJS.LTS" -Title "Node.js 자동 설치 (winget)"
Install-CommandIfMissing -Name "cargo" -WingetId "Rustlang.Rustup" -Title "Rust 자동 설치 (winget)"
Install-CommandIfMissing -Name "ollama" -WingetId "Ollama.Ollama" -Title "Ollama 자동 설치 (winget)"
# uv — 파이썬 의존성·빌드의 정상 경로 전부가 uv 전제인데 아무도 설치하지 않았다(2026-09-06 감사 A1).
Install-CommandIfMissing -Name "uv" -WingetId "astral-sh.uv" -Title "uv 자동 설치 (winget)"
Install-CommandIfMissing -Name "py" -WingetId "Python.Python.3.12" -Title "Python 자동 설치 (winget)"
Ensure-WindowsCppToolchain
Initialize-ToolPaths
Add-MsvcLinkerPaths

# npm 전역 설치 경로를 사용자 홈으로 고정 (권한/디렉토리 이슈 방지)
Invoke-Step -Title "npm 전역 prefix 고정 (사용자 홈)" -Command "npm config set prefix `"$NpmGlobalPrefix`""
Initialize-ToolPaths

Test-RequiredCommand -Name "node" -InstallHint "Node.js LTS 설치 후 새 터미널을 열어주세요. (https://nodejs.org)"
Test-RequiredCommand -Name "npm" -InstallHint "Node.js 설치에 npm이 포함됩니다. PATH를 확인해 주세요."
# npm ci 는 node_modules 를 통째로 지우고 다시 깐다. OneDrive 폴더 안에서는 동기화 클라이언트가
# 방금 만든 파일을 잡고 있어 삭제가 EBUSY(-4082)로 죽는다(2026-09-06 실측: 두 번째 setup 실행이
# 여기서 멈춤). lockfile 이 그대로면 건너뛴다 — 재실행이 빨라지는 덤도 있다.
$NpmStamp = Join-Path $AppDir "node_modules\.package-lock.json"
$NpmLock = Join-Path $AppDir "package-lock.json"
if ((Test-Path $NpmStamp) -and ((Get-Item $NpmStamp).LastWriteTime -ge (Get-Item $NpmLock).LastWriteTime)) {
    Write-Host "[건너뜀] node_modules 가 package-lock.json 과 같거나 더 새롭다 (npm ci 생략)"
} else {
    Invoke-Step -Title "Node 의존성 설치 (npm ci)" -Command "npm ci" -WorkingDirectory $AppDir
}

if (-not (Get-Command openclaw -ErrorAction SilentlyContinue)) {
    Write-Warning "[SETUP_OPENCLAW_MISSING_OR_PATH] openclaw 명령을 찾지 못했습니다. 설치 후 새 터미널에서 npm prefix/PATH를 다시 확인해 주세요."
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Invoke-Step -Title "Python 의존성 동기화 (uv sync --extra dev)" -Command "uv sync --extra dev" -WorkingDirectory $SidecarDir
} else {
    # 전역이 아니라 **셋업이 약속한 venv**에 깐다 — 게이트·배터리·문서가 전부
    # $SidecarVenvPath\Scripts\python.exe 를 가리킨다(2026-09-06 감사 A4).
    if (Get-Command py -ErrorAction SilentlyContinue) { $BootstrapPy = "py" }
    else {
        Test-RequiredCommand -Name "python" -InstallHint "Python 3.11+ 설치 후 재시도해 주세요. (https://python.org)"
        $BootstrapPy = "python"
    }
    $VenvPy = Join-Path $SidecarVenvPath "Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) {
        Invoke-Step -Title "Python venv 생성 ($SidecarVenvPath)" -Command "$BootstrapPy -m venv `"$SidecarVenvPath`""
    }
    Invoke-Step -Title "Python 의존성 설치 (venv pip install -r requirements.txt)" -Command "& `"$VenvPy`" -m pip install -r `"$ProjectDir/requirements.txt`""
}

Test-RequiredCommand -Name "cargo" -InstallHint "Rust 설치 후 재시도해 주세요. (https://rustup.rs)"
Test-RequiredCommand -Name "link.exe" -InstallHint "Visual Studio C++ Build Tools가 필요합니다. (winget: Microsoft.VisualStudio.2022.BuildTools)"
Invoke-Step -Title "Rust 툴체인 확인 (cargo --version)" -Command "cargo --version"
# `link.exe /?` 는 도움말을 찍고도 종료코드가 0이 아니다(MSVC 14.44 실측: 1100 / -1).
# 2026-09-06 감사 A5로 Invoke-Step 이 종료코드를 보게 되자 이 단계가 **항상** 죽어
# 새 PC 셋업이 여기서 끝났다(같은 날 실측, Ji_NH). 경로만 확인하고 MSVC 것인지 본다 —
# Git for Windows 의 coreutils `link.exe` 가 먼저 잡히면 cargo 링크가 실패하기 때문이다.
$LinkExe = (Get-Command link.exe).Source
Write-Host "==> MSVC 링커 확인 (link.exe)" -ForegroundColor Cyan
Write-Host "    $LinkExe" -ForegroundColor DarkGray
if ($LinkExe -notmatch 'VC\\Tools\\MSVC') {
    throw "PATH 의 link.exe 가 MSVC 가 아닙니다: $LinkExe (Visual Studio C++ Build Tools 가 필요합니다)"
}
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

# 사이드카 단일 실행파일(PyInstaller)은 **배포본에만** 필요하다. dev 모드는 venv 소스로
# 사이드카를 띄우므로(apps/desktop/src-tauri/src/sidecar.rs) 빈 placeholder면 된다.
# 예전엔 기본으로 빌드했는데, spec(sidecar-hardened.spec)이 Nuitka --module 산출물
# (build-mod/)을 요구해 새 PC에서는 여기서 죽고 **모델 준비까지 못 갔다**(2026-09-06 실측).
# 필요할 때만 -BuildSidecar 로 켠다 — build_sidecar.py 가 Nuitka 단계까지 같이 돈다.
if ($BuildSidecar) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-Step -Title "Python sidecar 빌드 (uv run --extra dev python build_sidecar.py)" -Command "uv run --extra dev python build_sidecar.py" -WorkingDirectory $SidecarDir
    } else {
        # pip 폴백엔 PyInstaller(dev extra)가 없다 — dev 모드는 venv 소스로 뜨므로 지장 없다(감사 A3).
        Write-Warning "uv가 없어 사이드카 단일 실행파일 빌드는 건너뜁니다."
    }
}

# ── 모델 준비 ────────────────────────────────────────────────────────────────
# 앱은 모델 둘을 쓴다: 범용 대화(model)와 Excel 계획 수립(planner_model).
# **둘 다 Ollama 공개 레지스트리에 없다.** 범용 `skt/A.X-4.0-Light`는 SKT가 HF에
# safetensors로만 올렸고 Ollama 레지스트리에는 없어서 `ollama pull skt/A.X-4.0-Light`가
# "pull model manifest: file does not exist"로 죽는다(2026-09-06 실측, 새 PC Ji_NH).
# 개발기는 커뮤니티 GGUF(hf.co/jayusop/…)를 받아 앱이 기대하는 이름으로 `ollama cp`
# 해 두었던 것이다(개발일지 2026-05-21). 셋업도 같은 경로를 밟는다.
# 플래너는 이 저장소에서 파인튜닝한 것이라 어느 레지스트리에도 없다. 가중치는
# git으로 못 옮기므로(4.4GB) Hugging Face에 올려 두고 받아 온다 — 올릴 파일은
# scripts\export-planner-model.ps1이 만든다.
$GeneralModel = "skt/A.X-4.0-Light:latest"
$DefaultGeneralHfRepo = "jayusop/A.X-4.0-Light-Q4_K_M-GGUF"
$GenRepo = if (-not [string]::IsNullOrWhiteSpace($GeneralHfRepo)) {
    $GeneralHfRepo.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace($env:OFFICECLAW_GENERAL_HF_REPO)) {
    "$($env:OFFICECLAW_GENERAL_HF_REPO)".Trim()
} else {
    $DefaultGeneralHfRepo
}
$PlannerModel = "ax7bplanner-v3:latest"
# 기본 배포처 — 2026-09-05 공개 업로드 완료. 인자·환경변수가 있으면 그쪽이 우선.
$DefaultPlannerHfRepo = "PJiNH/ax7bplanner-v3-GGUF"
$HfRepo = if (-not [string]::IsNullOrWhiteSpace($PlannerHfRepo)) {
    $PlannerHfRepo.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace($env:OFFICECLAW_PLANNER_HF_REPO)) {
    "$($env:OFFICECLAW_PLANNER_HF_REPO)".Trim()
} else {
    $DefaultPlannerHfRepo
}

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $installedModels = if ($DryRun) { "" } else { (& ollama list 2>$null | Out-String) }

    if ($installedModels -notmatch [regex]::Escape($GeneralModel.Split(":")[0])) {
        Invoke-Step -Title "범용 모델 내려받기 (hf.co/$GenRepo)" -Command "ollama pull hf.co/$GenRepo"
        # 앱 설정(local_stack/presets.py)은 'skt/A.X-4.0-Light:latest'를 기대한다 — 받은 이름을 그쪽으로 맞춘다.
        Invoke-Step -Title "범용 모델 이름 맞추기 ($GeneralModel)" -Command "ollama cp hf.co/${GenRepo}:latest $GeneralModel"
        # 옛 앱 설정(2026-05 온보딩)은 별칭 'ax4-light:latest' 를 저장해 둔 PC 가 있다 — 새 PC 첫 구동
        # 실측(2026-09-06 Ji_NH)에서 /health missing_models 에 이 이름이 남았다. 같은 blob 이라 공짜다.
        Invoke-Step -Title "범용 모델 옛 별칭 (ax4-light:latest)" -Command "ollama cp $GeneralModel ax4-light:latest"
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

# ── 연습용 워크북(2026-09-06) ────────────────────────────────────────────────
# `엑셀 작업 폴더/*` 는 사용자 파일이라 gitignore 다. 그래서 새 clone 엔 README 가 말하는
# AI_Excel_Automation_Demo.xlsx 가 없었다(실클론 감사에서 발견). 원본은 추적되는
# `복잡한 엑셀 작업을 위한 자료/` 에 있으니 폴더가 비어 있을 때만 한 부 복사한다.
$WorkDir = Join-Path $ProjectDir "엑셀 작업 폴더"
$DemoSrc = Join-Path $ProjectDir "복잡한 엑셀 작업을 위한 자료\AI_Excel_Automation_Demo.xlsx"
Ensure-Directory $WorkDir
if (-not (Get-ChildItem -Path $WorkDir -Filter *.xlsx -File -ErrorAction SilentlyContinue)) {
    if (Test-Path $DemoSrc) {
        Copy-Item -Path $DemoSrc -Destination (Join-Path $WorkDir "AI_Excel_Automation_Demo.xlsx")
        Write-Host "[완료] 연습용 워크북을 '엑셀 작업 폴더'에 넣었습니다 (AI_Excel_Automation_Demo.xlsx)"
    } else {
        Write-Host "[주의] 연습용 워크북 원본을 찾지 못했습니다: $DemoSrc" -ForegroundColor Yellow
    }
} else {
    Write-Host "[건너뜀] '엑셀 작업 폴더'에 이미 엑셀 파일이 있습니다"
}

Write-Host ""
Write-Host "=== 통합 설치 완료 ===" -ForegroundColor Green
Write-Host "OPENCLAW_HOME=$env:OPENCLAW_HOME"
Write-Host "CARGO_HOME=$env:CARGO_HOME"
Write-Host "NPM_CONFIG_PREFIX=$env:NPM_CONFIG_PREFIX"
Write-Host "UV_PROJECT_ENVIRONMENT=$env:UV_PROJECT_ENVIRONMENT"
Write-Host "  ↳ 이 값은 사용자 환경변수로 저장됩니다. 다른 파이썬 프로젝트에서 'uv sync'를 돌리면" -ForegroundColor DarkGray
Write-Host "    같은 venv 를 쓰다가 사이드카 패키지가 지워집니다. 그럴 땐 그 프로젝트에서" -ForegroundColor DarkGray
Write-Host "    UV_PROJECT_ENVIRONMENT=.venv 를 지정하세요 (docs/DEVELOPMENT.md)." -ForegroundColor DarkGray
if (Get-Command openclaw -ErrorAction SilentlyContinue) {
    Write-Host "OPENCLAW_CLI=detected"
} else {
    Write-Host "OPENCLAW_CLI=missing (reason_code=SETUP_OPENCLAW_MISSING_OR_PATH)"
}
Write-Host "다음 실행 명령:"
Write-Host "  npm run tauri:dev"
