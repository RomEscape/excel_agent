"""지우기 규칙 경로가 시트를 통째로 비우지 않게 막는다.

"Discount 열은 이제 안 쓰니까 지워줘"가 규칙 경로로 가면 target_range가
__ACTIVE_SELECTION__(= 파일 엔진에서는 사용 범위 전체)이 되어 표가 통째로 날아간다.
실행은 성공으로 보고되므로 사용자는 되돌리기 전까지 알아채지 못한다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _build_quick_action_plan


def _first_action(message: str, context_range: str | None = None) -> str:
    plan = _build_quick_action_plan(message, context_range)
    return str(plan[0]["action"]) if plan else ""


@pytest.mark.parametrize(
    "message",
    [
        "Discount 열은 이제 안 쓰니까 지워줘",
        "할인율 열 삭제해줘",
        "취소된 주문은 지워줘",
        "상태가 완료인 것들은 다 빼고 지워줘",
        "중복된 행 지워줘",
        "빈 행 삭제해줘",
    ],
)
def test_targeted_delete_requests_go_to_the_planner(message):
    assert _first_action(message) != "excel_live.clear_range"


@pytest.mark.parametrize(
    "message",
    [
        "전체 다 지워줘",
        "시트 전체 비워줘",
        "싹 다 밀어줘",
        "초기 상태로 되돌려줘",
    ],
)
def test_whole_sheet_reset_still_uses_the_quick_path(message):
    assert _first_action(message) == "excel_live.clear_range"


def test_explicit_range_still_uses_the_quick_path():
    plan = _build_quick_action_plan("A1:C5 지워줘", None)
    assert plan and plan[0]["action"] == "excel_live.clear_range"
    assert plan[0]["params"]["target_range"] == "A1:C5"
