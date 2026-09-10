"""테스트 공통 설정.

기본값은 임시 디렉터리다. 테스트가 돌 때마다 600여 턴이 실제
`logs/chat_log.jsonl`에 쌓이면 사람이 읽어야 할 기록이 묻힌다.

들여다봐야 할 때는 켜면 된다. 켜면 테스트가 만든 모든 턴이 저장소 **밖**
`<reports>/test-runs/chat_log.jsonl`(Windows 기본 `%LOCALAPPDATA%/office_claw/reports/`,
환경변수 `OFFICE_CLAW_REPORTS_DIR` 로 변경)에 누적되고, 어느 테스트가 만든 턴인지
`source.test`에 남는다. 저장소 `logs/` 에는 `chat_log.jsonl` 하나만 둔다는
규칙(docs/logs.md)이라 테스트 기록도 저장소 안에는 두지 않는다.

    $env:OFFICE_CLAW_TRACE_TESTS = "1"
    uv run pytest -q
    python scripts/show_turns.py --log "$env:LOCALAPPDATA/office_claw/reports/test-runs/chat_log.jsonl" --failed
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# 모노레포 공용 패키지(oc-protocol·oc-shared) 폴백 — uv sync를 못 돌린 환경
# (uv 없는 개발기, 스크래치 워크트리)에서도 relay 사슬 import가 성립하게 한다.
# uv sync가 된 환경에서는 site-packages가 먼저 잡히므로 이 블록은 무해하다.
try:
    import oc_protocol  # noqa: F401
except ImportError:  # pragma: no cover - 환경 폴백
    import sys

    _repo_root = Path(__file__).resolve().parents[3]
    sys.path.append(str(_repo_root / "packages" / "protocol" / "python"))
    sys.path.append(str(_repo_root / "packages" / "py-shared"))


def _resolve_logs_dir() -> Path:
    if str(os.getenv("OFFICE_CLAW_TRACE_TESTS", "") or "").strip().lower() in {"1", "true", "yes"}:
        # 저장소 밖 <reports>/test-runs — 실제 사용 기록(logs/chat_log.jsonl)과 파일을
        # 나누고, 저장소 logs/ 에는 chat_log.jsonl 하나만 둔다(docs/logs.md).
        # 기본 경로(임시 디렉터리)에서는 config 를 건드리지 않도록 여기서만 임포트한다.
        from office_claw_sidecar.config import get_reports_dir

        return get_reports_dir() / "test-runs"
    return Path(tempfile.gettempdir()) / "officeclaw_test_logs"


_TEST_LOGS_DIR = _resolve_logs_dir()
_TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("OFFICE_CLAW_LOGS_DIR", str(_TEST_LOGS_DIR))
# 회전 조각과 스크립트 산출물도 같은 폴더 아래로 돌린다. 이걸 안 하면 테스트용 chat_log 가
# 64MB 를 넘는 순간 `decision_trace._rotate_if_needed` 가 조각을 사용자의 실제
# %LOCALAPPDATA%/office_claw/chat_log_archive 로 옮기고, 테스트가 부르는 스크립트가
# 실제 reports/ 를 더럽힌다(2026-09-11 검증에서 짚은 결함). 둘 다 config 의 get_* 가
# 부를 때 읽으므로 임포트 시점에 미리 정해 둬야 한다.
os.environ.setdefault("OFFICE_CLAW_CHAT_LOG_ARCHIVE_DIR", str(_TEST_LOGS_DIR / "archive"))
os.environ.setdefault("OFFICE_CLAW_REPORTS_DIR", str(_TEST_LOGS_DIR / "reports"))


@pytest.fixture(autouse=True)
def _no_live_model_by_default(monkeypatch):
    """테스트는 실제 Ollama 를 부르지 않는다 — 모델이 필요하면 그 테스트가 목을 세운다.

    2026-09-11 실측: `OFFICECLAW_INTENT_FIRST` 기본이 켜진 뒤 `/excel-live/command` 를
    치는 테스트 24개 파일 중 12개가 모델 격리 없이 **실제 Ollama** 를 불렀다. 개발기에서는
    범용 모델의 답이 계획을 바꿔(연결 수식 → 값 쓰기) 테스트가 떨어지고, CI 처럼 Ollama 가
    없으면 예외 폴백으로 통과한다 — 환경에 따라 결과가 갈리는 테스트다. 전체 스위트도
    213초 → 283초로 늘었다(모델 왕복).

    `test_battery_regressions.TestGuiTableInterviewIncident20260825` 가 쓰던 `_NoLLM`
    과 같은 꼴을 기본으로 깐다. 어떤 메서드를 불러도 RuntimeError 라 의도 정규화·
    되묻기 생성은 자기 예외 처리로 규칙 폴백을 타고, 플래너 호출은 실패로 떨어진다.
    자기 가짜 모델이 필요한 테스트는 지금처럼 `app.dependency_overrides[get_llm_service]`
    를 덮으면 이 기본을 이긴다(같은 키를 나중에 setitem 한 쪽이 이긴다).
    """
    from office_claw_sidecar.main import app
    from office_claw_sidecar.services.llm_service import get_llm_service

    class _NoLLM:
        def __getattr__(self, name):
            def _refuse(*a, **k):
                raise RuntimeError(f"테스트는 모델을 부르지 않는다 — {name}() 를 목으로 세워라")

            return _refuse

    monkeypatch.setitem(app.dependency_overrides, get_llm_service, lambda: _NoLLM())


@pytest.fixture(autouse=True)
def _tag_turns_with_test_id(request):
    """이 테스트가 만든 턴에 테스트 이름을 붙인다."""
    from office_claw_sidecar.services.decision_trace import source

    with source(kind="test", test=request.node.nodeid):
        yield
