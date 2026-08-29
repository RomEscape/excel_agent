#!/bin/bash
# Development script: run Python sidecar + Vite + Tauri dev in parallel
set -e

# Load nvm if available
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Load Cargo if available
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_DIR/services/sidecar"
APP_DIR="$PROJECT_DIR/apps/desktop"

echo "=== Starting Office Claw Development ==="
# SIDECAR_VENV_PATH는 어디서도 정의되지 않아 빈 문자열로 흘렀다(2026-08-30 감사)
# — venv가 의도한 공유 경로가 아니라 uv 기본 위치에 생겼다. 기본값을 준다.
SIDECAR_VENV_PATH="${SIDECAR_VENV_PATH:-$HOME/.cache/officeclaw/venvs/python-sidecar}"
mkdir -p "$(dirname "$SIDECAR_VENV_PATH")"
export UV_PROJECT_ENVIRONMENT="$SIDECAR_VENV_PATH"
echo "Python venv path: $UV_PROJECT_ENVIRONMENT"

# 1. Sync Python dependencies via uv
echo "Syncing Python dependencies..."
cd "$SIDECAR_DIR"
uv sync

# 1.5 Tauri externalBin placeholder — 없으면 tauri dev 컴파일이 곧장 실패한다
# (tauri.conf.json bundle.externalBin, 2026-08-30 감사). dev 모드 사이드카는
# 소스(venv)로 뜨므로 빈 파일이면 충분하다.
BIN_DIR="$APP_DIR/src-tauri/binaries"
mkdir -p "$BIN_DIR"
case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) TRIPLE="aarch64-apple-darwin" ;;
    Darwin-x86_64) TRIPLE="x86_64-apple-darwin" ;;
    Linux-x86_64) TRIPLE="x86_64-unknown-linux-gnu" ;;
    *) TRIPLE="x86_64-pc-windows-msvc" ;;
esac
PLACEHOLDER="$BIN_DIR/office-claw-sidecar-$TRIPLE"
[ "$TRIPLE" = "x86_64-pc-windows-msvc" ] && PLACEHOLDER="$PLACEHOLDER.exe"
if [ ! -f "$PLACEHOLDER" ]; then
    echo "Creating sidecar placeholder: $PLACEHOLDER"
    touch "$PLACEHOLDER"
fi

# 2. Install JS dependencies if node_modules missing
if [ ! -d "$APP_DIR/node_modules" ]; then
    echo "Installing JS dependencies..."
    cd "$APP_DIR"
    npm install
fi

# 3. Start Python sidecar in background (via uv run)
echo "Starting Python sidecar..."
cd "$SIDECAR_DIR"
uv run python -m office_claw_sidecar --port 19532 --auth-token dev-token --reload &
SIDECAR_PID=$!
echo "Sidecar PID: $SIDECAR_PID"

# 4. Start Vite dev server in background
echo "Starting Vite dev server..."
cd "$APP_DIR"
npm run dev &
VITE_PID=$!
echo "Vite PID: $VITE_PID"

# 5. Wait for sidecar to be ready
echo "Waiting for sidecar..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:19532/health > /dev/null 2>&1; then
        echo "Sidecar is ready!"
        break
    fi
    sleep 0.5
done

# 6. Wait for Vite to be ready
echo "Waiting for Vite..."
for i in $(seq 1 20); do
    if curl -s http://localhost:1420 > /dev/null 2>&1; then
        echo "Vite is ready!"
        break
    fi
    sleep 0.5
done

# 7. Start Tauri app — cargo tauri(별도 설치 필요) 대신 npm 로컬 CLI를 쓴다
# (@tauri-apps/cli가 package.json 의존성이라 npm install만으로 성립, 2026-08-30 감사).
echo "Starting Tauri app..."
cd "$APP_DIR"
npm run tauri:dev

# Cleanup on exit
kill $SIDECAR_PID 2>/dev/null || true
kill $VITE_PID 2>/dev/null || true
echo "=== Development session ended ==="
