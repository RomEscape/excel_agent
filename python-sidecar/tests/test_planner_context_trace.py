"""플래너에게 실제로 무엇을 보여 줬는지 로그에 남는다.

모델이 통합문서에 없는 열 이름을 지어낸 계획을 만들었을 때, 원인은 둘 중 하나다.

1. 프롬프트에 머리글을 안 넣어 줬다 — 프롬프트 조립 결함
2. 넣어 줬는데 모델이 무시했다 — 모델 결함

둘은 고치는 곳이 완전히 다르다. 로그에 "모델이 본 통합문서 상태"가 없으면 이 둘을
가를 수 없어, 실제로는 모델 문제인 것을 프롬프트에서 계속 찾게 된다.

프롬프트 전체를 남기지는 않는다. 앞부분 4천여 자는 액션 목록과 규칙이라 턴마다
똑같고, 그것 때문에 정작 필요한 뒷부분이 길이 제한에 잘려 나갔다.
"""

from __future__ import annotations

import json

from office_claw_sidecar.services import decision_trace
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt

_DIGEST = (
    "현재 통합문서 상태(실제 파일에서 읽음):\n"
    "- 시트 매출 (활성) 사용범위=A1:D9\n"
    "  열: A=코드 | B=지역 | C=금액 | D=날짜\n"
)


def _stage(turn, name):
    for entry in turn.get("stages", []):
        if entry.get("stage") == name:
            return entry
    return {}


def _build_in_turn(tmp_path, monkeypatch, context):
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)
    with decision_trace.turn_scope(endpoint="excel-live/command", message="정렬해줘"):
        prompt = build_planner_prompt("정렬해줘", context=context)
    turn = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    return prompt, turn


def test_the_workbook_digest_shown_to_the_model_is_logged(tmp_path, monkeypatch):
    prompt, turn = _build_in_turn(tmp_path, monkeypatch, {"workbook_digest_text": _DIGEST})

    given = _stage(turn, "planner_context")
    assert given, "플래너에게 준 컨텍스트가 기록되지 않았다"
    assert "A=코드 | B=지역 | C=금액 | D=날짜" in given["workbook_digest"]
    # 로그에 남은 것이 실제로 프롬프트에 들어간 것과 같아야 한다.
    assert given["workbook_digest"] in prompt


def test_an_empty_digest_is_visible_as_empty(tmp_path, monkeypatch):
    """머리글을 안 넣어 준 경우가 로그에서 드러나야 한다. 이게 1번 원인이다."""
    _, turn = _build_in_turn(tmp_path, monkeypatch, {})
    assert _stage(turn, "planner_context").get("workbook_digest") == ""


def test_the_digest_is_not_truncated_at_the_short_limit(tmp_path, monkeypatch):
    """다이제스트는 400자에서 잘리면 열 목록이 통째로 사라진다."""
    long_digest = _DIGEST + "".join(f"  '지역{i}' 값 후보: 서울, 부산, 대구\n" for i in range(40))
    assert len(long_digest) > 1000
    _, turn = _build_in_turn(tmp_path, monkeypatch, {"workbook_digest_text": long_digest})

    logged = _stage(turn, "planner_context")["workbook_digest"]
    assert len(logged) > 1000
    assert "지역30" in logged


def test_building_a_prompt_outside_a_turn_does_not_blow_up():
    """SFT 데이터 생성 스크립트도 같은 함수를 쓴다. 거기엔 열린 턴이 없다."""
    assert build_planner_prompt("정렬해줘", context={"workbook_digest_text": _DIGEST})
