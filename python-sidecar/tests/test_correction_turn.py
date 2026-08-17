"""정정 턴이 남의 셀을 지우던 문제.

2026-08-17 실측(실제 HTTP, 세션 이어서):

    [1] "매출 시트 A10에 서울 입력"   → A10 = '서울'
    [2] "아니 부산으로 바꿔줘"        → find_replace(find='부산', replace='')

플래너가 **찾을 말과 바꿀 말을 뒤바꿨다.** 시트에 원래 있던 B4의 '부산'이 지워지고,
정작 고치려던 A10은 그대로였다. 그리고 `replaced_cells: 1`과 함께 성공으로 보고됐다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _AMBIGUITY_SENSITIVE_SLOTS, PlanStep
from office_claw_sidecar.services.excel_correction_context import (
    build_correction_plan,
    find_replace_erases_data,
    parse_correction,
    recall_write,
    record_write,
    reset_for_tests,
)
from office_claw_sidecar.services.excel_param_binder import bind_plan_steps


@pytest.fixture(autouse=True)
def _clean():
    reset_for_tests()
    yield
    reset_for_tests()


class TestReadingTheCorrection:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("아니 부산으로 바꿔줘", "부산"),
            ("아니 부산으로", "부산"),
            ("아니라 부산으로 바꿔줘", "부산"),
            ("서울 말고 부산으로 바꿔줘", "부산"),
            ("아니 부산", "부산"),
            ("아니 12000으로 고쳐줘", "12000"),
            ("아니 대전으로 수정해줘", "대전"),
        ],
    )
    def test_it_takes_the_new_value(self, message, expected):
        assert parse_correction(message) == expected

    @pytest.mark.parametrize(
        "message",
        [
            "부산으로 바꿔줘",  # 정정 표지가 없다 — 진짜 찾아 바꾸기일 수 있다.
            "매출 시트 A10에 서울 입력",
            "아니 그거 취소해줘",
            "아니 되돌려줘",
            "아니 그거 지워줘",
            "아니 그거",  # 지시대명사는 값이 아니다.
            "",
        ],
    )
    def test_it_stays_out_of_the_way(self, message):
        assert parse_correction(message) == ""


class TestRememberingTheLastWrite:
    def test_a_single_cell_write_is_remembered(self):
        record_write("s", sheet_name="매출", address="A10", values=[["서울"]])
        last = recall_write("s")
        assert last is not None
        assert (last.sheet_name, last.cell, last.value) == ("매출", "A10", "서울")

    @pytest.mark.parametrize(
        ("address", "values"),
        [
            ("A1:D1", [["날짜", "지역", "담당자", "금액"]]),
            ("A1:A3", [["가"], ["나"], ["다"]]),
            ("", [["서울"]]),
        ],
    )
    def test_a_multi_cell_write_is_not(self, address, values):
        # 여러 칸을 한 값으로 덮는 건 정정이 아니다 — 무엇을 고치라는지 알 수 없다.
        record_write("s", sheet_name="매출", address=address, values=values)
        assert recall_write("s") is None

    def test_a_new_write_replaces_the_old_one(self):
        record_write("s", sheet_name="매출", address="A10", values=[["서울"]])
        record_write("s", sheet_name="매출", address="B2", values=[["경기"]])
        assert recall_write("s").cell == "B2"

    def test_a_multi_cell_write_clears_the_memory(self):
        record_write("s", sheet_name="매출", address="A10", values=[["서울"]])
        record_write("s", sheet_name="매출", address="A1:D1", values=[["가", "나", "다", "라"]])
        assert recall_write("s") is None

    def test_it_expires(self):
        record_write("s", sheet_name="매출", address="A10", values=[["서울"]], now=1000.0)
        assert recall_write("s", now=1200.0) is not None
        assert recall_write("s", now=1400.0) is None

    def test_sessions_do_not_share(self):
        record_write("a", sheet_name="매출", address="A10", values=[["서울"]])
        assert recall_write("b") is None

    def test_no_session_key_is_not_stored(self):
        record_write("", sheet_name="매출", address="A10", values=[["서울"]])
        assert recall_write("") is None


class TestTheCorrectionPlan:
    def _last(self):
        record_write("s", sheet_name="매출", address="A10", values=[["서울"]])
        return recall_write("s")

    def test_it_rewrites_the_cell_that_was_just_written(self):
        plan = build_correction_plan("아니 부산으로 바꿔줘", self._last())
        assert plan == [
            {
                "action": "excel_live.write_range",
                "params": {
                    "start_cell": "A10",
                    "values_2d": [["부산"]],
                    "sheet_name": "매출",
                },
                "reason": "직전에 쓴 A10을(를) '부산'로 정정",
            }
        ]

    def test_no_previous_write_means_no_plan(self):
        # 문맥이 없으면 규칙으로 풀 수 없다. 평소 경로로 보낸다.
        assert build_correction_plan("아니 부산으로 바꿔줘", None) is None

    def test_a_non_correction_means_no_plan(self):
        assert build_correction_plan("매출 합계 구해줘", self._last()) is None

    def test_the_same_value_is_a_no_op(self):
        record_write("s", sheet_name="매출", address="A10", values=[["부산"]])
        assert build_correction_plan("아니 부산으로 바꿔줘", recall_write("s")) is None


class TestEmptyReplacementIsBlocked:
    """정정이 규칙에 안 잡히고 플래너까지 가더라도, 지우는 것만은 막는다."""

    @pytest.mark.parametrize(
        ("params", "message", "blocked"),
        [
            ({"find_text": "부산", "replace_text": ""}, "아니 부산으로 바꿔줘", True),
            ({"find_text": "부산", "replace_text": ""}, "부산 지워줘", False),
            ({"find_text": "(주)", "replace_text": ""}, "(주) 빼줘", False),
            ({"find_text": "서울", "replace_text": "부산"}, "서울을 부산으로 바꿔줘", False),
            ({"find_text": "", "replace_text": ""}, "아니 부산으로 바꿔줘", False),
            ({}, "아니 부산으로", False),
        ],
    )
    def test_it_blocks_only_unrequested_deletion(self, params, message, blocked):
        assert find_replace_erases_data(params, message) is blocked

    def test_the_note_reaches_the_gate(self):
        digest = {
            "active_sheet": "매출",
            "sheets": [{
                "name": "매출", "used_range": "A1:D4",
                "columns": [{"letter": "A", "header": "날짜"}, {"letter": "B", "header": "지역"}],
            }],
        }
        step = PlanStep(
            action="excel_live.find_replace",
            params={"target_range": "__USED_RANGE__", "find_text": "부산", "replace_text": ""},
            reason="",
        )
        _bound, notes = bind_plan_steps(
            [step], digest=digest, message="아니 부산으로 바꿔줘", sheet_name=None
        )
        pairs = {
            (n.get("action"), n.get("slot")) for n in notes if n.get("status") == "unresolved"
        }
        assert pairs & _AMBIGUITY_SENSITIVE_SLOTS, "메모가 게이트까지 못 갔다 — 그대로 지운다"

    def test_the_slot_is_registered_as_sensitive(self):
        assert ("excel_live.find_replace", "replace_text") in _AMBIGUITY_SENSITIVE_SLOTS
