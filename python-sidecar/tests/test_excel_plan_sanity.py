"""계획 위생 검사 — 실행 전에 계획이 원문과 앞뒤가 맞는지.

사후조건(`excel_result_verifier`)은 "계획대로 썼는가"만 본다. 계획이 틀린 경우는 원리적으로 못 잡는다.
아래 사례는 전부 2026-08-19 결과 워크북 감사에서 **성공으로 집계된 채** 파일에 남아 있던 것들이다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_plan_sanity import check_plan_sanity


def write(start: str, values, sheet: str | None = None):
    params = {"start_cell": start, "values_2d": values}
    if sheet:
        params["sheet_name"] = sheet
    return {"action": "excel_live.write_range", "params": params}


class TestDirectiveWrittenAsData:
    @pytest.mark.parametrize(
        "message, value",
        [
            ("B35 빼기 B36 한 값을 E35에 넣어줘", "B35 빼기 B36 한 값"),
            ("E35에 B35에서 B36 뺀 값 넣어줘", "B35에서 B36 뺀 값"),
            ("F9에 B9 나누기 C9 한 값 넣어줘", "B9 나누기 C9 한 값"),
        ],
    )
    def test_a_formula_sentence_written_as_text_is_caught(self, message, value):
        issues = check_plan_sanity([write("E35", [[value]])], message=message, active_sheet="대시보드")
        assert [i.code for i in issues] == ["value_is_a_directive"], issues

    def test_a_sheet_plus_aggregate_sentence_is_caught(self):
        message = "성적부 시트 결석 합계를 B10에 넣어줘"
        issues = check_plan_sanity([write("B10", [["성적부 시트 결석 합계"]])], message=message, active_sheet="대시보드")
        assert [i.code for i in issues] == ["value_is_a_directive"], issues

    def test_a_bare_particle_value_is_caught(self):
        issues = check_plan_sanity([write("A1", [["을"]])], message="제목을 A1에 넣어줘", active_sheet="대시보드")
        assert [i.code for i in issues] == ["value_is_a_directive"], issues

    @pytest.mark.parametrize(
        "value", ["이 표 아래에", "합계 줄", "여기에", "굵게", "평균 한 줄"]
    )
    def test_a_directive_fragment_as_a_value_is_caught(self, value):
        issues = check_plan_sanity(
            [write("A8", [[value]])], message=f"넣어줘 {value}, 이 표 아래에", active_sheet="데이터"
        )
        assert [i.code for i in issues] == ["value_is_a_directive"], (value, issues)


class TestRealDataIsNotFlagged:
    @pytest.mark.parametrize(
        "message, values",
        [
            # 사람이 진짜로 '합계'라는 머리글을 쓰는 경우
            ("A8에 합계 라고 써줘", [["합계"]]),
            # 표 붙여넣기 — 값 안에 계산 낱말이 있어도 격자는 데이터다
            (
                "여기에 항목, 비고; 평균 운행시간, 산출 완료; 총계, 확인 입력해줘",
                [["항목", "비고"], ["평균 운행시간", "산출 완료"], ["총계", "확인"]],
            ),
            # 셀 주소를 닮은 자재 코드
            ("여기에 코드, 이름; D25, 철근; T100, 단열재 입력해줘", [["코드", "이름"], ["D25", "철근"], ["T100", "단열재"]]),
            ("A1에 물류 관제 대시보드 써줘", [["물류 관제 대시보드"]]),
        ],
    )
    def test_ordinary_values_pass(self, message, values):
        assert check_plan_sanity([write("A1", values)], message=message, active_sheet="데이터") == []

    def test_a_value_not_present_in_the_message_is_not_flagged(self):
        # 원문에 없는 값이면 지시문 오인이 아니다(모델이 만들어낸 요약 등은 다른 층이 본다).
        issues = check_plan_sanity(
            [write("A1", [["B1 빼기 B2 한 값"]])], message="직전 결과 그대로 넣어줘", active_sheet="데이터"
        )
        assert issues == []


class TestWritingToTheSourceSheet:
    def test_a_plan_targeting_the_sheet_named_after_the_cell_is_caught(self):
        # "B10에다 성적부 시트 …" — 셀이 먼저 나왔으므로 성적부는 원본이다.
        steps = [
            {
                "action": "excel_live.set_formula",
                "params": {"range_ref": "B10", "formula_a1": "=SUM(F2:F17)", "sheet_name": "성적부"},
            }
        ]
        issues = check_plan_sanity(steps, message="B10에다 성적부 시트 결석 다 더한 값 가져와줘", active_sheet="대시보드")
        assert [i.code for i in issues] == ["writes_to_the_source_sheet"], issues

    def test_the_same_plan_on_the_active_sheet_passes(self):
        steps = [
            {
                "action": "excel_live.set_formula",
                "params": {"range_ref": "B10", "formula_a1": "=SUM('성적부'!F2:F11)", "sheet_name": "대시보드"},
            }
        ]
        assert check_plan_sanity(steps, message="B10에다 성적부 시트 결석 다 더한 값 가져와줘", active_sheet="대시보드") == []

    def test_a_sheet_named_before_the_cell_is_a_destination(self):
        # "성적부 시트 A1에 …" — 시트가 먼저면 그 시트가 대상이다.
        steps = [
            {"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["제목"]], "sheet_name": "성적부"}}
        ]
        assert check_plan_sanity(steps, message="성적부 시트 A1에 제목 써줘", active_sheet="대시보드") == []
