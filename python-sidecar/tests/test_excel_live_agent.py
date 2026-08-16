"""Excel Live 자연어 파서 규칙/플래너 테스트."""

import asyncio

from office_claw_sidecar.services.excel_live_agent import (
    extract_create_table_slot_hints,
    parse_command_rule_based,
    parse_excel_live_command,
)


def test_parse_highlight_command_korean():
    parsed = parse_command_rule_based("A열 데이터 중 50 이상인 셀을 노란색으로 칠해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.highlight_by_condition"
    assert parsed["params"]["target_range"] == "A:A"
    assert parsed["params"]["operator"] == ">="
    assert parsed["params"]["threshold"] == 50.0


def test_parse_header_write_command_korean():
    parsed = parse_command_rule_based("B2:D2에 헤더 써줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "B2"
    assert parsed["params"]["values_2d"] == [["헤더1", "헤더2", "헤더3"]]


def test_parse_header_write_command_english():
    parsed = parse_command_rule_based("write header in B2:D2")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "B2"
    assert parsed["params"]["values_2d"] == [["헤더1", "헤더2", "헤더3"]]


def test_parse_read_range_korean():
    parsed = parse_command_rule_based("A1:C3 범위 읽어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.read_range"
    assert parsed["params"]["range_ref"] == "A1:C3"


def test_parse_read_column_korean():
    parsed = parse_command_rule_based("B열 보여줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.read_range"
    assert parsed["params"]["range_ref"] == "B:B"


def test_parse_read_without_range_uses_active_selection():
    parsed = parse_command_rule_based("지금 선택한 범위 읽어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.read_range"
    assert parsed["params"]["range_ref"] == "__ACTIVE_SELECTION__"


def test_parse_single_cell_write_korean():
    parsed = parse_command_rule_based("C3에 120 입력해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"
    assert parsed["params"]["values_2d"] == [[120]]


def test_parse_single_cell_write_korean_with_cell_word():
    parsed = parse_command_rule_based("C3 셀에 777 입력해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"
    assert parsed["params"]["values_2d"] == [[777]]


def test_parse_single_cell_write_korean_value_phrase():
    parsed = parse_command_rule_based("C3 값을 777로 입력해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"
    assert parsed["params"]["values_2d"] == [[777]]


def test_parse_single_cell_write_korean_without_particle():
    parsed = parse_command_rule_based("C3 777 입력")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"
    assert parsed["params"]["values_2d"] == [[777]]


def test_parse_single_cell_write_without_range_uses_active_cell():
    parsed = parse_command_rule_based("777 입력해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "__ACTIVE_CELL__"
    assert parsed["params"]["values_2d"] == [[777]]


def test_parse_single_cell_clear_korean():
    parsed = parse_command_rule_based("C3 셀의 모든 내용을 지워줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"
    assert parsed["params"]["values_2d"] == [[None]]


def test_parse_range_clear_korean():
    parsed = parse_command_rule_based("B2:D3 범위를 비워줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "B2"
    assert parsed["params"]["values_2d"] == [
        [None, None, None],
        [None, None, None],
    ]


def test_parse_row_write_korean():
    parsed = parse_command_rule_based("B2:D2에 이름,수량,금액 입력")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "B2"
    assert parsed["params"]["values_2d"] == [["이름", "수량", "금액"]]


def test_parse_highlight_comparison_phrase():
    parsed = parse_command_rule_based("A열 20보다 큰 값 빨간색으로 칠해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.highlight_by_condition"
    assert parsed["params"]["target_range"] == "A:A"
    assert parsed["params"]["operator"] == ">"
    assert parsed["params"]["threshold"] == 20.0
    assert parsed["params"]["fill_color"] == "#FF0000"


def test_parse_highlight_without_range_defaults_to_wide_area():
    parsed = parse_command_rule_based("50 이상 값 노란색으로 강조해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.highlight_by_condition"
    assert parsed["params"]["target_range"] == "A:Z"
    assert parsed["params"]["operator"] == ">="
    assert parsed["params"]["threshold"] == 50.0


def test_parse_formula_template_sum_korean():
    parsed = parse_command_rule_based("C1에 A1:A10 합계 수식 넣어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.set_formula"
    assert parsed["params"]["range_ref"] == "C1"
    assert parsed["params"]["formula_a1"] == "=SUM(A1:A10)"


def test_parse_direct_formula_korean():
    parsed = parse_command_rule_based("D1:D10에 수식 =A1*2 적용해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.set_formula"
    assert parsed["params"]["range_ref"] == "D1:D10"
    assert parsed["params"]["formula_a1"] == "=A1*2"


def test_parse_formula_without_range_uses_active_selection():
    parsed = parse_command_rule_based("수식 =SUM(A1:A10) 적용해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.set_formula"
    assert parsed["params"]["range_ref"] == "__ACTIVE_SELECTION__"
    assert parsed["params"]["formula_a1"] == "=SUM(A1:A10)"


def test_parse_single_cell_write_english_compact():
    parsed = parse_command_rule_based("C7 777 set")
    assert parsed is not None
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C7"
    assert parsed["params"]["values_2d"] == [[777]]


def test_parse_formula_english_trims_trailing_set():
    parsed = parse_command_rule_based("J1 formula =SUM(A1:A10) set")
    assert parsed is not None
    assert parsed["action"] == "excel_live.set_formula"
    assert parsed["params"]["range_ref"] == "J1"
    assert parsed["params"]["formula_a1"] == "=SUM(A1:A10)"


def test_parse_select_workbook_korean():
    parsed = parse_command_rule_based("워크북 text_1.xlsx 선택")
    assert parsed is not None
    assert parsed["action"] == "excel_live.select_workbook"
    assert parsed["params"]["workbook_id"] == "text_1.xlsx"


def test_parse_save_workbook_korean():
    parsed = parse_command_rule_based("엑셀 저장해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.save_workbook"
    assert parsed["params"] == {}


def test_parse_save_workbook_english():
    parsed = parse_command_rule_based("save workbook")
    assert parsed is not None
    assert parsed["action"] == "excel_live.save_workbook"
    assert parsed["params"] == {}


def test_parse_apply_border_with_explicit_range():
    parsed = parse_command_rule_based("B2:D5 범위에 경계선 적용해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.apply_border"
    assert parsed["params"]["target_range"] == "B2:D5"
    assert parsed["params"]["weight"] == "medium"
    assert parsed["params"]["color"] == "#000000"


def test_parse_apply_border_without_range_uses_active_selection():
    parsed = parse_command_rule_based("선택한 범위 테두리 넣어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.apply_border"
    assert parsed["params"]["target_range"] == "__ACTIVE_SELECTION__"


def test_parse_apply_border_cell_range_not_misclassified_as_write():
    parsed = parse_command_rule_based("B10에 테두리 넣어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.apply_border"
    assert parsed["params"]["target_range"] == "B10"


def test_parse_create_table_with_multiplication_notation():
    parsed = parse_command_rule_based("5 * 5 표를 하나 만들어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.create_table"
    assert parsed["params"]["start_cell"] == "__ACTIVE_CELL__"
    assert parsed["params"]["rows"] == 5
    assert parsed["params"]["cols"] == 5


def test_parse_fill_range_without_threshold_uses_active_selection():
    parsed = parse_command_rule_based("표 색을 전반적으로 노랗게 칠해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.fill_range"
    assert parsed["params"]["target_range"] == "__ACTIVE_SELECTION__"
    assert parsed["params"]["fill_color"] == "#FFFF00"


def test_parse_rule_based_list_sheets():
    parsed = parse_command_rule_based("현재 시트 목록 보여줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.list_sheets"


def test_parse_rule_based_select_sheet():
    parsed = parse_command_rule_based("요약 시트로 이동해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.select_sheet"
    assert parsed["params"]["sheet_name"] == "요약"


def test_parse_rule_based_create_sheet():
    parsed = parse_command_rule_based("요약 시트 만들어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.create_sheet"
    assert parsed["params"]["sheet_name"] == "요약"


def test_parse_rule_based_rename_sheet():
    parsed = parse_command_rule_based("Sheet1 시트 이름을 Dashboard로 바꿔줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.rename_sheet"
    assert parsed["params"]["sheet_name"] == "Sheet1"
    assert parsed["params"]["new_name"] == "Dashboard"


def test_parse_rule_based_delete_sheet():
    parsed = parse_command_rule_based("Sheet1 시트 삭제해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.delete_sheet"
    assert parsed["params"]["sheet_name"] == "Sheet1"


def test_parse_column_rename_is_not_sheet_rename():
    parsed = parse_command_rule_based("Profit_Margin 열 이름을 마진율로 바꿔줘")
    assert parsed is None or parsed.get("action") != "excel_live.rename_sheet"


def test_extract_create_table_slot_hints_with_size_and_headers():
    hints = extract_create_table_slot_hints("5*5크기로 금액, 장소, 날짜, 요건, 비고 표 만들어줘")
    assert hints["table_intent"] is True
    assert hints["rows"] == 5
    assert hints["cols"] == 5
    assert hints["headers"] == ["금액", "장소", "날짜", "요건", "비고"]


def test_extract_create_table_slot_hints_does_not_treat_pivot_as_blank_grid():
    hints = extract_create_table_slot_hints("부서별 비용 집계표 만들어줘")
    assert hints["table_intent"] is False


def test_extract_create_table_slot_hints_excel_table_is_not_blank_grid():
    hints = extract_create_table_slot_hints("Sales_Data를 엑셀 표 테이블로 만들어줘")
    assert hints["table_intent"] is False
    parsed = parse_command_rule_based("Sales_Data를 엑셀 표 테이블로 만들어줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.convert_to_excel_table"


def test_parse_text_equals_highlight_not_fill():
    parsed = parse_command_rule_based("Inventory 시트에서 상태가 발주필요인 행을 노란색으로 칠해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.highlight_by_condition"
    assert parsed["params"]["value"] == "발주필요"


def test_parse_header_bold_uses_set_font():
    parsed = parse_command_rule_based("머리글을 굵게 해줘")
    assert parsed is not None
    assert parsed["action"] == "excel_live.set_font"
    assert parsed["params"]["bold"] is True


def test_parse_formula_cf_from_conditional_format_phrase():
    parsed = parse_command_rule_based("H열 발주필요면 빨간 조건부서식")
    assert parsed is not None
    assert parsed["action"] == "excel_live.apply_formula_cf"
    assert "발주필요" in parsed["params"]["formula"]


def test_extract_create_table_slot_hints_reads_unit_suffixed_sizes():
    """'4행 4열' 순서만 알아듣던 탓에 같은 질문이 반복됐다. 단위가 붙은 표기를 모두 읽는다."""
    for message in ("4열*4행", "4행*4열", "4열 4행", "가로 4 세로 4", "행 4개 열 4개", "4줄 4칸"):
        hints = extract_create_table_slot_hints(message)
        assert hints["rows"] == 4, message
        assert hints["cols"] == 4, message


def test_extract_create_table_slot_hints_strips_header_suffix_from_last_token():
    """'날짜 헤더로'가 통째로 머리글이 되던 문제."""
    hints = extract_create_table_slot_hints("금액, 장소, 날짜 헤더로 표 만들어줘")
    assert hints["headers"] == ["금액", "장소", "날짜"]


def test_extract_create_table_slot_hints_reads_a_quoted_header_list():
    """따옴표로 나열한 헤더. 쉼표로 쪼개면 앞뒤 문장이 붙고 항목 안 쉼표에서 갈라진다."""
    hints = extract_create_table_slot_hints(
        "헤더에는 '날짜', '사용 목적', '사용처', '법인카드 사용내역서 여부', "
        "'금액', '법인카드, 조교카드 이체 여부', '비용 유형' 이렇게 목록을 만들어줄 수 있어?"
    )

    assert hints["headers"] == [
        "날짜",
        "사용 목적",
        "사용처",
        "법인카드 사용내역서 여부",
        "금액",
        "법인카드, 조교카드 이체 여부",
        "비용 유형",
    ]


def test_extract_create_table_slot_hints_ignores_a_single_quoted_word():
    """따옴표 하나짜리는 헤더 나열이 아니라 인용이다."""
    hints = extract_create_table_slot_hints("'매출' 시트에 표 만들어줘")

    assert hints["headers"] == []


def test_extract_create_table_slot_hints_keeps_unit_size_out_of_headers():
    hints = extract_create_table_slot_hints("4열*3행, 제목, 사양, 비고")
    assert hints["rows"] == 3
    assert hints["cols"] == 4
    assert hints["headers"] == ["제목", "사양", "비고"]


def test_extract_create_table_slot_hints_reads_headers_announced_after_the_request():
    """지시문이 먼저 오고 나열이 뒤에 오면 첫 머리글에 문장 절반이 들어가던 문제.

    진단 배터리에서 "2행 2열 표 만들어줘. 머리글은 이름, 점수"가
    머리글 ["표 만들어줘. 머리글은 이름", "점수"]로 표를 만들고도 검증을 통과했다.
    검증기는 계획된 값과 셀을 비교하므로 계획이 망가지면 같이 통과한다.
    """
    cases = {
        "2행 2열 표 만들어줘. 머리글은 이름, 점수": ["이름", "점수"],
        "3행 4열 표 만들어줘. 헤더는 이름, 나이, 지역": ["이름", "나이", "지역"],
        "표 만들어줘. 컬럼은 날짜, 금액": ["날짜", "금액"],
        "5행 2열 표 만들어줘, 머리글은 상품, 가격": ["상품", "가격"],
        "표 하나 만들어줘\n머리글은 이름, 점수": ["이름", "점수"],
        "머리글: 이름, 점수로 표 만들어줘": ["이름", "점수"],
        "열 이름은 코드, 지역, 금액": ["코드", "지역", "금액"],
    }
    for message, expected in cases.items():
        assert extract_create_table_slot_hints(message)["headers"] == expected, message


def test_extract_create_table_slot_hints_drops_the_particle_tying_the_list_to_the_table():
    """"이름, 점수로 표 만들어줘"의 "로"는 조사지 머리글의 일부가 아니다."""
    assert extract_create_table_slot_hints("이름, 점수로 표 만들어줘")["headers"] == [
        "이름",
        "점수",
    ]
    assert extract_create_table_slot_hints("머리글은 이름, 점수인 표 만들어줘")["headers"] == [
        "이름",
        "점수",
    ]


def test_extract_create_table_slot_hints_keeps_headers_that_merely_end_in_a_particle():
    """조사를 무르지 않고 떼면 "확인"이 "확"이 된다. 한 글자만 남으면 되돌린다."""
    assert extract_create_table_slot_hints("이름, 확인 표 만들어줘")["headers"] == [
        "이름",
        "확인",
    ]
    assert extract_create_table_slot_hints("부서, 경로 표 만들어줘")["headers"] == [
        "부서",
        "경로",
    ]


def test_extract_create_table_slot_hints_keeps_a_trailing_header_word_as_a_closer():
    """"날짜 헤더로"의 "헤더"는 나열을 여는 말이 아니라 이미 끝난 나열을 닫는 말이다.

    안내말 뒤를 머리글로 삼는 규칙이 이 문장까지 삼키면 머리글이 통째로 사라진다.
    """
    assert extract_create_table_slot_hints("금액, 장소, 날짜 헤더로 표 만들어줘")["headers"] == [
        "금액",
        "장소",
        "날짜",
    ]


def test_extract_create_table_slot_hints_detects_template_without_table_word():
    hints = extract_create_table_slot_hints("프로젝트 진행 상황 체크리스트 만들어줘")
    assert hints["table_intent"] is True
    assert hints["template_key"] == "checklist"
    assert len(hints["template_headers"]) >= 4


def test_extract_create_table_slot_hints_parses_multiline_tabular_values():
    hints = extract_create_table_slot_hints(
        "아래 내용으로 표 만들어줘\n"
        "날짜\t사용 목적\t금액\n"
        "26/02/24\t학기 초 회의\t320000\n"
        "26/03/09\t개강 회의\t200000"
    )
    assert hints["table_intent"] is True
    assert hints["rows"] == 3
    assert hints["cols"] == 3
    assert hints["headers"] == ["날짜", "사용 목적", "금액"]
    assert hints["values_2d"][1] == ["26/02/24", "학기 초 회의", "320000"]


def test_extract_create_table_slot_hints_parses_compact_expense_text():
    hints = extract_create_table_slot_hints(
        "날짜 사용 목적 사용처 법인카드 사용내역서 여부 금액 법인카드, 조교카드 이체 여부 비용 유형 "
        "26/02/24 학기 초 회의 영화장 O 320,000 O 학과운영비(회의) "
        "26/03/09 개강 회의 고흥집 O 200,000 O 국제화비용(회의)"
    )
    assert hints["table_intent"] is True
    assert hints["rows"] >= 3
    assert hints["cols"] == 7
    assert hints["headers"][:3] == ["날짜", "사용 목적", "사용처"]
    assert hints["values_2d"][1][0] == "26/02/24"
    assert hints["values_2d"][2][0] == "26/03/09"


class _FakeLLM:
    def __init__(self, response):
        self._response = response
        self._idx = 0
        self.prompts = []
        self.models = []
        self.json_only_flags = []

    async def chat(self, messages, model=None, temperature=None, json_only=False, timeout=None):
        if messages and isinstance(messages[-1], dict):
            self.prompts.append(str(messages[-1].get("content", "")))
        self.models.append(str(model or ""))
        self.json_only_flags.append(json_only)
        if isinstance(self._response, list):
            out = self._response[min(self._idx, len(self._response) - 1)]
            self._idx += 1
            return out
        return self._response


def test_parse_excel_live_command_prefers_llm_action_plan():
    llm = _FakeLLM(
        '{"action_plan":[{"action":"excel_live.create_table","params":{"start_cell":"__ACTIVE_CELL__","rows":5,"cols":5},"reason":"표 생성"}],"reason":"요청 작업 계획"}'
    )
    parsed = asyncio.run(parse_excel_live_command("5 * 5 표 만들어줘", llm))
    assert parsed["action_plan"][0]["action"] == "excel_live.create_table"
    assert parsed["action_plan"][0]["params"]["rows"] == 5


def test_parse_excel_live_command_raises_when_llm_fails():
    llm = _FakeLLM("json 아님")
    try:
        asyncio.run(parse_excel_live_command("B2:D5 범위에 경계선 적용해줘", llm))
        assert False, "ValueError expected"
    except ValueError:
        pass


def test_parse_rule_based_uses_context_range_for_ambiguous_border():
    parsed = parse_command_rule_based("여기에 테두리 적용해줘", context_range="C3:E9")
    assert parsed is not None
    assert parsed["action"] == "excel_live.apply_border"
    assert parsed["params"]["target_range"] == "C3:E9"


def test_parse_excel_live_command_passes_context_to_planner():
    llm = _FakeLLM(
        '{"action_plan":[{"action":"excel_live.fill_range","params":{"target_range":"C3:E9","fill_color":"#FFFF00"},"reason":"이전 범위 색칠"}],"reason":"문맥 기반 계획"}'
    )
    parsed = asyncio.run(
        parse_excel_live_command(
            "이 범위 노랗게 칠해줘",
            llm,
            context={"context_range": "C3:E9"},
        )
    )
    assert parsed["action_plan"][0]["action"] == "excel_live.fill_range"
    assert parsed["action_plan"][0]["params"]["target_range"] == "C3:E9"


def test_parse_excel_live_command_includes_deep_reasoning_prompt():
    llm = _FakeLLM(
        '{"intent":"edit","action_plan":[{"action":"excel_live.set_formula","params":{"range_ref":"D2:D20","formula_a1":"=B2*C2"},"reason":"계산"}],"reason":"deep"}'
    )
    parsed = asyncio.run(
        parse_excel_live_command(
            "수량과 단가를 곱해서 계산해줘",
            llm,
            context={"reasoning_mode": "deep", "complexity_score": 5},
        )
    )
    assert parsed["action_plan"][0]["action"] == "excel_live.set_formula"
    prompt = llm.prompts[0]
    assert "추가 지침(Deep reasoning)" in prompt
    assert "복잡도 점수=5" in prompt


def test_parse_excel_live_command_includes_reflection_prompt():
    llm = _FakeLLM(
        '{"intent":"edit","action_plan":[{"action":"excel_live.set_formula","params":{"range_ref":"D2:D20","formula_a1":"=B2*C2"},"reason":"교정"}],"reason":"reflect"}'
    )
    parsed = asyncio.run(
        parse_excel_live_command(
            "수량과 단가를 곱해서 계산해줘",
            llm,
            context={
                "reasoning_mode": "reflect",
                "reflection_note": "intent_unknown",
                "previous_first_action": "excel_live.read_range",
            },
        )
    )
    assert parsed["action_plan"][0]["action"] == "excel_live.set_formula"
    prompt = llm.prompts[0]
    assert "추가 지침(Reflection 1회)" in prompt
    assert "reflection_note=intent_unknown" in prompt
    assert "previous_first_action=excel_live.read_range" in prompt


def test_parse_excel_live_command_includes_personalization_prompt():
    llm = _FakeLLM(
        '{"intent":"edit","action_plan":[{"action":"excel_live.apply_border","params":{"target_range":"B2:D5","line_style":"continuous","weight":"thin","color":"#D9D9D9"},"reason":"개인화 반영"}],"reason":"persona"}'
    )
    parsed = asyncio.run(
        parse_excel_live_command(
            "여기 경계를 기본으로 맞춰줘",
            llm,
            context={
                "personalization_hint": "개인화 힌트:\n- 실패 표현: \"경계 기본\" -> 기대 액션 `excel_live.apply_border`",
            },
        )
    )
    assert parsed["action_plan"][0]["action"] == "excel_live.apply_border"
    prompt = llm.prompts[0]
    assert "추가 지침(Persona memory)" in prompt
    assert "기대 액션 `excel_live.apply_border`" in prompt


def test_parse_excel_live_command_uses_context_planner_model():
    llm = _FakeLLM(
        '{"intent":"edit","action_plan":[{"action":"excel_live.fill_range","params":{"target_range":"A:A","fill_color":"#FFFF00"},"reason":"ok"}],"reason":"planner_model"}'
    )
    parsed = asyncio.run(
        parse_excel_live_command(
            "A열 노랑으로 칠해줘",
            llm,
            context={"planner_model": "officeclaw-ax7b-planner:latest"},
        )
    )
    assert parsed["action_plan"][0]["action"] == "excel_live.fill_range"
    assert llm.models
    assert llm.models[0] == "officeclaw-ax7b-planner:latest"


def test_parse_excel_live_command_replans_when_edit_intent_misclassified():
    llm = _FakeLLM(
        [
            '{"action_plan":[{"action":"excel_live.list_workbooks","params":{},"reason":"오분류"}],"reason":"first"}',
            '{"action_plan":[{"action":"excel_live.apply_border","params":{"target_range":"B2:D5","line_style":"continuous","weight":"medium","color":"#000000"},"reason":"재계획 성공"}],"reason":"second"}',
        ]
    )
    parsed = asyncio.run(parse_excel_live_command("B2:D5 범위에 경계선 적용해줘", llm))
    assert parsed["action_plan"][0]["action"] == "excel_live.apply_border"


def test_parse_excel_live_command_raises_when_highlight_intent_still_list_after_replan():
    llm = _FakeLLM(
        [
            '{"action_plan":[{"action":"excel_live.list_workbooks","params":{},"reason":"first misclassify"}],"reason":"first"}',
            '{"action_plan":[{"action":"excel_live.list_workbooks","params":{},"reason":"second misclassify"}],"reason":"second"}',
        ]
    )
    try:
        asyncio.run(parse_excel_live_command("A열에서 10 이상인 셀만 노란색 배경 적용", llm))
        assert False, "ValueError expected"
    except ValueError:
        pass


def test_parse_excel_live_command_replans_when_edit_intent_returns_read_range():
    llm = _FakeLLM(
        [
            '{"action_plan":[{"action":"excel_live.list_workbooks","params":{},"reason":"first misclassify"}],"reason":"first"}',
            '{"action_plan":[{"action":"excel_live.read_range","params":{"range_ref":"__ACTIVE_SELECTION__"},"reason":"second still passive"}],"reason":"second"}',
            '{"action_plan":[{"action":"excel_live.highlight_by_condition","params":{"target_range":"A:A","operator":">=","threshold":10,"fill_color":"#FFFF00"},"reason":"edit plan"}],"reason":"third"}',
        ]
    )
    parsed = asyncio.run(parse_excel_live_command("A열에서 10 이상인 셀만 노란색 배경 적용", llm))
    assert parsed["action_plan"][0]["action"] == "excel_live.highlight_by_condition"


def test_parse_excel_live_command_uses_intent_field_for_replan():
    llm = _FakeLLM(
        [
            '{"intent":"edit","action_plan":[{"action":"excel_live.read_range","params":{"range_ref":"__ACTIVE_SELECTION__"},"reason":"bad"}],"reason":"first"}',
            '{"intent":"edit","action_plan":[{"action":"excel_live.fill_range","params":{"target_range":"A:A","fill_color":"#FFFF00"},"reason":"good"}],"reason":"second"}',
        ]
    )
    parsed = asyncio.run(parse_excel_live_command("애매한 표현", llm))
    assert parsed["intent"] == "edit"
    assert parsed["action_plan"][0]["action"] == "excel_live.fill_range"


def test_parse_excel_live_command_returns_planner_question_as_clarify():
    """모델이 되묻기를 고르면 질문을 그대로 끌어올려 clarify로 돌려준다."""
    llm = _FakeLLM(
        '{"intent":"clarify","action_plan":[{"action":"excel_live.clarify",'
        '"params":{"question":"\'금액\'과 \'수량\' 중 어느 열 기준으로 정렬할까요?"},'
        '"reason":"기준 열 불명"}],"reason":"확인 필요"}'
    )
    parsed = asyncio.run(parse_excel_live_command("이거 정리해줘", llm))
    assert parsed["intent"] == "clarify"
    assert parsed["action"] == "excel_live.clarify"
    assert "어느 열" in parsed["follow_up_question"]


def test_parse_excel_live_command_does_not_force_edit_over_clarify():
    """편집처럼 들리는 문장이어도 되묻기를 편집 액션으로 갈아치우지 않는다."""
    llm = _FakeLLM(
        '{"intent":"clarify","action_plan":[{"action":"excel_live.clarify",'
        '"params":{"question":"어느 열을 지울까요?"},"reason":"대상 불명"}],"reason":"확인 필요"}'
    )
    parsed = asyncio.run(parse_excel_live_command("그 열 지워줘", llm))
    assert parsed["action"] == "excel_live.clarify"
    # 되묻기는 워크북을 바꾸지 않는다.
    assert parsed["intent"] == "clarify"


def test_parse_excel_live_command_rejects_clarify_mixed_with_execution():
    """되묻고 나서 실행까지 하는 계획은 반려한다 — 물어본 의미가 없어진다."""
    llm = _FakeLLM(
        [
            '{"intent":"edit","action_plan":['
            '{"action":"excel_live.clarify","params":{"question":"어느 열?"},"reason":"확인"},'
            '{"action":"excel_live.clear_range","params":{"target_range":"A:A"},"reason":"삭제"}'
            '],"reason":"혼합"}',
            '{"intent":"edit","action_plan":['
            '{"action":"excel_live.clarify","params":{"question":"어느 열?"},"reason":"확인"},'
            '{"action":"excel_live.clear_range","params":{"target_range":"A:A"},"reason":"삭제"}'
            '],"reason":"혼합"}',
        ]
    )
    try:
        asyncio.run(parse_excel_live_command("A열 지워줘", llm))
        assert False, "ValueError expected"
    except ValueError:
        pass



class TestValuelessWriteFallsThroughToClarify:
    """셀은 지목했는데 넣을 값이 없는 문장은 규칙으로 계획을 만들면 안 된다.

    2026-08-16 실측: 되묻기 다음 턴 "Sales_Data 시트 H1에 넣어줘"에서 규칙 파서가
    values_2d=[[""]]를 만들었고, 검증기는 빈 값 대 빈 셀을 같다고 보아 통과시켜
    **빈 칸을 쓰고도 "성공"으로 보고**됐다. 원 요청의 '총매출'은 사라졌다.
    """

    def test_a_cell_without_a_value_yields_no_plan(self):
        assert parse_command_rule_based("H1에 넣어줘") is None
        assert parse_command_rule_based("Sales_Data 시트 H1에 넣어줘") is None

    def test_it_does_not_leak_into_the_active_cell_rule(self):
        # 아래 "선택 셀 입력" 규칙으로 흘리면 문장 자체("H1에")를 값으로 써 버린다.
        parsed = parse_command_rule_based("H1에 넣어줘")
        assert parsed is None, f"문장을 값으로 쓰는 계획이 만들어졌다: {parsed}"

    def test_a_value_that_is_present_still_works(self):
        parsed = parse_command_rule_based("H1에 총매출 넣어줘")
        assert parsed["action"] == "excel_live.write_range"
        assert parsed["params"]["values_2d"] == [["총매출"]]
        assert parsed["params"]["start_cell"] == "H1"

    def test_clearing_is_untouched(self):
        # "지워줘"는 빈 값을 쓰는 게 정답이다. 이 경로를 막으면 안 된다.
        parsed = parse_command_rule_based("B2 지워줘")
        assert parsed["action"] == "excel_live.write_range"
        assert parsed["params"]["values_2d"] == [[None]]
