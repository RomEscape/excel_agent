"""Excel Live 자연어 파서 규칙/플래너 테스트."""

import asyncio

from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based, parse_excel_live_command


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


class _FakeLLM:
    def __init__(self, response):
        self._response = response
        self._idx = 0

    async def chat(self, messages):
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

