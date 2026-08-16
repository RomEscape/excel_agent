"""드래그한 영역이 옛 주소를 이기는가 (①).

프론트가 보내는 `context_range`는 `lastExcelRangeRef` — **직전 명령의 결과 주소**다.
사용자가 Excel에서 새로 드래그하고 "여기에 표 만들어줘"라고 해도 옛 주소가 계획에
들어갔다. 매번 "A3:J4"처럼 좌표를 부르지 않아도 되게 하려는 수정이다.

깨지면 안 되는 것: 문장에 범위를 직접 적었으면 그게 최우선이다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_selection_context import (
    decide_selection_source,
    resolve_context_range,
)


def _decide(message, context_range="B2:B9"):
    return decide_selection_source(message=message, context_range=context_range)


class TestExplicitRangeAlwaysWins:
    @pytest.mark.parametrize(
        "message",
        [
            "A1:C5 정렬해줘",
            "D2 셀에 합계 넣어줘",
            "a3:j4 범위에 표 만들어줘",
            "여기 말고 A1:B2에 써줘",  # 지시어가 있어도 명시 범위가 이긴다
        ],
    )
    def test_a_range_in_the_sentence_beats_everything(self, message):
        assert _decide(message) == "message"


class TestDeicticGoesToLiveSelection:
    @pytest.mark.parametrize(
        "message",
        [
            "여기에 표 만들어줘",
            "이 범위 정렬해줘",
            "지금 선택한 영역 합계 내줘",
            "내가 드래그한 영역에 출석부 만들어줘",
            "끌어 둔 곳에 테두리 넣어줘",
            "방금 잡은 데 색칠해줘",
            "선택해 둔 부분 굵게",
        ],
    )
    def test_pointing_words_mean_the_current_selection(self, message):
        # 옛 주소(B2:B9)가 있어도 "여기"는 지금 끌어 둔 곳을 뜻한다.
        assert _decide(message) == "selection"


class TestFallbacks:
    def test_no_context_at_all_falls_back_to_the_selection(self):
        # 새 대화라 lastExcelRangeRef가 없다. 선택이라도 알려 주는 편이 낫다.
        assert _decide("표 만들어줘", context_range=None) == "selection"
        assert _decide("표 만들어줘", context_range="  ") == "selection"

    def test_an_ordinary_command_keeps_the_given_context(self):
        # 지시어도 없고 문맥은 있다 — COM을 왕복할 이유가 없다.
        assert _decide("정렬해줘", context_range="B2:B9") == "context"


class TestResolve:
    class FakeService:
        def __init__(self, ref="C3:F20"):
            self.ref = ref
            self.calls = 0

        def get_active_selection_ref(self, workbook_id, sheet_name):
            self.calls += 1
            return self.ref

    def _resolve(self, service, message, ctx):
        return resolve_context_range(
            service,
            message=message,
            context_range=ctx,
            workbook_id=None,
            sheet_name=None,
        )

    def test_the_live_selection_replaces_the_stale_address(self):
        svc = self.FakeService("C3:F20")
        assert self._resolve(svc, "여기에 표 만들어줘", "B2:B9") == "C3:F20"

    def test_com_is_not_touched_for_ordinary_commands(self):
        # 매 턴 COM을 왕복하면 단순 명령이 느려진다.
        svc = self.FakeService()
        assert self._resolve(svc, "정렬해줘", "B2:B9") == "B2:B9"
        assert svc.calls == 0

    def test_an_explicit_range_does_not_trigger_a_lookup(self):
        svc = self.FakeService()
        assert self._resolve(svc, "A1:C5 정렬해줘", "B2:B9") == "B2:B9"
        assert svc.calls == 0

    def test_a_failed_lookup_keeps_the_original(self):
        class Boom:
            def get_active_selection_ref(self, _w, _s):
                raise RuntimeError("COM 실패")

        assert self._resolve(Boom(), "여기에 써줘", "B2:B9") == "B2:B9"

    def test_a_service_without_the_getter_keeps_the_original(self):
        assert self._resolve(object(), "여기에 써줘", "B2:B9") == "B2:B9"

    def test_an_empty_selection_keeps_the_original(self):
        assert self._resolve(self.FakeService(""), "여기에 써줘", "B2:B9") == "B2:B9"

    def test_the_address_is_normalized_to_upper_case(self):
        assert self._resolve(self.FakeService("c3:f20"), "여기에 써줘", None) == "C3:F20"
