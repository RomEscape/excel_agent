"""Excel Live 자연어 파서 규칙 테스트."""

from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based


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

