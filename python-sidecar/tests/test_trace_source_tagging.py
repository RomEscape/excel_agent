"""테스트가 만든 턴에는 출처가 붙어야 한다.

`decision_trace.source()`는 "이 턴을 누가 만들었나"를 남기려고 있다. 테스트·벤치마크가
만든 턴이 사람이 친 명령과 한 파일에 섞이면 나중에 실패를 되짚을 수 없기 때문이다.
`conftest.py`가 모든 테스트를 이 컨텍스트로 감싼다.

문제는 `ContextVar`라는 것이다. `TestClient`는 앱을 별도 스레드의 이벤트 루프에서
돌리므로, 태그가 그 경계를 넘어가지 못하면 조용히 사라진다. 조용히 사라지는 것이
특히 나쁘다 — 로그에는 출처 없는 턴이 남고, 그건 "사람이 친 명령"과 구분되지 않는다.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.services import decision_trace


def _turns(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_turn_made_through_the_test_client_carries_its_origin(tmp_path, monkeypatch):
    """TestClient로 만든 턴에도 출처 태그가 남는다."""
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    client = TestClient(app)
    with decision_trace.source(kind="diagnostic", case="출처확인"):
        client.post(
            "/excel-live/command",
            json={"message": "A1에 1 입력해줘", "workbook_id": "없는파일.xlsx", "approve": False},
        )

    turns = _turns(log_path)
    assert turns, "턴이 아예 기록되지 않았다 — 이 테스트가 재려는 상황이 아니다"
    assert turns[-1].get("source"), (
        "TestClient를 거치면서 출처 태그가 사라졌다. "
        "테스트가 만든 턴이 사람이 친 명령과 구분되지 않는다."
    )
    assert turns[-1]["source"].get("case") == "출처확인"
