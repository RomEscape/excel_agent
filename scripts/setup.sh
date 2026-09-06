#!/usr/bin/env bash
# Team 503 AI 통합 설치 스크립트 (macOS/Linux)
set -euo pipefail 2>/dev/null || set -eu

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_DIR/services/sidecar"  # 모노레포 이행(2026-08-30): python-sidecar → services/sidecar
TAURI_DIR="$PROJECT_DIR/apps/desktop/src-tauri"  # 모노레포 이행: src-tauri → apps/desktop/src-tauri
DRY_RUN="${DRY_RUN:-0}"
# 사이드카 단일 실행파일은 배포본에만 필요하다 — dev 모드는 venv 소스로 뜬다. 기본 0(setup.ps1 과 같게).
# build_sidecar.py 가 Nuitka 컴파일까지 돌게 된 2026-09-06 이후 기본으로 켜 두면 첫 셋업이 수십 분 늘어난다.
BUILD_SIDECAR="${BUILD_SIDECAR:-0}"
AUTO_INSTALL_TOOLS="${AUTO_INSTALL_TOOLS:-1}"
SKIP_BUILD="${SKIP_BUILD:-0}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
SIDECAR_VENV_PATH="${UV_PROJECT_ENVIRONMENT:-$HOME/.cache/officeclaw/venvs/python-sidecar}"

detect_os() {
  local uname_out
  uname_out="$(uname -s | tr '[:upper:]' '[:lower:]')"
  if [[ "$uname_out" == "darwin" ]]; then
    echo "macos"
    return
  fi
  echo "linux"
}

OS_KIND="$(detect_os)"

for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=1
  fi
  if [[ "$arg" == "--build-sidecar" ]]; then
    BUILD_SIDECAR=1
  fi
  if [[ "$arg" == "--no-build-sidecar" ]]; then
    BUILD_SIDECAR=0
  fi
  if [[ "$arg" == "--no-auto-install-tools" ]]; then
    AUTO_INSTALL_TOOLS=0
  fi
  if [[ "$arg" == "--skip-build" ]]; then
    SKIP_BUILD=1
  fi
done

run_step() {
  local title="$1"
  local cmd="$2"
  local cwd="${3:-$PROJECT_DIR}"
  echo ""
  echo "==> $title"
  echo "    $cmd"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  (cd "$cwd" && eval "$cmd")
}

ensure_dir() {
  local p="$1"
  mkdir -p "$p"
}

ensure_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd 명령을 찾을 수 없습니다. $hint" >&2
    exit 1
  fi
}

hydrate_path() {
  export PATH="$CARGO_HOME/bin:$NPM_CONFIG_PREFIX/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

  if [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
  fi

  # 사용자 로컬 셸 설정에서 nvm/homebrew PATH가 들어오도록 시도.
  for f in "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do
    if [[ -f "$f" ]]; then
      # shellcheck source=/dev/null
      source "$f" >/dev/null 2>&1 || true
    fi
  done
}

try_install_with_brew() {
  local pkg="$1"
  if ! command -v brew >/dev/null 2>&1; then
    return 1
  fi
  run_step "$pkg 설치 (brew install $pkg)" "brew install $pkg" "$PROJECT_DIR"
}

auto_install_node_if_missing() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    return
  fi
  if [[ "$AUTO_INSTALL_TOOLS" != "1" ]]; then
    return
  fi
  if [[ "$OS_KIND" == "macos" ]]; then
    try_install_with_brew "node" || true
  else
    if command -v apt-get >/dev/null 2>&1; then
      run_step "Node 설치 (apt-get)" "sudo apt-get update && sudo apt-get install -y nodejs npm" "$PROJECT_DIR" || true
    fi
  fi
  hydrate_path
}

auto_install_rust_if_missing() {
  if command -v cargo >/dev/null 2>&1; then
    return
  fi
  if [[ "$AUTO_INSTALL_TOOLS" != "1" ]]; then
    return
  fi

  if command -v rustup >/dev/null 2>&1; then
    run_step "Rust toolchain 설치 (rustup default stable)" "rustup default stable" "$PROJECT_DIR" || true
  else
    run_step "Rustup 설치" "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y" "$PROJECT_DIR" || true
  fi
  hydrate_path
}

auto_install_ollama_if_missing() {
  # Windows setup.ps1 은 winget 으로 Ollama 를 깔지만 이 스크립트는 감지만 했다(2026-09-06 감사).
  # macOS 는 brew cask 로 앱을 깐다. 데몬 기동은 앱을 한 번 열거나 `ollama serve` 가 맡는다.
  if command -v ollama >/dev/null 2>&1; then
    return
  fi
  if [[ "$AUTO_INSTALL_TOOLS" != "1" ]]; then
    return
  fi
  if [[ "$OS_KIND" == "macos" ]]; then
    try_install_with_brew "ollama" || true
  else
    if command -v curl >/dev/null 2>&1; then
      run_step "Ollama 설치 (ollama.com/install.sh)" "curl -fsSL https://ollama.com/install.sh | sh" "$PROJECT_DIR" || true
    fi
  fi
  hydrate_path
}

auto_install_python_if_missing() {
  if command -v python3 >/dev/null 2>&1; then
    return
  fi
  if [[ "$AUTO_INSTALL_TOOLS" != "1" ]]; then
    return
  fi
  if [[ "$OS_KIND" == "macos" ]]; then
    try_install_with_brew "python" || true
  else
    if command -v apt-get >/dev/null 2>&1; then
      run_step "Python 설치 (apt-get)" "sudo apt-get update && sudo apt-get install -y python3 python3-pip" "$PROJECT_DIR" || true
    fi
  fi
  hydrate_path
}

echo "=== Team 503 AI 통합 설치 시작 ==="
echo "프로젝트 경로: $PROJECT_DIR"
ensure_dir "$OPENCLAW_HOME"
ensure_dir "$CARGO_HOME"
ensure_dir "$RUSTUP_HOME"
ensure_dir "$NPM_CONFIG_PREFIX"
ensure_dir "$(dirname "$SIDECAR_VENV_PATH")"

export OPENCLAW_HOME
export CARGO_HOME
export RUSTUP_HOME
export NPM_CONFIG_PREFIX
export UV_PROJECT_ENVIRONMENT="$SIDECAR_VENV_PATH"
# 새 셸의 uv run이 services/sidecar/.venv 를 따로 만드는 것을 막는다(감사 B1).
if [[ "$DRY_RUN" != "1" ]]; then
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$rc" ] && ! grep -q "UV_PROJECT_ENVIRONMENT" "$rc"; then
      echo "export UV_PROJECT_ENVIRONMENT=\"$SIDECAR_VENV_PATH\"" >> "$rc"
      echo "[알림] $rc 에 UV_PROJECT_ENVIRONMENT 를 추가했습니다."
    fi
  done
fi
echo "Python venv path: $UV_PROJECT_ENVIRONMENT"

hydrate_path
auto_install_node_if_missing
auto_install_rust_if_missing
auto_install_python_if_missing
auto_install_ollama_if_missing
# uv — 파이썬 의존성·빌드의 정상 경로 전부가 uv 전제(2026-09-06 감사 A1).
if ! command -v uv >/dev/null 2>&1; then
  run_step "uv 자동 설치 (astral.sh)" "curl -LsSf https://astral.sh/uv/install.sh | sh" "$PROJECT_DIR"
  export PATH="$HOME/.local/bin:$PATH"
  hydrate_path
fi

ensure_cmd node "Node.js LTS 설치 후 재시도해 주세요. https://nodejs.org"
ensure_cmd npm "Node.js 설치에 npm이 포함됩니다."
run_step "npm 전역 prefix 고정 (사용자 홈)" "npm config set prefix \"$NPM_CONFIG_PREFIX\"" "$PROJECT_DIR"
hydrate_path
run_step "Node 의존성 설치 (npm ci)" "npm ci" "$PROJECT_DIR/apps/desktop"  # 루트엔 lockfile이 없다

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[SETUP_OPENCLAW_MISSING_OR_PATH] openclaw 명령을 찾지 못했습니다. 설치 후 새 터미널에서 npm prefix/PATH를 다시 확인해 주세요." >&2
fi

if command -v uv >/dev/null 2>&1; then
  run_step "Python 의존성 동기화 (uv sync --extra dev)" "uv sync --extra dev" "$SIDECAR_DIR"
else
  ensure_cmd python3 "Python 3.11+ 설치 후 재시도해 주세요. https://python.org"
  # 전역이 아니라 셋업이 약속한 venv에 깐다 — 게이트·러너가 이 경로를 본다(감사 A4).
  [ -x "$SIDECAR_VENV_PATH/bin/python" ] || run_step "Python venv 생성" "python3 -m venv \"$SIDECAR_VENV_PATH\"" "$PROJECT_DIR"
  run_step "Python 의존성 설치 (venv pip)" "\"$SIDECAR_VENV_PATH/bin/python\" -m pip install -r \"$PROJECT_DIR/requirements.txt\"" "$PROJECT_DIR"
fi

ensure_cmd cargo "Rust 설치 후 재시도해 주세요. https://rustup.rs"
run_step "Rust 툴체인 확인 (cargo --version)" "cargo --version" "$PROJECT_DIR"
# externalBin 자리채움 — tauri.conf.json이 파일의 **존재**를 요구한다(dev도 같음).
# .gitignore 대상이라 클론 직후엔 없다. 없으면 바로 아래 cargo check가 실패한다(감사 A2).
BIN_DIR="$TAURI_DIR/binaries"
mkdir -p "$BIN_DIR"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) TRIPLE="aarch64-apple-darwin" ;;
  Darwin-x86_64) TRIPLE="x86_64-apple-darwin" ;;
  Linux-x86_64) TRIPLE="x86_64-unknown-linux-gnu" ;;
  *) TRIPLE="x86_64-pc-windows-msvc" ;;
esac
PLACEHOLDER="$BIN_DIR/office-claw-sidecar-$TRIPLE"
[ "$TRIPLE" = "x86_64-pc-windows-msvc" ] && PLACEHOLDER="$PLACEHOLDER.exe"
[ -f "$PLACEHOLDER" ] || touch "$PLACEHOLDER"

run_step "Tauri 크레이트 의존성 프리페치 (cargo fetch)" "cargo fetch" "$TAURI_DIR"

if [[ "$SKIP_BUILD" != "1" ]]; then
  run_step "프론트엔드 빌드 (npm run build)" "npm run build" "$PROJECT_DIR"
  run_step "Rust 체크 빌드 (cargo check)" "cargo check" "$TAURI_DIR"
fi

if [[ "$BUILD_SIDECAR" == "1" ]]; then
  if command -v uv >/dev/null 2>&1; then
    run_step "Python sidecar 빌드 (uv run --extra dev python build_sidecar.py)" "uv run --extra dev python build_sidecar.py" "$SIDECAR_DIR"
  else
    run_step "Python sidecar 빌드 (python3 build_sidecar.py)" "python3 build_sidecar.py" "$SIDECAR_DIR"
  fi
fi

echo ""

# ── 모델 준비 ────────────────────────────────────────────────────────────────
# 범용 대화(model)와 Excel 계획 수립(planner_model) 둘을 쓴다. **둘 다 Ollama 공개
# 레지스트리에 없다.** 범용 `skt/A.X-4.0-Light`는 SKT가 HF에 safetensors로만 올려서
# `ollama pull skt/A.X-4.0-Light`는 "pull model manifest: file does not exist"로
# 죽는다(2026-09-06 실측). 개발기는 커뮤니티 GGUF(hf.co/jayusop/…)를 받아 앱이
# 기대하는 이름으로 `ollama cp` 해 두었던 것이다(개발일지 2026-05-21). 같은 경로를 밟는다.
# 플래너는 이 저장소에서 파인튜닝한 것이라 어디에도 없다. 가중치(4.4GB)는 git으로
# 못 옮기므로 Hugging Face에 올려 두고 받아 온다.
GENERAL_MODEL="skt/A.X-4.0-Light:latest"
GENERAL_HF_REPO="${OFFICECLAW_GENERAL_HF_REPO:-jayusop/A.X-4.0-Light-Q4_K_M-GGUF}"
PLANNER_MODEL="ax7bplanner-v3:latest"
# 기본 배포처 — 2026-09-05 공개 업로드 완료. 환경변수가 있으면 그쪽이 우선.
HF_REPO="${OFFICECLAW_PLANNER_HF_REPO:-PJiNH/ax7bplanner-v3-GGUF}"

if command -v ollama >/dev/null 2>&1; then
  if [[ "$DRY_RUN" == "1" ]]; then
    INSTALLED_MODELS=""
  else
    INSTALLED_MODELS="$(ollama list 2>/dev/null || true)"
  fi

  if ! grep -q "${GENERAL_MODEL%%:*}" <<<"$INSTALLED_MODELS"; then
    run_step "범용 모델 내려받기 (hf.co/$GENERAL_HF_REPO)" "ollama pull hf.co/$GENERAL_HF_REPO"
    # 앱 설정(local_stack/presets.py)은 'skt/A.X-4.0-Light:latest'를 기대한다 — 받은 이름을 그쪽으로 맞춘다.
    run_step "범용 모델 이름 맞추기 ($GENERAL_MODEL)" "ollama cp hf.co/${GENERAL_HF_REPO}:latest $GENERAL_MODEL"
    # 옛 앱 설정이 저장해 둔 별칭(2026-05 온보딩) — 새 PC 첫 구동 /health 에 missing 으로 남았다(2026-09-06).
    run_step "범용 모델 옛 별칭 (ax4-light:latest)" "ollama cp $GENERAL_MODEL ax4-light:latest"
  else
    echo "[건너뜀] 범용 모델이 이미 있습니다 ($GENERAL_MODEL)"
  fi

  if ! grep -q "${PLANNER_MODEL%%:*}" <<<"$INSTALLED_MODELS"; then
    if [[ -n "$HF_REPO" ]]; then
      run_step "플래너 모델 내려받기 (hf.co/$HF_REPO)" "ollama pull hf.co/$HF_REPO"
      # 앱 설정은 'ax7bplanner-v3:latest'를 기대한다 — 받은 이름을 그쪽으로 맞춘다.
      run_step "플래너 모델 이름 맞추기 ($PLANNER_MODEL)" "ollama cp hf.co/${HF_REPO}:latest $PLANNER_MODEL"
    elif [[ -f "$PROJECT_DIR/artifacts/ax7b-planner-v3-f16.gguf" ]]; then
      run_step "플래너 모델 생성 (로컬 GGUF)" "ollama create ax7bplanner-v3 -f deploy/ollama/Modelfile.ax7b-planner-v3"
    else
      echo ""
      echo "[주의] 플래너 모델($PLANNER_MODEL)이 없습니다 — Excel 계획 수립 품질이 떨어집니다."
      echo "       받을 곳을 알려주세요: OFFICECLAW_PLANNER_HF_REPO=<계정>/ax7bplanner-v3-GGUF ./scripts/setup.sh"
    fi
  else
    echo "[건너뜀] 플래너 모델이 이미 있습니다 ($PLANNER_MODEL)"
  fi
else
  echo "[주의] ollama를 찾지 못해 모델 준비를 건너뜁니다."
fi

echo "=== 통합 설치 완료 ==="
echo "OPENCLAW_HOME=$OPENCLAW_HOME"
echo "CARGO_HOME=$CARGO_HOME"
echo "NPM_CONFIG_PREFIX=$NPM_CONFIG_PREFIX"
echo "UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT"
if command -v openclaw >/dev/null 2>&1; then
  echo "OPENCLAW_CLI=detected"
else
  echo "OPENCLAW_CLI=missing (reason_code=SETUP_OPENCLAW_MISSING_OR_PATH)"
fi
echo "다음 실행 명령: npm run tauri:dev"
