#!/usr/bin/env bash
# Team 503 AI 통합 설치 스크립트 (macOS/Linux)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_DIR/python-sidecar"
TAURI_DIR="$PROJECT_DIR/src-tauri"
DRY_RUN="${DRY_RUN:-0}"
BUILD_SIDECAR="${BUILD_SIDECAR:-0}"

for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=1
  fi
  if [[ "$arg" == "--build-sidecar" ]]; then
    BUILD_SIDECAR=1
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

ensure_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd 명령을 찾을 수 없습니다. $hint" >&2
    exit 1
  fi
}

echo "=== Team 503 AI 통합 설치 시작 ==="
echo "프로젝트 경로: $PROJECT_DIR"

ensure_cmd node "Node.js LTS 설치 후 재시도해 주세요. https://nodejs.org"
ensure_cmd npm "Node.js 설치에 npm이 포함됩니다."
run_step "Node 의존성 설치 (npm ci)" "npm ci" "$PROJECT_DIR"

if command -v uv >/dev/null 2>&1; then
  run_step "Python 의존성 동기화 (uv sync --extra dev)" "uv sync --extra dev" "$SIDECAR_DIR"
else
  ensure_cmd python3 "Python 3.11+ 설치 후 재시도해 주세요. https://python.org"
  run_step "Python 의존성 설치 (python3 -m pip install -r requirements.txt)" "python3 -m pip install -r \"$PROJECT_DIR/requirements.txt\"" "$PROJECT_DIR"
fi

ensure_cmd cargo "Rust 설치 후 재시도해 주세요. https://rustup.rs"
run_step "Rust 툴체인 확인 (cargo --version)" "cargo --version" "$PROJECT_DIR"
run_step "Tauri 크레이트 의존성 프리페치 (cargo fetch)" "cargo fetch" "$TAURI_DIR"

if [[ "$BUILD_SIDECAR" == "1" ]]; then
  if command -v uv >/dev/null 2>&1; then
    run_step "Python sidecar 빌드 (uv run --extra dev python build_sidecar.py)" "uv run --extra dev python build_sidecar.py" "$SIDECAR_DIR"
  else
    run_step "Python sidecar 빌드 (python3 build_sidecar.py)" "python3 build_sidecar.py" "$SIDECAR_DIR"
  fi
fi

echo ""
echo "=== 통합 설치 완료 ==="
echo "다음 실행 명령: npm run tauri:dev"
