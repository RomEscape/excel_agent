"""테스트 공통 설정.

기본값은 임시 디렉터리다. 테스트가 돌 때마다 600여 턴이 실제
`logs/chat_log.jsonl`에 쌓이면 사람이 읽어야 할 기록이 묻힌다.

들여다봐야 할 때는 켜면 된다. 켜면 테스트가 만든 모든 턴이
`logs/test-runs/chat_log.jsonl`에 누적되고, 어느 테스트가 만든 턴인지
`source.test`에 남는다.

    $env:OFFICE_CLAW_TRACE_TESTS = "1"
    uv run pytest -q
    python scripts/show_turns.py --log ../logs/test-runs/chat_log.jsonl --failed
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def _resolve_logs_dir() -> Path:
    if str(os.getenv("OFFICE_CLAW_TRACE_TESTS", "") or "").strip().lower() in {"1", "true", "yes"}:
        # 소스 트리의 logs/test-runs — 실제 사용 기록과는 파일을 나눈다.
        return Path(__file__).resolve().parents[2] / "logs" / "test-runs"
    return Path(tempfile.gettempdir()) / "officeclaw_test_logs"


_TEST_LOGS_DIR = _resolve_logs_dir()
_TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("OFFICE_CLAW_LOGS_DIR", str(_TEST_LOGS_DIR))


@pytest.fixture(autouse=True)
def _tag_turns_with_test_id(request):
    """이 테스트가 만든 턴에 테스트 이름을 붙인다."""
    from office_claw_sidecar.services.decision_trace import source

    with source(kind="test", test=request.node.nodeid):
        yield
