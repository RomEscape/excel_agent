"""조건에 맞는 셀이 0개인 것은 실패가 아니다 (Task 7).

## 무엇이 문제였나

"50 이상인 셀만 노란색"에서 50 이상이 하나도 없으면 아무것도 안 칠하는 게 **정답**이다.
그런데 검증기가 `changed_cells >= 1`로 판정해서 이걸 실패로 봤다. 실패로 보면
`abort_on_failure`로 계획이 끊기고 스냅샷 롤백이 돌고 재계획이 뜬다.

2026-08-11 `0811-182610-armA-off` 실측: `이상치강조`가 3회 모두
`verify:failed×2 → replan:1`을 탔고, 재계획이 조건을 느슨하게 만들어 **49행 전부**를
칠했다. 잘못된 오탐 하나가 정상 결과를 파괴적 결과로 바꿨다.

## 무엇으로 가르나

"칠했는가"가 아니라 **"대 봤는가"**다. `scanned_cells`가 그 값이다.

- `scanned >= 1, changed == 0` → 조건에 맞는 게 없었다. 정상
- `scanned == 0` → 검사할 셀이 없었다. 범위를 잘못 잡은 것이므로 실패

조건이 없는 `fill_range`·`apply_border`는 그대로 둔다. 이 둘의 `changed_cells`는
범위 크기와 같아서 0이면 범위가 잘못된 것이다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _no_match_note, _verify_step_result
from office_claw_sidecar.services.excel_result_verifier import verify_effect

HIGHLIGHT = "excel_live.highlight_by_condition"


def _effect(action: str, result: dict) -> tuple[bool, str]:
    return verify_effect(
        action=action,
        params={"target_range": "A1:A10"},
        result=result,
        service=None,
        workbook_id=None,
        sheet_name=None,
    )


class TestEffectVerifierIsTheGate:
    """`_verify_step_result`가 `verify_effect`를 먼저 부르고 실패면 즉시 반환한다.

    이쪽을 안 고치면 라우터 분기를 아무리 고쳐도 판정이 안 바뀐다 — 실제로
    처음에 라우터만 고쳤을 때 `0811-204214-after-task7`에서 `verify:failed×2 →
    replan:1`이 그대로 나왔다.
    """

    def test_zero_matches_after_a_real_scan_is_a_pass(self):
        assert _effect(HIGHLIGHT, {"scanned_cells": 40, "changed_cells": 0})[0] is True

    def test_an_empty_range_is_still_a_failure(self):
        ok, detail = _effect(HIGHLIGHT, {"scanned_cells": 0, "changed_cells": 0})
        assert ok is False
        assert "empty_target_range" in detail

    def test_an_old_result_without_scanned_cells_uses_the_old_rule(self):
        ok, detail = _effect(HIGHLIGHT, {"changed_cells": 0})
        assert ok is False
        assert "no_cells_changed" in detail

    @pytest.mark.parametrize(
        "action",
        ["excel_live.fill_range", "excel_live.apply_border", "excel_live.set_border"],
    )
    def test_unconditional_painting_still_fails_on_zero(self, action):
        assert _effect(action, {"changed_cells": 0})[0] is False
        assert _effect(action, {"changed_cells": 12})[0] is True


def _verify(action: str, result: dict) -> tuple[bool, str]:
    checked = _verify_step_result(
        action=action,
        params={"target_range": "A1:A10"},
        result=result,
        workbook_id=None,
        sheet_name=None,
    )
    if isinstance(checked, tuple):
        return bool(checked[0]), str(checked[1])
    return bool(checked), ""


@pytest.fixture(autouse=True)
def _skip_state_verification(monkeypatch):
    """이 테스트가 보는 건 사후조건 분기 하나다. 상태 재확인은 여기 관심사가 아니다."""
    import office_claw_sidecar.routers.excel_live as router

    monkeypatch.setattr(router, "get_excel_live_service", lambda: None)
    monkeypatch.setattr(router, "verify_effect", lambda **_: (True, ""))


class TestHighlightWithNoMatches:
    def test_zero_matches_after_a_real_scan_is_a_pass(self):
        ok, _ = _verify(HIGHLIGHT, {"scanned_cells": 40, "changed_cells": 0, "matched_cells": 0})
        assert ok is True

    def test_an_empty_range_is_still_a_failure(self):
        # 아무것도 검사하지 못한 것은 대상을 잘못 잡았다는 뜻이라 계속 실패다.
        ok, detail = _verify(HIGHLIGHT, {"scanned_cells": 0, "changed_cells": 0})
        assert ok is False
        assert "empty_target_range" in detail

    def test_normal_highlight_still_passes(self):
        ok, _ = _verify(HIGHLIGHT, {"scanned_cells": 40, "changed_cells": 3})
        assert ok is True

    def test_an_old_result_without_scanned_cells_uses_the_old_rule(self):
        # 옛 실행기 결과를 만나도 판정이 뒤집히면 안 된다.
        assert _verify(HIGHLIGHT, {"changed_cells": 2})[0] is True
        assert _verify(HIGHLIGHT, {"changed_cells": 0})[0] is False


class TestUnconditionalPaintingIsUnchanged:
    @pytest.mark.parametrize("action", ["excel_live.fill_range", "excel_live.apply_border"])
    def test_zero_changed_cells_stays_a_failure(self, action):
        # 조건이 없는 액션은 범위 전체를 칠하므로 0은 범위가 잘못됐다는 뜻이다.
        assert _verify(action, {"changed_cells": 0})[0] is False
        assert _verify(action, {"changed_cells": 12})[0] is True


class TestUserFacingNote:
    def test_it_says_how_many_cells_were_checked(self):
        note = _no_match_note(HIGHLIGHT, {"scanned_cells": 40, "changed_cells": 0})
        assert "조건에 맞는 셀이 없어" in note
        assert "40칸" in note

    def test_no_note_when_something_was_painted(self):
        assert _no_match_note(HIGHLIGHT, {"scanned_cells": 40, "changed_cells": 3}) == ""

    def test_no_note_for_other_actions(self):
        assert _no_match_note("excel_live.fill_range", {"changed_cells": 0}) == ""
