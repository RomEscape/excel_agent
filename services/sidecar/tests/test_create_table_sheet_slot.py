"""표 생성 슬롯이 사용자가 지목한 시트를 끝까지 들고 가는지.

되묻기를 거치면 마지막 발화("3행 3열, 머리글은 …")에는 시트 언급이 없다. 그때
처음 지목한 시트를 잃어버리면 계획이 활성 시트로 떨어지고, 표 크기만큼 남의 데이터를
경고 없이 덮어쓴다. 실제로 3회 반복 전부 재현됐던 사고라 여기에 못을 박는다.
"""

from __future__ import annotations

from office_claw_sidecar.routers.excel_live import (
    ExcelLiveCommandRequest,
    _build_create_table_steps,
    _merge_create_table_slots,
    _named_sheet_in_text,
)


def _req(message: str, *, sheet_name: str) -> ExcelLiveCommandRequest:
    return ExcelLiveCommandRequest(
        message=message,
        workbook_id="C:/tmp/book.xlsx",
        sheet_name=sheet_name,
        session_id="s1",
    )


def _merge(slot, message: str, *, active_sheet: str):
    return _merge_create_table_slots(
        slot,
        hints={},
        parsed=None,
        req=_req(message, sheet_name=active_sheet),
        session_key="s1",
    )


class TestNamedSheetInText:
    def test_it_reads_a_named_sheet(self):
        assert _named_sheet_in_text("정산 시트에 표 만들어줘") == "정산"

    def test_a_demonstrative_is_not_a_name(self):
        assert _named_sheet_in_text("이 시트에 표 만들어줘") is None
        assert _named_sheet_in_text("새 시트에 표 만들어줘") is None
        assert _named_sheet_in_text("현재 시트에 표 만들어줘") is None

    def test_no_mention_is_none(self):
        assert _named_sheet_in_text("3행 3열, 머리글은 이름, 점수, 등급") is None

    def test_the_last_named_sheet_wins(self):
        # 앞쪽은 원본이다. 앞을 고르면 결과가 원본을 덮어쓴다.
        assert _named_sheet_in_text("매출 시트를 정리해서 요약 시트에 표로 만들어줘") == "요약"


class TestTheSheetSurvivesTheFollowUp:
    def test_a_named_sheet_is_remembered_across_turns(self):
        slot = _merge(None, "정산 시트에 표 만들어줘", active_sheet="평가")
        assert slot.explicit_sheet_name == "정산"

        # 되묻기 다음 턴: 시트 언급이 없고, 활성 시트는 여전히 평가다.
        slot = _merge(slot, "3행 3열, 머리글은 이름, 점수, 등급", active_sheet="평가")
        assert slot.explicit_sheet_name == "정산"

    def test_every_generated_step_targets_that_sheet(self):
        slot = _merge(None, "정산 시트에 표 만들어줘", active_sheet="평가")
        slot = _merge(slot, "3행 3열, 머리글은 이름, 점수, 등급", active_sheet="평가")

        steps = _build_create_table_steps(slot)
        assert steps, "표 생성 단계가 나와야 한다"
        for step in steps:
            assert step["params"]["sheet_name"] == "정산", (
                f"{step['action']}가 활성 시트(평가)로 떨어지면 남의 데이터를 덮어쓴다"
            )

    def test_a_later_named_sheet_replaces_the_earlier_one(self):
        slot = _merge(None, "정산 시트에 표 만들어줘", active_sheet="평가")
        slot = _merge(slot, "아니 요약 시트에 3행 3열로", active_sheet="평가")
        assert slot.explicit_sheet_name == "요약"


class TestWithoutANamedSheetNothingChanges:
    def test_no_sheet_param_is_added(self):
        slot = _merge(None, "표 만들어줘", active_sheet="평가")
        slot = _merge(slot, "3행 3열", active_sheet="평가")

        assert slot.explicit_sheet_name is None
        steps = _build_create_table_steps(slot)
        assert steps
        for step in steps:
            assert "sheet_name" not in step["params"], (
                "지목이 없으면 기존대로 하류가 활성 시트를 고르게 둔다"
            )

    def test_a_demonstrative_does_not_pin_a_sheet(self):
        slot = _merge(None, "이 시트에 표 만들어줘", active_sheet="평가")
        assert slot.explicit_sheet_name is None
