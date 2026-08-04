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
mkdir -p "$(dirname "$SIDECAR_VENV_PATH")"
export UV_PROJECT_ENVIRONMENT="$SIDECAR_VENV_PATH"
echo "Python venv path: $UV_PROJECT_ENVIRONMENT"

# 1. Sync Python dependencies via uv
echo "Syncing Python dependencies..."
cd "$SIDECAR_DIR"
uv sync

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

# 7. Start Tauri app
echo "Starting Tauri app..."
cd "$APP_DIR"
cargo tauri dev

# Cleanup on exit
kill $SIDECAR_PID 2>/dev/null || true
kill $VITE_PID 2>/dev/null || true
echo "=== Development session ended ==="
