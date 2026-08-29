"""매크로 계획의 참조 커버리지 검사.

2026-08-16 실측: 분해가 "Dashboard 시트 A6:A11에 서울,경기,… 입력" 단계를 빠뜨린 채
`=SUMIF(...,A6,...)`를 B6:C11에 넣었다. 기준 셀이 비어 12칸이 전부 0이 됐는데
실행은 전부 성공이라 "19단계를 마쳤습니다"로 보고됐다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_macro_coverage import (
    criteria_cells,
    idx_to_col,
    parse_rect,
    sheet_in_command,
    target_range_in_command,
)
from office_claw_sidecar.services.excel_macro_planner import validate_macro_steps

DIGEST = {
    "active_sheet": "Sales_Data",
    "sheets": [{"name": "Sales_Data", "used_range": "A1:I61", "columns": []}],
}

SUMIF_B = (
    "Dashboard 시트 B6:B11에 수식 "
    "=SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$J$2:$J$61) 적용"
)
FILL_REGIONS = "Dashboard 시트 A6:A11에 서울,경기,충청,영남,호남,강원 입력"


class TestA1Helpers:
    @pytest.mark.parametrize(
        ("ref", "expected"),
        [
            ("A1", (1, 1, 1, 1)),
            ("B6:B11", (6, 2, 11, 2)),
            ("$A$6", (6, 1, 6, 1)),
            ("C10:A1", (1, 1, 10, 3)),  # 뒤집혀 와도 정규화한다
        ],
    )
    def test_parse_rect(self, ref, expected):
        assert parse_rect(ref) == expected

    def test_parse_rect_rejects_non_a1(self):
        assert parse_rect("매출표") is None
        assert parse_rect("") is None

    def test_idx_to_col(self):
        assert idx_to_col(1) == "A"
        assert idx_to_col(27) == "AA"


class TestCommandParsing:
    def test_sheet_is_taken_from_the_sentence(self):
        assert sheet_in_command(SUMIF_B) == "Dashboard"
        assert sheet_in_command("A1에 3 입력") == ""

    def test_target_range_ignores_the_formula_part(self):
        # 수식 안의 $C$2:$C$61을 대상으로 잡으면 커버리지가 통째로 어긋난다.
        assert target_range_in_command(SUMIF_B) == "B6:B11"

    def test_target_range_of_a_plain_write(self):
        assert target_range_in_command(FILL_REGIONS) == "A6:A11"


class TestCriteriaExpansion:
    def test_a_relative_reference_flows_down_the_target_range(self):
        cells = criteria_cells("=SUMIF(X!$A$1:$A$9,A6,X!$B$1:$B$9)", parse_rect("B6:B11"))
        assert cells == [(6, 1), (7, 1), (8, 1), (9, 1), (10, 1), (11, 1)]

    def test_an_absolute_reference_does_not_flow(self):
        cells = criteria_cells("=SUMIF(X!$A$1:$A$9,$A$6,X!$B$1:$B$9)", parse_rect("B6:B11"))
        assert cells == [(6, 1)]

    def test_sheet_qualified_references_are_not_local_criteria(self):
        # Sales_Data!$C$2:$C$61은 다른 시트다. 이 시트의 기준 셀로 세면 안 된다.
        assert criteria_cells("=SUM(Sales_Data!J2:J61)", parse_rect("B3")) == []


class TestTrackerOnRealPlans:
    def _warnings(self, plan):
        steps = validate_macro_steps(plan, digest=DIGEST)
        return {s.index: s.warnings for s in steps if s.warnings}

    def test_the_missing_region_step_is_caught(self):
        plan = [
            "Dashboard 시트 만들어줘",
            "Dashboard 시트 A5에 평균주문금액 입력",
            "Dashboard 시트 A6에 수식 =AVERAGE(Sales_Data!J2:J61) 적용",
            SUMIF_B,
        ]
        warned = self._warnings(plan)
        assert 4 in warned, "빈 기준 셀을 참조하는 SUMIF를 놓쳤다"
        assert "A7" in warned[4][0]

    def test_filling_the_criteria_first_clears_the_warning(self):
        plan = ["Dashboard 시트 만들어줘", FILL_REGIONS, SUMIF_B]
        assert self._warnings(plan) == {}

    def test_a_step_cannot_satisfy_its_own_reference(self):
        # 읽기 판정이 쓰기 기록보다 먼저 와야 한다.
        plan = ["Dashboard 시트 만들어줘", "Dashboard 시트 A1에 수식 =A1+1 적용"]
        assert 2 in self._warnings(plan)

    def test_seeded_used_range_counts_as_filled(self):
        # 통합문서에 이미 있던 데이터는 앞 단계가 채우지 않아도 참조할 수 있다.
        plan = ["Sales_Data 시트 J2:J61에 수식 =E2*F2*(1-G2) 적용"]
        assert self._warnings(plan) == {}

    def test_a_new_sheet_starts_empty(self):
        plan = ["요약 시트 만들어줘", "요약 시트 B1에 수식 =A1*2 적용"]
        assert 2 in self._warnings(plan)

    def test_non_formula_steps_are_never_warned(self):
        plan = ["Dashboard 시트 만들어줘", "Dashboard 시트 A1에 총매출 입력"]
        assert self._warnings(plan) == {}

    def _coverage_warnings(self, plan):
        """참조 커버리지 경고만 골라낸다 — 시트 부재 경고는 별개 검사다."""
        return [w for ws in self._warnings(plan).values() for w in ws if "참조하는" in w]

    def test_a_pivot_marks_the_whole_output_sheet_as_filled(self):
        # 결과 표는 크기를 미리 모른다. 오탐을 내느니 통째로 채워진 것으로 둔다.
        # 규칙 파서가 집계 명령에 None을 돌려주므로 문장으로도 잡아야 한다.
        plan = [
            "집계 시트 만들어줘",
            "집계 시트에 지역별 매출 집계표 만들어줘",
            "집계 시트 D1에 수식 =B2+C2 적용",
        ]
        assert self._coverage_warnings(plan) == []

    def test_an_unknown_sheet_is_never_warned_about(self):
        # 다이제스트가 사용 범위를 안 준 시트는 비었는지 알 수 없다. 모르는 것을
        # 결함으로 단정하면 멀쩡한 계획이 경고로 뒤덮인다.
        steps = validate_macro_steps(
            ["Sales_Data 시트 J2:J61에 수식 =E2*F2 적용"],
            digest={"sheets": [{"name": "Sales_Data"}]},
        )
        assert [w for s in steps for w in s.warnings if "참조하는" in w] == []


class TestOverwriteGuard:
    """원본 데이터를 덮어쓰거나 병합으로 없애는 계획을 실행 전에 잡는다.

    2026-08-16 실측: 물류 통합문서(사용범위 A1:M201)에 분해가 낸 1단계가
    `배송_데이터 시트 A1:M201 병합해줘`였다. 201행이 한 칸으로 합쳐져 원본이 통째로
    사라졌고 뒤 단계는 MergedCell read-only로 죽었다. few-shot 예시가 J·K열이 비어 있는
    워크북 기준이라, 이미 데이터가 있는 열에도 그대로 베껴 쓴 것이다.
    """

    BIG = {
        "active_sheet": "배송_데이터",
        "sheets": [{"name": "배송_데이터", "used_range": "A1:M201", "columns": []}],
    }

    def _warn(self, plan, digest=None):
        steps = validate_macro_steps(plan, digest=digest or self.BIG)
        return {s.index: s.warnings for s in steps if s.warnings}

    def test_merging_over_data_is_flagged(self):
        warned = self._warn(["배송_데이터 시트 A1:M201 병합해줘"])
        assert 1 in warned
        assert "사라집니다" in warned[1][0]

    def test_writing_over_data_is_flagged(self):
        warned = self._warn(["배송_데이터 시트 J2:J201에 수식 =SUM(K2:K2) 적용"])
        assert 1 in warned
        assert "덮어씁니다" in warned[1][0]

    def test_plain_formatting_is_not_flagged(self):
        # 배경색·굵게는 값을 지우지 않는다. 여기 경고를 붙이면 정상 계획이 경고로 뒤덮인다.
        assert self._warn([
            "배송_데이터 시트 A1:M201 배경색 #F5F5F5로 칠해줘",
            "배송_데이터 시트 A1:M1 글자 굵게 해줘",
            "배송_데이터 시트 틀 고정해줘",
        ]) == {}

    def test_writing_outside_the_used_range_is_fine(self):
        # 빈 열에 파생 값을 만드는 건 정상 작업이다.
        assert self._warn(["배송_데이터 시트 N2:N201에 수식 =A2 적용"]) == {}

    def test_a_new_sheet_is_never_flagged(self):
        assert self._warn([
            "대시보드 시트 만들어줘",
            "대시보드 시트 A1에 물류 관제 입력",
            "대시보드 시트 A1:C1 병합해줘",
        ]) == {}

    def test_an_unknown_sheet_is_not_flagged(self):
        # 사용 범위를 모르면 덮어쓰는지도 알 수 없다.
        digest = {"active_sheet": "X", "sheets": [{"name": "X"}]}
        assert self._warn(["X 시트 A1:M201 병합해줘"], digest) == {}
