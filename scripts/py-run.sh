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
    # pytest·ruff 는 `[project.optional-dependencies] dev` 에 있고, `uv run` 은 extra 를
    # 기본으로 넣지 않는다 — 그냥 부르면 "No module named pytest" 로 죽는다
    # (2026-09-08 실측: uv 가 프로젝트 venv 를 잠금파일대로 맞추면서 pytest 를 걷어내
    #  pre-commit 의 python-pins 훅이 커밋을 막았다).
    # 다만 extra 를 늘 동기화해 두면 PyInstaller 번들에 그대로 실리므로(pyproject 주석)
    # **그 extra 가 실제로 필요한 명령일 때만** 붙인다.
    case " $* " in
        *" -m pytest "*|*" -m ruff "*)
            exec uv run --extra dev python "$@"
            ;;
    esac
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
