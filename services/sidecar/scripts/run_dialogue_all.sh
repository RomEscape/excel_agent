#!/usr/bin/env bash
# 8개 대화 각본을 순차(동시 금지)로 N라운드씩 돌리고 요약한다.
# 사용: bash scripts/run_dialogue_all.sh <REPEAT> [ex1 ex2 ...]   (한 번에 하나만 — 동시 실행 금지)
# 로그(_log.json)는 각본 옆에 남는다. DIALOGUE_LOG_DIR로 바꿀 수 있다.
REPEAT="${1:-1}"; shift
EXS=("$@"); [ ${#EXS[@]} -eq 0 ] && EXS=(ex1 ex2 ex3 ex4 ex5 ex6 ex7 ex8)
HERE="$(cd "$(dirname "$0")/.." && pwd)"
S="${DIALOGUE_DIR:-$HERE/scenarios/dialogue}"
# OFFICECLAW_PY 로 덮을 수 있다 — 기본은 셋업이 만드는 venv(감사 D).
PY="${OFFICECLAW_PY:-$LOCALAPPDATA/officeclaw/venvs/python-sidecar/Scripts/python.exe}"
cd "$HERE" || exit 1
for ex in "${EXS[@]}"; do
  echo "########## $ex (x$REPEAT) $(date +%H:%M:%S)"
  PYTHONUTF8=1 EXCEL_LIVE_ENGINE=file HUMAN_REPEAT="$REPEAT" "$PY" scripts/run_dialogue.py "$S/dialogue_$ex.json" 2>&1 \
    | grep -E "FAIL|예외|action=|성공 [0-9]|결정성|오염|^\s+\("
done
echo "########## done $(date +%H:%M:%S)"
