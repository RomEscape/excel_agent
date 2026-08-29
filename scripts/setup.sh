#!/usr/bin/env bash
# Team 503 AI 통합 설치 스크립트 (macOS/Linux)
set -euo pipefail 2>/dev/null || set -eu

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_DIR/services/sidecar"  # 모노레포 이행(2026-08-30): python-sidecar → services/sidecar
TAURI_DIR="$PROJECT_DIR/apps/desktop/src-tauri"  # 모노레포 이행: src-tauri → apps/desktop/src-tauri
DRY_RUN="${DRY_RUN:-0}"
BUILD_SIDECAR="${BUILD_SIDECAR:-1}"
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
echo "Python venv path: $UV_PROJECT_ENVIRONMENT"

hydrate_path
auto_install_node_if_missing
auto_install_rust_if_missing
auto_install_python_if_missing

ensure_cmd node "Node.js LTS 설치 후 재시도해 주세요. https://nodejs.org"
ensure_cmd npm "Node.js 설치에 npm이 포함됩니다."
run_step "npm 전역 prefix 고정 (사용자 홈)" "npm config set prefix \"$NPM_CONFIG_PREFIX\"" "$PROJECT_DIR"
hydrate_path
run_step "Node 의존성 설치 (npm ci)" "npm ci" "$PROJECT_DIR"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[SETUP_OPENCLAW_MISSING_OR_PATH] openclaw 명령을 찾지 못했습니다. 설치 후 새 터미널에서 npm prefix/PATH를 다시 확인해 주세요." >&2
fi

if command -v uv >/dev/null 2>&1; then
  run_step "Python 의존성 동기화 (uv sync --extra dev)" "uv sync --extra dev" "$SIDECAR_DIR"
else
  ensure_cmd python3 "Python 3.11+ 설치 후 재시도해 주세요. https://python.org"
  run_step "Python 의존성 설치 (python3 -m pip install -r requirements.txt)" "python3 -m pip install -r \"$PROJECT_DIR/requirements.txt\"" "$PROJECT_DIR"
fi

ensure_cmd cargo "Rust 설치 후 재시도해 주세요. https://rustup.rs"
run_step "Rust 툴체인 확인 (cargo --version)" "cargo --version" "$PROJECT_DIR"
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
