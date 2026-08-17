"""표를 없애라면 값만이 아니라 **서식까지** 걷어내야 한다.

2026-08-17 GUI 실측: 사용자가 A1:D9를 붙여넣고 "여기를 원래대로 되돌려줘 안에
표를 없애줘"라고 했는데, 에이전트는 "[1/1] 엑셀 작업이 완료되었습니다"라고 답하고
**화면은 그대로였다.** 사용자는 "하나도 작업이 처리가 안 되잖아"라고 했다.

원인 두 가지:
  1. `_is_clear_reset_request`의 동사 목록에 **"없애"가 없었다.** 규칙에 안 걸려
     플래너로 갔고, 플래너가 무언가 하고 성공만 보고했다.
  2. 걸렸더라도 `clear_range`는 **값만** 지운다. 셀이 이미 비어 있고 테두리만
     남은 상태에서는 눈에 보이는 변화가 0이다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import (
    _build_quick_action_plan,
    _is_clear_reset_request,
)


def _actions(message: str, context: str | None = "A1:D9") -> list[str]:
    return [s["action"] for s in (_build_quick_action_plan(message, context) or [])]


class TestClearVerbs:
    @pytest.mark.parametrize(
        "verb", ["지워줘", "삭제해줘", "없애줘", "비워줘", "초기화해줘"]
    )
    def test_every_clear_verb_is_recognized(self, verb):
        # "없애"가 빠져 있었다 — 가장 자연스러운 말인데 규칙이 못 잡았다.
        assert _is_clear_reset_request(f"여기 {verb}")

    def test_a_prohibition_is_not_a_clear_request(self):
        # "지우지 마"를 지우기로 읽으면 데이터가 날아간다.
        assert not _is_clear_reset_request("여기 지우지 마")
        assert not _is_clear_reset_request("삭제 안 되게 해줘")


class TestRemovingATableStripsFormatting:
    @pytest.mark.parametrize(
        "message",
        [
            "여기를 원래대로 되돌려줘 안에 표를 없애줘",
            "여기 표 없애줘",
            "여기 표 지워줘",
            "여기 서식 없애줘",
            "여기 표 초기화해줘",
        ],
    )
    def test_it_removes_borders_and_fill_too(self, message):
        actions = _actions(message)
        assert "excel_live.apply_border" in actions, f"테두리가 남으면 화면이 그대로다: {message}"
        assert "excel_live.fill_range" in actions
        assert "excel_live.clear_range" in actions

    def test_the_border_step_removes_rather_than_draws(self):
        steps = _build_quick_action_plan("여기 표 없애줘", "A1:D9") or []
        border = next(s for s in steps if s["action"] == "excel_live.apply_border")
        assert border["params"]["line_style"] == "none"

    def test_it_targets_the_given_range(self):
        for step in _build_quick_action_plan("여기 표 없애줘", "A1:D9") or []:
            assert step["params"]["target_range"] == "A1:D9"

    def test_a_border_only_request_stays_border_only(self):
        # "테두리만 지워줘"에 값까지 지우면 데이터가 날아간다.
        # 테두리 전용 규칙이 먼저 잡는 게 맞다.
        assert _actions("여기 테두리 지워줘") == ["excel_live.apply_border"]


class TestValueOnlyClearStaysNarrow:
    @pytest.mark.parametrize(
        "message", ["여기 값만 지워줘", "여기 내용만 비워줘", "A1:D9 초기화해줘"]
    )
    def test_it_does_not_strip_formatting(self, message):
        # 서식을 말하지 않았는데 테두리를 지우면 사용자가 만든 표가 망가진다.
        actions = _actions(message)
        assert actions == ["excel_live.clear_range"], f"과잉 삭제: {message} → {actions}"
