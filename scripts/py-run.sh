#!/bin/sh
# 이 저장소의 파이썬을 찾아 인자를 그대로 넘긴다.
#
#   sh scripts/py-run.sh -m pytest -q
#   sh scripts/py-run.sh scripts/show_turns.py -n 5
#
# 훅과 문서가 `uv run python ...`을 그대로 쓰면 uv가 없는 환경에서 한 줄도 안 돈다
# (2026-08-16 실측: Windows 개발기에 uv도 시스템 파이썬도 없어 pre-commit이 전부 실패).
# 찾는 순서는 uv → 환경변수 → 프로젝트 venv → PATH의 python이다.
set -e

if command -v uv >/dev/null 2>&1; then
    exec uv run python "$@"
fi

if [ -n "$OFFICECLAW_PY" ] && [ -x "$OFFICECLAW_PY" ]; then
    exec "$OFFICECLAW_PY" "$@"
fi

for candidate in \
    "$LOCALAPPDATA/officeclaw/venvs/python-sidecar/Scripts/python.exe" \
    "$HOME/.cache/officeclaw/venvs/python-sidecar/bin/python" \
    "services/sidecar/.venv/Scripts/python.exe" \
    "services/sidecar/.venv/bin/python" \
    "../../services/sidecar/.venv/Scripts/python.exe" \
    "../../services/sidecar/.venv/bin/python" \
    ".venv/Scripts/python.exe" \
    ".venv/bin/python"
do
    # 깨진 venv(pyvenv.cfg 없음)는 실행은 되고 즉시 죽는다. 존재만으로 고르지 않는다.
    if [ -x "$candidate" ] && "$candidate" -c "" >/dev/null 2>&1; then
        exec "$candidate" "$@"
    fi
done

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$@"
fi
exec python "$@"
