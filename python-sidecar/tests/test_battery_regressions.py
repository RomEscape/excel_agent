"""2026-08-17 전 카테고리 배터리(24케이스)가 찾은 구멍 8개의 회귀 테스트.

배터리는 실제 HTTP + 파일 검증으로 16/24 → 24/24가 됐다. 여기서는 각 구멍의
원인 지점을 단위로 고정한다. 파이프라인 전체는 배터리와
`test_rule_plan_survives_pipeline.py`가 지킨다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _looks_like_format_code
from office_claw_sidecar.services.excel_correction_context import (
    LastFormula,
    build_below_formula_plan,
)
from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based
from office_claw_sidecar.services.excel_macro_planner import looks_like_macro_request
from office_claw_sidecar.services.excel_param_binder import (
    formula_has_reversed_range,
    formula_refers_beyond_used_columns,
)

ENTRY = {"used_range": "A1:D9", "columns": [{"letter": "D", "header": "금액"}]}


class TestQuotativeParticleIsNotData:
    """ "A12에 합계 라고 입력해줘" → A12에 '합계 라고'가 들어갔다."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("A12에 합계 라고 입력해줘", "합계"),
            ("A12에 완료 이라고 적어줘", "완료"),
            ("A12에 합계 입력해줘", "합계"),
        ],
    )
    def test_the_particle_is_stripped(self, message, expected):
        step = parse_command_rule_based(message)
        assert step["params"]["values_2d"] == [[expected]]


class TestFormattingWordsAreNotCellValues:
    """ "금액에 천 단위 콤마 넣어줘"가 셀에 '금액에 천 단위 콤마'라는 텍스트로 들어갔다."""

    @pytest.mark.parametrize(
        "message",
        ["금액에 천 단위 콤마 넣어줘", "소수점 둘째 자리로 넣어줘", "테두리 넣어줘", "퍼센트로 넣어줘"],
    )
    def test_no_write_plan_is_made(self, message):
        step = parse_command_rule_based(message)
        assert step is None or step.get("action") != "excel_live.write_range", step

    def test_a_plain_value_still_writes(self):
        # 서식 어휘 차단이 정상 입력("777 입력해줘")까지 막으면 안 된다.
        step = parse_command_rule_based("777 입력해줘")
        assert step["action"] == "excel_live.write_range"
        assert step["params"]["values_2d"] == [[777]]


class TestSlashInsideValuesIsNotASeparator:
    """ "A44:E44에 토 07/05,278000,… 입력"의 "토 07/05"가 두 칸으로 갈라졌다.

    2026-08-18 ex2 재현 실측: 나열 구분자에 빗금이 들어 있어 날짜("07/05")를
    쪼갰고, 요일 7행 전체의 열이 한 칸씩 밀렸다. 쉼표가 있는 나열에서 빗금은
    값의 일부다.
    """

    def test_a_comma_list_keeps_slashes_inside_values(self):
        step = parse_command_rule_based("A44:E44에 토 07/05,278000,28,29,104% 입력")
        assert step["action"] == "excel_live.write_range"
        assert step["params"]["values_2d"] == [["토 07/05", 278000, 28, 29, "104%"]]

    def test_a_slash_only_list_still_splits(self):
        # 쉼표가 아예 없으면 빗금 나열("월/화/수")은 여전히 세 칸이다.
        step = parse_command_rule_based("A1:C1에 월/화/수 입력")
        assert step["params"]["values_2d"] == [["월", "화", "수"]]


class TestBatchRowInput:
    """한 턴에 여러 행 — 세미콜론·줄바꿈이 행 구분자다.

    2026-08-18: 검증된 문형이 "한 턴 = 한 행"뿐이라 표 하나에 수십 턴이
    들었다(85턴 재현 대화). 행 구분자를 결정적 규칙으로 받는다.
    """

    def test_semicolon_separated_rows(self):
        step = parse_command_rule_based("A2:C4에 가,1,2; 나,3,4; 다,5,6 입력")
        assert step["action"] == "excel_live.write_range"
        assert step["params"]["start_cell"] == "A2"
        assert step["params"]["values_2d"] == [["가", 1, 2], ["나", 3, 4], ["다", 5, 6]]

    def test_newline_separated_rows(self):
        step = parse_command_rule_based("A2:B3에 사과,100\n배,200 입력")
        assert step["params"]["values_2d"] == [["사과", 100], ["배", 200]]

    def test_a_single_row_is_unchanged(self):
        step = parse_command_rule_based("B2:D2에 이름,수량,금액 입력")
        assert step["params"]["values_2d"] == [["이름", "수량", "금액"]]

    def test_short_rows_are_padded_to_the_range_width(self):
        step = parse_command_rule_based("A2:C3에 가,1; 나 입력")
        assert step["params"]["values_2d"] == [["가", 1, ""], ["나", "", ""]]


class TestFormatCodeSniffing:
    """플래너가 format_code에 말 그대로 "소수점 둘째 자리"를 넣었다."""

    @pytest.mark.parametrize("code", ["#,##0", "0.00", "0%", "yyyy-mm-dd", "General", "@ "])
    def test_real_codes_pass(self, code):
        assert _looks_like_format_code(code) is True

    @pytest.mark.parametrize("code", ["소수점 둘째 자리", "천 단위 콤마", "", "예쁘게"])
    def test_prose_fails(self, code):
        assert _looks_like_format_code(code) is False


class TestDegenerateFormulaDetection:
    """=AVERAGE(A2:A1)이 A1:A8에 적용돼 날짜 열이 통째로 덮였다."""

    def test_reversed_rows_are_caught(self):
        assert formula_has_reversed_range("=AVERAGE(A2:A1)") is True
        assert formula_has_reversed_range("=SUM(D2:D9)") is False

    def test_reversed_columns_are_caught(self):
        assert formula_has_reversed_range("=SUM(D1:A1)") is True

    def test_sheet_prefixed_ranges_are_ignored(self):
        assert formula_has_reversed_range("=SUM(매출!D9:D2)") is False


class TestOutOfDataColumnDetection:
    """=AVERAGE(E:E) — 데이터는 A~D뿐인데 근거 없는 열을 집었다."""

    def test_a_column_beyond_the_data_is_flagged(self):
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", ENTRY) is True
        assert formula_refers_beyond_used_columns("=SUM(F2:F9)", ENTRY) is True

    def test_columns_inside_the_data_are_fine(self):
        assert formula_refers_beyond_used_columns("=SUM(D:D)", ENTRY) is False
        assert formula_refers_beyond_used_columns("=SUM(D2:D9)", ENTRY) is False

    def test_unknown_used_range_is_never_flagged(self):
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", None) is False
        assert formula_refers_beyond_used_columns("=AVERAGE(E:E)", {}) is False


class TestBelowCellFormulaContext:
    """ "F2에 합계" 다음의 "그 아래 칸에는 평균" — 플래너로 가면 날짜 열이 덮였다."""

    LAST = LastFormula(sheet_name="매출", cell="F2", formula="=SUM(D:D)", at_ts=0.0)

    def test_the_function_is_swapped_one_cell_below(self):
        plan = build_below_formula_plan("그 아래 칸에는 평균 넣어줘", self.LAST)
        assert plan[0]["params"]["range_ref"] == "F3"
        assert plan[0]["params"]["formula_a1"] == "=AVERAGE(D:D)"

    @pytest.mark.parametrize(
        ("message", "func"),
        [("아래 칸에 최댓값도", "MAX"), ("밑에 칸에 건수 세어줘", "COUNTA"), ("그 아래 셀에 합계", "SUM")],
    )
    def test_every_aggregate_word_works(self, message, func):
        plan = build_below_formula_plan(message, self.LAST)
        assert plan[0]["params"]["formula_a1"].startswith(f"={func}(")

    def test_no_below_mention_means_no_plan(self):
        assert build_below_formula_plan("평균 넣어줘", self.LAST) is None

    def test_no_remembered_formula_means_no_plan(self):
        assert build_below_formula_plan("그 아래 칸에 평균", None) is None

    def test_a_complex_formula_is_not_imitated(self):
        last = LastFormula(sheet_name="", cell="F2", formula="=SUM(A1:B2)+SUM(C1:C2)", at_ts=0.0)
        assert build_below_formula_plan("아래 칸에 평균", last) is None


class TestSingleGroupByIsNotAMacro:
    """ "지역별 금액 합계 집계표 만들어줘"가 16단계 매크로가 돼 산출물이 0이었다."""

    @pytest.mark.parametrize(
        "message", ["지역별 금액 합계 집계표 만들어줘", "월별 매출 요약표 만들어줘", "지역별 집계표 만들어줘"]
    )
    def test_it_stays_on_the_pivot_path(self, message):
        assert looks_like_macro_request(message) is False

    def test_a_dashboard_is_still_a_macro(self):
        assert looks_like_macro_request("매출 대시보드 만들어줘") is True


class TestFormatCodeAliases:
    """'comma'는 'mm' 때문에 코드처럼 보여 셀 서식이 말 그대로 comma가 됐다."""

    @pytest.mark.parametrize("alias", ["comma", "thousand", "percent", "currency"])
    def test_english_word_aliases_are_not_codes(self, alias):
        assert _looks_like_format_code(alias) is False


class TestNamedSheetCreation:
    """"요약이라는 이름으로 시트 추가좀" → '이름으로' 시트가 생겼다 (배터리 실측)."""

    def test_the_named_form_wins(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan("요약이라는 이름으로 시트 추가좀", None)
        assert plan and plan[0]["params"]["sheet_name"] == "요약", plan

    def test_the_trailing_name_form(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan("새로운 시트 하나 파줘 이름은 백업으로", None)
        assert plan and plan[0]["params"]["sheet_name"] == "백업", plan


class TestChartWordsBeatReadWords:
    """"클레임 비중 도넛으로 보여줘"의 '보여줘'가 단순 조회로 새서 차트가 안 생겼다."""

    def test_kind_word_plus_show_verb_becomes_a_chart(self):
        from office_claw_sidecar.routers.excel_live import (
            _chart_kind_from_message,
            _chart_step_from_message,
        )

        assert _chart_kind_from_message("클레임 비중 도넛으로 보여줘") == "doughnut"
        step = _chart_step_from_message("주문건수 추이 그래프 하나 그려줄래?")
        assert step and step["params"]["chart_type"] == "line"


class TestTypoNormalization:
    """사람의 오타("만들어조"·"함계"·"정열")를 표준형으로 — 2026-08-18 지시."""

    @pytest.mark.parametrize(
        ("raw", "fixed"),
        [
            ("지역성과 시트 만들어조", "지역성과 시트 만들어줘"),
            ("함계를 표 아래 한줄로 부탁해", "합계를 표 아래 한줄로 부탁해"),
            ("정열 좀 해줘", "정렬 좀 해줘"),
            ("테두르 둘러줘", "테두리 둘러줘"),
            ("막대 차투 그러줘", "막대 차트 그려줘"),
        ],
    )
    def test_common_typos_are_fixed(self, raw, fixed):
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        assert normalize_common_typos(raw) == fixed

    def test_clean_sentences_are_untouched(self):
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        msg = "A1:F6에 지역,주문건수 입력"
        assert normalize_common_typos(msg) == msg


class TestCommandSentencesAreNotCellValues:
    """"합계를 표 아래에 한 줄로 넣어줘"가 활성 셀 값으로 들어갔다 (GUI 실측).

    셀 지목 없이 네 낱말 넘는 문장은 값이 아니다 — 쓰지 말고 물러나서
    뒤 단계가 되묻게 한다. '라고' 인용은 예외다.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "합계를 표 아래에 한 줄로 넣어줘",
            "이 값들 전부 다른 데로 옮겨 넣어줘",
        ],
    )
    def test_long_command_clauses_back_off(self, message):
        step = parse_command_rule_based(message)
        assert step is None or step.get("action") != "excel_live.write_range" or (
            step["params"].get("start_cell") != "__ACTIVE_CELL__"
        ), step

    def test_short_values_and_quotes_still_write(self):
        step = parse_command_rule_based("완료 입력해줘")
        assert step["params"]["values_2d"] == [["완료"]]
        step2 = parse_command_rule_based("최종 점검 완료 되었음 이라고 입력해줘")
        assert step2["params"]["values_2d"] == [["최종 점검 완료 되었음"]]


class TestVerbSuffixesInBatchWrite:
    """"넣어줘"의 줘가 \b와 결합 못 해 행 쓰기가 미스 → 단일 셀 규칙이
    "A1:F6에"의 F6을 셀로 오인 → 문장 전체가 한 값 → 쉼표 재배열로 표 전체가
    조용히 오염됐다(2026-08-18 지저분판 실측 — 이번 강건성 작업 최대의 수확).
    """

    BODY = "지역,주문건수; 수도권,10452; 충청권,3892"

    @pytest.mark.parametrize("verb", ["입력", "입력해줘", "입력해주라", "넣어줘", "써줘"])
    def test_every_suffix_keeps_all_rows(self, verb):
        step = parse_command_rule_based(f"A1:B3에 {self.BODY} {verb}")
        assert step["action"] == "excel_live.write_range"
        v = step["params"]["values_2d"]
        assert len(v) == 3 and v[1][0] == "수도권", v

    def test_a_range_member_is_not_a_single_cell(self):
        # F6이 A1:F6의 일부일 때 단일 셀 쓰기가 잡으면 안 된다.
        step = parse_command_rule_based("A1:F6에 가,나,다,라,마,바 넣어줘")
        assert step["params"]["start_cell"] == "A1"
        assert len(step["params"]["values_2d"][0]) == 6


class TestGapHuntBatchOne:
    """5렌즈 사냥(2026-08-18, 70건) 중 최고 위험 6종의 회귀 핀."""

    def test_negated_save_does_not_save(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        assert _build_quick_action_plan("아직 저장하지 마", None) is None
        plan = _build_quick_action_plan("저장해줘", None)
        assert plan and plan[0]["action"] == "excel_live.save_workbook"

    def test_excluded_colors_are_skipped(self):
        from office_claw_sidecar.routers.excel_live import _quick_extract_colors

        assert _quick_extract_colors("빨간색 말고 노란색으로 칠해줘") == ["#FFFF00"]

    def test_conjugated_colors_resolve(self):
        from office_claw_sidecar.routers.excel_live import _quick_extract_colors

        assert _quick_extract_colors("F열 빨갛게 칠해줘") == ["#FF4D4F"]
        assert _quick_extract_colors("까맣게 칠해줘") == ["#000000"]

    def test_locative_da_ga_is_not_a_value(self):
        assert parse_command_rule_based("B3에다 500 넣어줘")["params"]["values_2d"] == [[500]]
        v = parse_command_rule_based("A2:C2에다가 이름,나이,점수 입력해줘")["params"]["values_2d"]
        assert v == [["이름", "나이", "점수"]]

    def test_vertical_range_takes_a_horizontal_list_downward(self):
        v = parse_command_rule_based("B2:B4에 12,000, 8,500, 9,300 입력해줘")["params"]["values_2d"]
        assert v == [[12000], [8500], [9300]]

    def test_thousand_commas_do_not_split_but_lists_do(self):
        v = parse_command_rule_based("A44:E44에 토 07/05,278000,28,29,104% 입력")["params"]["values_2d"]
        assert v == [["토 07/05", 278000, 28, 29, "104%"]]

    def test_subset_qualifiers_block_the_blanket_clear(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        assert _build_quick_action_plan("표 서식만 지워줘, 값은 그대로 두고", "A1:D9") is None
        assert _build_quick_action_plan("A1:C10에서 중복된 행은 지워줘", None) is None
        assert _build_quick_action_plan("합계 행만 지워줘", "A1:F7") is None
        # 정상 좁은 삭제(값만)는 그대로 동작해야 한다.
        plan = _build_quick_action_plan("여기 값만 지워줘", "A1:D9")
        assert plan and plan[-1]["action"] == "excel_live.clear_range"


class TestGapHuntBatchTwo:
    """5렌즈 백로그 2차 소화(2026-08-18) — 조용한 파괴 잔여 14종의 회귀 핀."""

    def test_sheet_deletion_words_do_not_clear_contents(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        q = _build_quick_action_plan("임시 시트 지워줘", None)
        assert q is None or q[0]["action"] != "excel_live.clear_range", q

    def test_font_size_without_the_word_size(self):
        from office_claw_sidecar.services.excel_live_agent import extract_font_params

        assert extract_font_params("여기 폰트 14로 바꿔줘").get("size") == 14.0
        assert extract_font_params("머리글 진하게 해줘").get("bold") is True

    @pytest.mark.parametrize(
        ("message", "value"),
        [("미납인 건 빨간색으로 칠해줘", "미납"), ("상태가 품절인 것만 빨간색으로", "품절")],
    )
    def test_colloquial_equality_forms(self, message, value):
        from office_claw_sidecar.services.excel_live_agent import parse_text_equals_condition

        assert parse_text_equals_condition(message) == value

    def test_fill_words_without_color_are_writes(self):
        r = parse_command_rule_based("C3에 100 채워줘")
        assert r["action"] == "excel_live.write_range"
        assert r["params"]["values_2d"] == [[100]]

    def test_trailing_adverbs_and_compound_clauses(self):
        r = parse_command_rule_based("A2:C2에 서울, 부산, 대구 순서대로 입력해줘")
        assert r["params"]["values_2d"] == [["서울", "부산", "대구"]]
        r2 = parse_command_rule_based("A2:C2에 서울,부산,대구 넣고 D2에 합계 써줘")
        assert r2 is None or "넣고" not in str(r2.get("params", {}).get("values_2d", ""))

    def test_grid_reshape_when_tokens_fill_the_range(self):
        r = parse_command_rule_based("A1:B2에 상품,수량,사과,3,배,5 입력해줘")
        assert r["params"]["values_2d"] == [["상품", "수량"], ["사과", 3], ["배", 5]]

    def test_compound_korean_numerals_and_minus(self):
        from office_claw_sidecar.services.korean_number import parse_condition

        assert parse_condition("매출이 3만 5천 원 이상이면") == (">=", 35000.0, False)
        assert parse_condition("수익률이 마이너스인 종목") == ("<", 0.0, False)

    def test_vague_conditions_back_off_instead_of_painting(self):
        r = parse_command_rule_based("지난주보다 급증한 센서값 빨간색으로 강조해줘")
        assert r is None or r.get("action") != "excel_live.fill_range", r

    def test_tell_me_is_a_read(self):
        r = parse_command_rule_based("B2 값 알려줘")
        assert r and r["action"] == "excel_live.read_range"


class TestThreeRoundHumanRunFindings:
    """사람 말투판 GUI 조건 ×3 라운드(2026-08-18)가 파일 검증에서 잡은 값 오염 2종."""

    def test_a_decimal_is_never_merged_with_the_next_thousand_group(self):
        # "92.6,145,0"이 92.6145로 붙어 열이 밀렸다.
        v = parse_command_rule_based("A1:F1에 강원제주,2495,2383,92.6,145,0 입력")["params"]["values_2d"]
        assert v == [["강원제주", 2495, 2383, 92.6, 145, 0]], v
        # 진짜 천 단위는 여전히 붙는다.
        assert parse_command_rule_based("B2:B4에 12,000, 8,500, 9,300 입력해줘")["params"]["values_2d"] == [[12000], [8500], [9300]]

    def test_header_row_styling_targets_only_the_first_row_of_the_context(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        # 직전 쓰기 범위 A1:E6이 컨텍스트여도 "머리글 행"은 A1:E1이다 — 표 전체가
        # 빨갛게 칠해져 상태 배지 강조와 겹쳤다.
        plan = _build_quick_action_plan("머리글 행은 빨간색 배경에 흰 글씨로 굵게 해줘", "A1:E6")
        assert {s["params"]["target_range"] for s in plan} == {"A1:E1"}, plan


class TestChartSlotKindVocab:
    """차트 슬롯의 종류 파서가 도넛·선그래프(붙여쓰기)를 몰라 종류를 말해도
    되묻기가 반복됐다(2026-08-18 ex5 대화형 각본). 어휘는 한 곳만 본다."""

    @pytest.mark.parametrize(
        ("message", "kind"),
        [("GMV로 도넛 차트 그려줘", "doughnut"), ("정시배송률 추이 선그래프로", "line"),
         ("지연건수는 막대로 그러줘", "bar"), ("클레임 비중 원형으로 그려줘", "pie")],
    )
    def test_slot_kind_matches_shared_vocab(self, message, kind):
        from office_claw_sidecar.routers.excel_live import _extract_operation_hints

        hints = _extract_operation_hints(message)
        assert hints.get("params", {}).get("chart_type") == kind, hints


class TestRealUsagePhraseBattery:
    """사용자의 실사용 문장 116개 배터리(2026-08-18) 유일 실패의 회귀 핀."""

    def test_sheet_plus_deictic_prefix_is_not_a_value(self):
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        r = parse_rangeless_row_write(
            "지역성과 시트에 이 영역에 지역,주문건수,출고건수; 수도권,10452,10158 입력해줘", "A1:C2"
        )
        assert r["params"]["values_2d"][0] == ["지역", "주문건수", "출고건수"], r

    def test_a_semicolon_list_never_becomes_one_cell_value(self):
        # 문맥이 없어도 배치 나열은 한 칸 값이 아니다 — 물러나서 되묻는다.
        step = parse_command_rule_based("지역,주문건수; 수도권,10452; 충청권,3892 입력해줘")
        assert step is None or step["params"].get("start_cell") != "__ACTIVE_CELL__" or (
            ";" not in str(step["params"].get("values_2d"))
        ), step


class TestNewScenarioAuthorTraps:
    """2026-08-19 ex9~22 각본 작성자 7명이 독립적으로 보고한 파서 함정 — 붙여넣기 앞말·No 머리글·값 안 영어."""

    @pytest.mark.parametrize(
        "text",
        [
            "이 표 옆에 이어서 No., 과제, 점수; 1, 보고서, 90 입력해줘",
            "이 옆에 No., 과제, 점수; 1, 보고서, 90 넣어줘",
            "오른쪽에 이어서 No., 과제, 점수; 1, 보고서, 90 써줘",
            "바로 밑에 No., 과제, 점수; 1, 보고서, 90 입력해줘",
            "그 다음 줄에 No., 과제, 점수; 1, 보고서, 90 입력해줘",
            "이어서 No., 과제, 점수; 1, 보고서, 90 입력해줘",
            "아 그리고 이 표 밑에 No., 과제, 점수; 1, 보고서, 90 입력해줘",
        ],
    )
    def test_locative_lead_words_are_not_values(self, text):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_rangeless_row_write,
        )

        r = parse_rangeless_row_write(normalize_common_typos(text), "A1:C2")
        assert r is not None, text
        assert r["params"]["values_2d"][0] == ["No.", "과제", "점수"], (text, r)

    def test_value_words_that_start_like_lead_words_survive(self):
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        r = parse_rangeless_row_write("이어폰, 케이블, 충전기; 3, 4, 5 입력", "A1:C2")
        assert r["params"]["values_2d"][0] == ["이어폰", "케이블", "충전기"]
        r = parse_rangeless_row_write("여기에 다음, 이전; 1, 2 입력해줘", "A1:B2")
        assert r["params"]["values_2d"][0] == ["다음", "이전"]

    def test_no_header_is_a_string_not_false(self):
        from office_claw_sidecar.services.excel_live_agent import (
            _parse_literal_value,
            parse_rangeless_row_write,
        )

        assert _parse_literal_value("No") == "No"
        assert _parse_literal_value("Yes") == "Yes"
        assert _parse_literal_value("TRUE") is True and _parse_literal_value("false") is False
        r = parse_rangeless_row_write("여기에 No, 이름, 승인; 1, 김철수, Yes 입력해줘", "A1:C2")
        assert r["params"]["values_2d"] == [["No", "이름", "승인"], [1, "김철수", "Yes"]]

    def test_english_words_inside_value_grid_are_preserved(self):
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        src = "여기에 샘플, HEK293 Cell Lysate, Flow Cytometry (T cell); 1, 2, 3 입력해줘"
        assert normalize_common_typos(src) == src

    def test_english_values_in_single_write_are_preserved_but_commands_map(self):
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        assert normalize_common_typos("A1에 Total 넣어줘") == "A1에 Total 넣어줘"
        assert normalize_common_typos("B2에 red 라고 써줘") == "B2에 red 라고 써줘"
        assert normalize_common_typos("sum 줄 넣어줘") == "합계 줄 넣어줘"
        assert normalize_common_typos("header bold 해줘") == "머리글 굵게 해줘"
        assert normalize_common_typos("첫 줄 freeze 해줘") == "첫 줄 고정 해줘"
        # 영어로만 된 명령은 영어 규칙 몫 — 건드리지 않는다.
        assert normalize_common_typos("write header in E1:G1") == "write header in E1:G1"


class TestNewScenarioRound1:
    """2026-08-19 ex9~15 1라운드 실패 7건(실제 원인 4종)의 회귀 핀."""

    def test_cell_ref_followed_by_hangul_counts_as_explicit(self):
        # "A45에"의 A45는 셀 좌표다 — `\b`는 한글 앞에서 경계가 아니라 못 잡았다(ex11 t57).
        from office_claw_sidecar.services.excel_selection_context import mentions_explicit_range

        assert mentions_explicit_range("대시보드 시트 A45에 각주 입력") is True
        assert mentions_explicit_range("A2:F6에 값 넣어줘") is True

    def test_footnote_sentence_is_a_literal_value_not_an_echo(self):
        from office_claw_sidecar.services.excel_param_binder import write_values_echo_the_request

        note = "※ AI 적용 후 수치는 추정값으로 시스템 로그 및 표본 분석 결과를 기반으로 산출되었습니다"
        assert write_values_echo_the_request({"values_2d": [[note]]}, f"대시보드 시트 A45에 {note} 입력") is False
        # 원래 잡던 되뇜은 그대로 잡는다.
        assert write_values_echo_the_request({"values_2d": [["가장 큰 매출"]]}, "F7에 가장 큰 매출 값 넣어줘") is True

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("재고 관리 시트도 하나 만들어줄래?", "재고 관리"),
            ("그리고 재고 관리 시트 만들어줘", "재고 관리"),
            ("이제 2024 실적 시트를 새로 만들어", "2024 실적"),
            ("매출 넣고 요약 시트 만들어줘", "요약"),
            ("데이터 시트는 두고 대시보드 시트 만들어줘", "대시보드"),
            ("월간 보고 시트 하나 파줘", "월간 보고"),
            ("ㅇㅇ 안전 점검 체크리스트 시트 추가", "안전 점검 체크리스트"),
        ],
    )
    def test_multiword_sheet_names_in_create(self, text, expected):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["action"] == "excel_live.create_sheet", (text, plan)
        assert plan[0]["params"]["sheet_name"] == expected, (text, plan)

    def test_multiword_sheet_mention_resolves_against_known_names(self):
        from office_claw_sidecar.services.excel_param_binder import sheet_mention_matches_known

        assert sheet_mention_matches_known("재고 관리 시트 A1에 값", "관리", ["Sheet", "재고 관리"]) is True
        assert sheet_mention_matches_known("간트 관리 시트 입력", "관리", ["Sheet", "간트관리"]) is True
        assert sheet_mention_matches_known("판매 관리 시트에", "관리", ["Sheet", "재고관리"]) is False

    def test_multiword_sheet_prefix_is_not_a_paste_value(self):
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        r = parse_rangeless_row_write("재고 관리 시트에 여기에 품목 코드, 품목명; SUP-1, 장갑 입력해줘", "A1:B2")
        assert r["params"]["values_2d"][0] == ["품목 코드", "품목명"]

    def test_sort_key_without_unit_suffix_or_by_tail_word(self):
        from office_claw_sidecar.services.excel_param_binder import _pick_sort_key

        headers = ["단과대학", "재적생 수(명)", "신입생 수(명)", "평균 평점(GPA)", "장학금 지급액(억원)"]
        assert _pick_sort_key("재적생 수 많은 순으로", headers) == "재적생 수(명)"
        assert _pick_sort_key("평점 높은 순", headers) == "평균 평점(GPA)"
        # 꼬리 낱말이 겹치면 쓰지 않는다.
        assert _pick_sort_key("건수 많은 순", ["지역", "주문 건수", "출고 건수"]) is None

    def test_cell_word_inside_values_does_not_bail(self):
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        r = parse_rangeless_row_write("여기에 ID, 시료; S1, HEK293 셀 용해물 입력해줘", "A1:B2")
        assert r["params"]["values_2d"][1] == ["S1", "HEK293 셀 용해물"]
        assert parse_rangeless_row_write("A1 셀 철수, 영희 입력해줘", "A1:B1") is None


class TestNewScenarioRound2And3:
    """2026-08-19 ex9~22 v2(말 바꾼 변형)·ex16~22 1라운드 실패의 회귀 핀."""

    @pytest.mark.parametrize(
        "text",
        ["이거 넣어줘", "음 이거 넣어줘", "복사한 거 여기에 붙여넣어줘", "방금 거 입력", "이거 여기다 써줘"],
    )
    def test_pronoun_only_write_is_not_a_value(self, text):
        from office_claw_sidecar.routers.excel_live import _BARE_WRITE_REQUEST
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        t = normalize_common_typos(text)
        assert _BARE_WRITE_REQUEST.match(t) is not None, text
        assert parse_command_rule_based(t, context_range="A1:E13") is None, text

    def test_pronoun_with_cell_is_valueless(self):
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        assert parse_command_rule_based("A1에 이거 넣어") is None
        assert parse_command_rule_based("이거 A1에 넣어줘") is None
        step = parse_command_rule_based("A1에 123 넣어줘")
        assert step and step["params"]["values_2d"] == [[123]]

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("대시보드라는 시트 하나 더 만들어주세요", "대시보드"),
            ("아 그리고 지점데이터 시트도 하나 더 만들어주세요", "지점데이터"),
            ("리소스관리 시트 하나 더 추가해주세요", "리소스관리"),
            ("시트 새로 하나 파 주세요, 이름은 Requisition으로", "Requisition"),
            ("대시보드란 이름의 시트 추가", "대시보드"),
            ("요약이라고 시트 하나 파줘", "요약"),
        ],
    )
    def test_sheet_creation_phrasings(self, text, expected):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos
        from office_claw_sidecar.services.excel_macro_planner import looks_like_macro_request

        t = normalize_common_typos(text)
        assert looks_like_macro_request(t) is False, text
        plan = _build_quick_action_plan(t, None)
        assert plan and plan[0]["action"] == "excel_live.create_sheet", (text, plan)
        assert plan[0]["params"]["sheet_name"] == expected, (text, plan)

    def test_range_then_values_then_deictic(self):
        from office_claw_sidecar.services.excel_live_agent import parse_explicit_row_write

        r = parse_explicit_row_write("A12:F17 월,객실 매출(원),부대매출(원); 2026-01,1328950000,342715000 여기 입력해줘")
        assert r["params"]["start_cell"] == "A12"
        assert r["params"]["values_2d"][1][:3] == ["2026-01", 1328950000, 342715000]
        r = parse_explicit_row_write("A1:B2 철수, 영희; 1, 2 여기에 이거 써줘")
        assert r["params"]["values_2d"] == [["철수", "영희"], [1, 2]]

    def test_a4_inside_a_range_is_not_print(self):
        from office_claw_sidecar.routers.excel_live import _extract_operation_hints

        assert _extract_operation_hints("A46:F51 표 아래에 합계를 한 줄로 넣어줘").get("intent") != "print"
        assert _extract_operation_hints("A4 가로로 인쇄해줘").get("intent") == "print"

    @pytest.mark.parametrize(
        "text, find, repl",
        [
            ("처리중을 진행중으로 바꿔줘", "처리중", "진행중"),
            ('"처리중"을 "진행중"으로 바꿔줘', "처리중", "진행중"),
            ("ML Ops를 MLOps로 찾아 바꿔줘", "ML Ops", "MLOps"),
            ("처리중 → 진행중으로 바꿔", "처리중", "진행중"),
            ("상태 열의 대기를 보류로 바꿔", "대기", "보류"),
            ("표에서 N/A는 빈칸으로 바꿔줘", "N/A", ""),
        ],
    )
    def test_find_replace_rule(self, text, find, repl):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan(text, "D60:D64")
        assert plan and plan[0]["action"] == "excel_live.find_replace", (text, plan)
        assert plan[0]["params"]["find_text"] == find and plan[0]["params"]["replace_text"] == repl

    def test_sort_without_column_is_unresolved_even_without_headers(self):
        from office_claw_sidecar.services.excel_live_executor import PlanStep
        from office_claw_sidecar.services.excel_param_binder import bind_plan_steps

        steps = [PlanStep(action="excel_live.sort_rows", params={"column": "이름", "order": "asc"}, reason="")]
        _bound, notes = bind_plan_steps(steps, digest={"sheets": []}, message="정렬 좀 해주세요", sheet_name=None)
        assert any(n.get("status") == "unresolved" and n.get("slot") == "column" for n in notes), notes
        _bound, notes = bind_plan_steps(steps, digest={"sheets": []}, message="지원자 기준 내림차순이요", sheet_name=None)
        assert not any(n.get("status") == "unresolved" for n in notes), notes


class TestNewScenarioRound4:
    """2026-08-19 4라운드(28본) 잔여 실패의 회귀 핀 — 조용한 오실행 2종 포함."""

    def test_paste_grid_is_not_retargeted_by_a_value_that_names_a_sheet(self):
        from office_claw_sidecar.services.excel_param_binder import resolve_sheet_from_message

        digest = {"sheets": [{"name": "Sheet"}, {"name": "재고 관리"}, {"name": "대시보드"}]}
        grid = "적용 영역, 자동화 과제; 예약 관리, AI; 재고 관리, 수요 예측 입력해줘"
        assert resolve_sheet_from_message(grid, digest, default="대시보드") == "대시보드"
        assert resolve_sheet_from_message("재고 관리 시트에 여기에 품목, 수량; A, 1 입력해줘", digest, default="대시보드") == "재고 관리"
        # 한 칸 쓰기도 셀 좌표 앞부분만 본다.
        assert resolve_sheet_from_message("A1에 재고 관리 현황 써줘", digest, default="대시보드") == "대시보드"
        assert resolve_sheet_from_message("재고 관리 시트 A1에 현황 써줘", digest, default="대시보드") == "재고 관리"

    def test_commandish_fragments_are_never_paste_values(self):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_rangeless_row_write,
        )

        for text in ["넣어줘 합계 줄, 이 표 아래에", "합계 줄 하나 넣어줘, 이 표 아래에", "입력해줘 평균 한 줄, 표 밑에"]:
            assert parse_rangeless_row_write(normalize_common_typos(text), "A1:G7") is None, text
        r = parse_rangeless_row_write("넣어줘 지역, 건수, 합계", "A1:C1")
        assert r["params"]["values_2d"][0] == ["지역", "건수", "합계"]

    def test_leading_filler_after_range_prefix(self):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_explicit_row_write,
        )

        m = normalize_common_typos("A5:J6 아 그리고 여기에 총 재적생 수(명),등록금 수입(억원); 23842, 428 입력해줘")
        assert m.startswith("A5:J6 여기에")
        assert parse_explicit_row_write(m) is not None

    def test_chart_kind_pie_spelled_as_won_graph(self):
        from office_claw_sidecar.routers.excel_live import _chart_kind_from_message

        assert _chart_kind_from_message("B40:B45로 원 그래프 그려줘") == "pie"

    @pytest.mark.parametrize(
        "text, find, repl",
        [("처리중이라고 된 거 전부 진행중으로 바꿔줘", "처리중", "진행중"), ("대기라고 적힌 셀은 보류로 바꿔", "대기", "보류")],
    )
    def test_find_replace_said_as_cells(self, text, find, repl):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan(text, "D60:D64")
        assert plan and plan[0]["action"] == "excel_live.find_replace"
        assert plan[0]["params"]["find_text"] == find and plan[0]["params"]["replace_text"] == repl

    def test_sheet_creation_after_a_clause(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos("ㅇㅇ 시작하자, 체크리스트 시트부터 하나 파줘"), None)
        assert plan and plan[0]["action"] == "excel_live.create_sheet" and plan[0]["params"]["sheet_name"] == "체크리스트"

    def test_cross_sheet_with_in_the_sheet_phrasing(self):
        from office_claw_sidecar.services.excel_aggregate_below import _CROSS_SHEET

        m = _CROSS_SHEET.search("B21에 간트관리 시트에 있는 예산 총합 가져와줄래")
        assert m and m.group(2) == "간트관리"


class TestWorkbookAuditFindings:
    """2026-08-19 **결과 워크북 감사**로만 드러난 조용한 오실행 — 러너는 전부 성공으로 셌다."""

    @pytest.mark.parametrize(
        "text, formula",
        [
            ("B35 빼기 B36 한 값을 E35에 넣어줘", "=B35-B36"),
            ("B35에서 B36 뺀 값을 E35에 넣어줘", "=B35-B36"),
            ("B9 나누기 C9 한 값을 F9에 넣어줘", "=B9/C9"),
            ("B7을 B6으로 나눈 값을 C7에 넣어줘", "=B7/B6"),
        ],
    )
    def test_value_first_arithmetic_becomes_a_formula_not_text(self, text, formula):
        # 셀 지목이 뒤에 오면 사칙연산 파서가 못 받아 **문장이 셀에 글자로** 박혔다.
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text))
        assert step and step["action"] == "excel_live.set_formula", (text, step)
        assert step["params"]["formula_a1"] == formula

    def test_bare_particle_never_becomes_a_cell_value(self):
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        assert parse_command_rule_based("제목을 A1에 넣어줘") is None
        step = parse_command_rule_based("매출 실적을 B2에 입력해줘")
        assert step["params"]["values_2d"] == [["매출 실적"]]

    @pytest.mark.parametrize(
        "text",
        [
            "B10에다 성적부 시트 결석 다 더한 값 가져와줘",
            "B10에 성적부 시트 결석 전부 더한 값 넣어줘",
            "B10에 성적부 시트의 결석 총합 가져와",
            "성적부 시트 결석 합계를 B10에 넣어줘",
        ],
    )
    def test_cross_sheet_aggregate_targets_the_named_cell_with_a_sheet_prefixed_formula(self, text):
        # 규칙이 못 잡으면 플래너가 **원본 시트에** 지역 SUM을 써서 학생 이름 칸을 덮었다.
        from office_claw_sidecar.services.excel_aggregate_below import build_cross_sheet_aggregate_plan

        def reader(sheet):
            return {
                "성적부": ("A1:F11", [["번호", "이름", "출석률(%)", "출석", "지각", "결석"], [1, "김", 90, 20, 1, 2]]),
                "대시보드": ("A1:A1", [["제목"]]),
            }.get(sheet)

        plan = build_cross_sheet_aggregate_plan(text, reader, sheet_names=["Sheet", "성적부", "대시보드"])
        assert plan, text
        assert plan[0]["params"]["range_ref"] == "B10"
        assert plan[0]["params"]["formula_a1"] == "=SUM('성적부'!F2:F11)"

    def test_a_source_sheet_named_after_the_destination_cell_does_not_retarget(self):
        from office_claw_sidecar.services.excel_param_binder import resolve_sheet_from_message

        digest = {"sheets": [{"name": "Sheet"}, {"name": "성적부"}, {"name": "대시보드"}]}
        assert resolve_sheet_from_message("B10에다 성적부 시트 결석 다 더한 값 가져와줘", digest, default="대시보드") == "대시보드"
        # 시트를 작업 대상으로 부른 문장은 그대로 옮긴다.
        assert resolve_sheet_from_message("성적부 시트 A1 굵게", digest, default="대시보드") == "성적부"

    def test_sheet_plus_aggregate_is_a_formula_request_not_a_literal_value(self):
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        step = parse_command_rule_based("성적부 시트 결석 합계를 B10에 넣어줘")
        assert step is None or step["action"] != "excel_live.write_range", step


class TestCrossSheetHeaderMatching:
    """2026-08-19 5라운드 워크북 감사: 괄호·공백이 든 머리글을 못 잡아 계획이 비었고,
    그 틈에 플래너가 **'=' 없는 문자열** 'SUM(렌트롤!D2:D100)'을 셀에 텍스트로 썼다."""

    def _reader(self):
        data = {
            "렌트롤": (
                "A1:Q10",
                [
                    ["자산 코드", "건물명", "층", "호수", "용도", "임차인", "전용면적", "임대면적", "시작일",
                     "만료일", "계약형태", "보증금", "기본임대료", "관리비", "총 임대료(원)", "평당임대료", "점유상태"],
                    ["A", "B", 1, 2, "c", "d", 1, 2, "e", "f", "g", 1, 2, 3, 4, 5, "h"],
                ],
            ),
            "간트관리": ("A1:F5", [["WBS", "작업", "유형", "예산(원)", "시작", "종료"], ["1", "a", "b", 100, "c", "d"]]),
            "성적부": ("A1:F11", [["번호", "이름", "출석률(%)", "출석", "지각", "결석"], [1, "김", 90, 20, 1, 2]]),
        }
        return lambda sheet: data.get(sheet)

    @pytest.mark.parametrize(
        "text, formula",
        [
            # 괄호·공백이 든 머리글
            ("B73에다 렌트롤 시트 총 임대료(원) 다 더한 값 가져와줘", "=SUM('렌트롤'!O2:O10)"),
            ("B73에 렌트롤 시트 총 임대료 합계 가져와", "=SUM('렌트롤'!O2:O10)"),
            # 단위 꼬리만 다른 경우
            ("B21에 간트관리 시트에 있는 예산 총합 가져와줄래", "=SUM('간트관리'!D2:D5)"),
            # 수량 부사가 머리글 자리를 뺏는 경우(기존 회귀)
            ("B10에다 성적부 시트 결석 다 더한 값 가져와줘", "=SUM('성적부'!F2:F11)"),
        ],
    )
    def test_headers_with_units_and_spaces_resolve(self, text, formula):
        from office_claw_sidecar.services.excel_aggregate_below import build_cross_sheet_aggregate_plan

        plan = build_cross_sheet_aggregate_plan(
            text, self._reader(), sheet_names=["Sheet", "렌트롤", "간트관리", "성적부", "대시보드"]
        )
        assert plan, text
        assert plan[0]["params"]["formula_a1"] == formula, (text, plan)

    def test_an_unknown_header_still_backs_off(self):
        # 없는 머리글을 추측해서 아무 열이나 잡으면 안 된다 — 되묻기로 가야 한다.
        from office_claw_sidecar.services.excel_aggregate_below import build_cross_sheet_aggregate_plan

        plan = build_cross_sheet_aggregate_plan(
            "B21에 간트관리 시트 없는열 합계 가져와", self._reader(), sheet_names=["Sheet", "간트관리"]
        )
        assert plan == []

    def test_an_ambiguous_partial_header_backs_off(self):
        # 부분 일치가 여럿이면 고르지 않는다.
        from office_claw_sidecar.services.excel_aggregate_below import _match_header

        assert _match_header("건수", ["지역", "주문 건수", "출고 건수"]) is None
        assert _match_header("주문 건수", ["지역", "주문 건수", "출고 건수"]) == 1


class TestNegationIsOneGate:
    """부정 판정을 규칙마다 따로 적으면 구멍이 반드시 생긴다.

    2026-08-19 블라인드 게이트: 저장 규칙이 자체 정규식("저장하지 마")만 들고 있어
    "저장 안 해도 돼요" · "저장 말고"가 그대로 실행돼 **파일이 저장됐다**.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "아직 저장 안 해도 돼요, 기다려 주세요",
            "이거 아직 저장 말고",
            "아직 저장하지 마",
            "저장 금지 아직은",
            "하지 마 저장은 아직",
            "저장은 나중에",
            "저장 보류",
            "지금은 save 하지 마세요.",
            "아즉 저장 하지맠",
            "저장은 아직 하지 말아 주세요.",
        ],
    )
    def test_a_negated_save_never_saves(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        actions = [str(s.get("action")) for s in (plan or [])]
        assert "excel_live.save_workbook" not in actions, (text, plan)

    @pytest.mark.parametrize("text", ["저장해줘", "저장", "파일 저장 부탁해", "save 해줘", "다 됐으면 저장해줘"])
    def test_a_plain_save_still_saves(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["action"] == "excel_live.save_workbook", (text, plan)


class TestRenameSheetRule:
    """2026-08-19 블라인드 게이트에서 `rename_sheet`는 **규칙 0건 · 오류 5 · 오실행 3**으로 가장 나빴다.

    기존 규칙을 실측하니 셋이 동시에 깨져 있었다:
      ① 새 이름이 조사를 먹는다 — "지역별실적으로" → '지역별실적으'라는 시트가 생긴다
      ② 지시어를 시트 이름으로 잡는다 — "이 시트 이름 …" → sheet_name='이' → 없는 시트 → ERROR
      ③ "시트명 X로" · "탭 이름 X로" · "시트 이름 X!" 는 아예 매칭되지 않는다
    """

    @pytest.mark.parametrize(
        "text, expected_old",
        [
            ("지역성과 시트 이름을 지역별실적으로 바꿔 주세요", "지역성과"),
            ("이 시트 이름 지역별실적으로 바꿔", None),
            ("시트명 지역별실적으로 변경해줴 빨ㄹ리", None),
            ("ㅇㅇ 탭 이름 지역별실적으로", None),
            ("시트 이름 지역별실적!", None),
            ("지역성과 씨트 이름 지역별실적으로 바꺼", "지역성과"),
            ("아 그리고 지역성과 시트는 지역별실적이라고 이름 바꿔놔", "지역성과"),
            ("지역성과 sheet 이름 지역별실적으로 rename 부탁드려요.", "지역성과"),
            ("지역성과라고 된 탭 이름을 지역별실적으로 고쳐 주세요", "지역성과"),
            ("이 시트 이름을 지역성과에서 지역별실적으로 바꿔 주세요", "지역성과"),
            ("지역별실적으로 바꿔줘 지역성과 시트 이름", "지역성과"),
            ("현재 시트 탭 이름 지역별실적으로 바꿔줘", None),
            ("이 시트 이름 지역별실적으로 다시 지어줘", None),
        ],
    )
    def test_the_new_name_never_swallows_a_particle(self, text, expected_old):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["action"] == "excel_live.rename_sheet", (text, plan)
        params = plan[0]["params"]
        assert params["new_name"] == "지역별실적", (text, params)
        assert params.get("sheet_name") == expected_old, (text, params)

    @pytest.mark.parametrize(
        "text",
        ["A1에 시트 이름 써줘", "B2에 시트명 입력해줘", "요약 시트 만들어줘", "지역성과 시트 삭제해줘"],
    )
    def test_it_does_not_grab_writes_or_other_sheet_actions(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        actions = [str(s.get("action")) for s in (plan or [])]
        assert "excel_live.rename_sheet" not in actions, (text, plan)


class TestAutofitRule:
    """2026-08-19 게이트: `autofit` 24문장 전부 규칙 0건 → 모델(해석 카드)로 갔다.
    결정적으로 풀리는 동작인데 모델을 부르면 느리고 흔들린다."""

    @pytest.mark.parametrize(
        "text",
        [
            "열 너비 내용에 맞게 자동으로 맞춰",
            "열너비 맞춰",
            "자동 맞춤 해줘 열 너비",
            "여기 열 폭 글자 길이에 맞게 맞춰봐",
            "열 너비 내용에 맏게 자동 조졍",
            "컬럼 폭 자동으로 마춰줘 빨ㄹ리",
            "ㅇㅇ 열 폭 자동 맞춤",
            "열 너비 autofit 부탁드려요.",
            "글자가 잘려서 ###으로 보이는 칸이 있어서요, 열 너비를 내용에 맞게 자동으로 조정해 주세요.",
            "column width 내용에 맞게 auto fit 해 주세요.",
            "칸 폭 글자 안 잘리게 알아서 맞춰 주세요",
            "열 간격 내용에 딱 맞게 넓혀줘, 보기 좋게",
        ],
    )
    def test_autofit_phrasings_resolve_to_a_rule(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["action"] == "excel_live.autofit_columns", (text, plan)

    @pytest.mark.parametrize("text", ["열 너비 15로 해줘", "행 높이 키워줘", "B열 굵게", "B열 지워줘"])
    def test_it_does_not_grab_other_column_actions(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        actions = [str(s.get("action")) for s in (plan or [])]
        assert "excel_live.autofit_columns" not in actions, (text, plan)


class TestPlanWasWrongNotExecution:
    """2026-08-19~20: 사후조건을 붙이고도 게이트가 안 움직인 이유 — 이 셋은 **계획이 틀렸고 실행은 충실했다**.

    증상만 보고 "사후조건이 잡을 수 있다"고 분류했던 것이 오판이었다. 계획 params를 열어 보고 알았다:
      · `형식=000` → 계획이 실제로 `format_code='000'`을 요청했다("1,000"의 숫자를 코드로 오인)
      · 배경 없음 → `fill_range` 단계가 계획에 **아예 없었다**('배겅' 오타로 규칙이 미스)
    """

    @pytest.mark.parametrize(
        "text",
        [
            "주문건수와 출고건수 컬럼은 1,000 단위 comma format으로 해 주세요.",
            "주문건수 1000단위 쉼표",
            "여기 주문건수 출고건수는 1000단위로 쉼표 넣어줘",
            "B2:C8은 천 단위 콤마로",
        ],
    )
    def test_a_thousands_phrase_never_becomes_a_literal_zero_code(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["action"] == "excel_live.set_number_format", (text, plan)
        assert plan[0]["params"]["format_code"] == "#,##0", (text, plan)

    @pytest.mark.parametrize(
        "text, code",
        [("D2:D6 소수 둘째 자리", "0.00"), ("정시배송률 퍼센트로 보여줘", "0.0%"), ("소수 한 자리로 표시", "0.0")],
    )
    def test_other_format_requests_are_unaffected(self, text, code):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["params"]["format_code"] == code, (text, plan)

    @pytest.mark.parametrize(
        "text",
        [
            "머리글 행 남색 배겅에 흰글씨 굵게 해주세요",
            "표 첫 줄을 남색 배경에 흰색 굴게",
            "머리글 행 남색 배경에 흰 글씨 굵게 해줘",
        ],
    )
    def test_a_single_typo_must_not_drop_the_fill_step(self, text):
        # '배겅' 한 글자가 fill_range를 계획에서 통째로 날려 "배경만 빠진" 결과가 성공으로 보고됐다.
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        actions = [str(s.get("action")) for s in (plan or [])]
        assert "excel_live.fill_range" in actions and "excel_live.set_font" in actions, (text, plan)


class TestFindReplaceMustNotGuess:
    """2026-08-20: 어제 넣은 느슨한 찾아 바꾸기 규칙이 게이트의 조용한 오실행을 **5→6건으로 늘렸다.**

    잡은 값이 엉망이었다(find='여기 표', repl='수도권을 서울권' / repl='서울권으' / repl='건 다 서울권').
    더 나쁜 건 오탐이었다 — `"매출 높은 순으로 보여줘"`(정렬)가 find='매출 높' → repl='순' **치환**이 됐다.
    잘못된 치환은 데이터를 망친다. **넓게 잡는 것보다 확실할 때만 잡는 편이 낫다.**
    """

    @pytest.mark.parametrize(
        "text",
        [
            "매출 높은 순으로 보여줘",
            "클레임 많은 순으로 정렬해줘",
            "재적생 수 많은 순으로",
            "시트 이름을 요약으로 바꿔줘",
            "배경을 파란색으로 바꿔줘",
            "B열 숫자를 퍼센트로 바꿔줘",
            "차트를 막대로 바꿔줘",
            "글꼴을 굴림으로 바꿔줘",
        ],
    )
    def test_it_never_turns_another_intent_into_a_replacement(self, text):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), "D1:D9")
        actions = [str(s.get("action")) for s in (plan or [])]
        assert "excel_live.find_replace" not in actions, (text, plan)

    @pytest.mark.parametrize(
        "text, find, repl",
        [
            ("수도권이라고 된 거 전부 서울권으로 바꿔", "수도권", "서울권"),
            ("수도권→서울권", "수도권", "서울권"),
            ("수도권 → 서울권으로 변경 부탁드려요.", "수도권", "서울권"),
            ("표에서 수도권이라는 글자를 전부 서울권으로 바꿔 주실 수 있을까요?", "수도권", "서울권"),
            ("수도권을 서울권으로 replace 부탁드려요.", "수도권", "서울권"),
            ("서울권으로 바꿔 주세요, 수도권이라고 된 거", "수도권", "서울권"),
            ("ML Ops를 MLOps로 찾아 바꿔줘", "ML Ops", "MLOps"),
            ("상태 열에서 대기 중을 보류로 바꿔줘", "대기 중", "보류"),
            ("표에서 N/A는 빈칸으로 바꿔줘", "N/A", ""),
        ],
    )
    def test_a_real_replacement_keeps_both_words_whole(self, text, find, repl):
        # 조사를 먹으면('서울권으') 시트에 없는 글자를 찾게 되고, 수량어를 먹으면 엉뚱한 값이 들어간다.
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), "D1:D9")
        assert plan and plan[0]["action"] == "excel_live.find_replace", (text, plan)
        assert plan[0]["params"]["find_text"] == find, (text, plan[0]["params"])
        assert plan[0]["params"]["replace_text"] == repl, (text, plan[0]["params"])


class TestConditionMustNotVanish:
    """조건이 사라지면 **선택 전체가 칠해진다** — 조용한 오실행 중 가장 흔한 부류였다.

    2026-08-20 게이트: `highlight_status` 6건이 `fill_range(__ACTIVE_SELECTION__)`로 떨어져
    조건 없이 전부 칠했다. 조건 파서가 "상태=대기" · "대기라고 된 칸" · "대기 상태인 칸들"을 몰랐다.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "ㅇㅇ 상태=대기 셀만 분홍 강조",
            "상태 열에서 대기라고 된 칸만 pink로 강조 부탁드려요.",
            "대기 상태인 칸들만 분홍으로 칠해서 한눈에 보이게 해줘",
            "상태 대기인거만 핑크로 강죠해 빨ㄹ리",
            "상태가 대기인 셀만 분홍색으로 표시해 주세요",
            "상태가 '대기'인 셀만 분홍색으로 칠해줘",
        ],
    )
    def test_the_equality_condition_is_parsed(self, text):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_text_equals_condition,
        )

        assert parse_text_equals_condition(normalize_common_typos(text)) == "대기", text

    @pytest.mark.parametrize(
        "text",
        [
            "ㅇㅇ 상태=대기 셀만 분홍 강조",
            "상태 열에서 대기라고 된 칸만 pink로 강조 부탁드려요.",
            "대기 상태인 칸들만 분홍으로 칠해서 한눈에 보이게 해줘",
        ],
    )
    def test_a_conditional_request_never_paints_everything(self, text):
        # fill_range(__ACTIVE_SELECTION__)로 떨어지면 조건이 사라져 전부 칠해진다.
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan, text
        assert plan[0]["action"] == "excel_live.highlight_by_condition", (text, plan)

    def test_a_plain_fill_is_still_a_plain_fill(self):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan("A1:C3 노란색으로 칠해줘", None)
        assert plan and plan[0]["action"] == "excel_live.fill_range", plan


class TestArithmeticNeverBecomesText:
    """게이트 `cell_subtract`: 사칙연산 문장이 셀에 **글자로** 박혔다(계산!D2='B2에서 C2 뺀 값, 이걸')."""

    @pytest.mark.parametrize(
        "text, formula",
        [
            ("B2에서 C2 뺀 증감 D2에 넣어롸 빨ㄹ리", "=B2-C2"),          # 결과 명사가 중간
            ("D2에 B2 minus C2 값 넣어 주세요.", "=B2-C2"),            # 영어 연산어
            ("이번주 B2에서 지난주 C2 빼서 D2에 넣어줘", "=B2-C2"),        # 셀 사이 수식어
            ("B2에서 C2 뺀 값, 이걸 D2에 넣어 주세요", "=B2-C2"),         # 쉼표 + 지시대명사
            ("B13과 B11의 차이를 D15에 기록해줘", "=B13-B11"),           # '차이'가 식의 일부
            ("E15에 A15에서 C15 뺀 값 넣어줘", "=A15-C15"),
            ("B35 빼기 B36 한 값을 E35에 넣어줘", "=B35-B36"),
            ("B7을 B6으로 나눈 값을 C7에 넣어줘", "=B7/B6"),
        ],
    )
    def test_arithmetic_phrasings_become_formulas(self, text, formula):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text))
        assert step and step["action"] == "excel_live.set_formula", (text, step)
        assert step["params"]["formula_a1"] == formula, (text, step)

    @pytest.mark.parametrize(
        "text, value",
        [("A1에 완료 라고 써줘", "완료"), ("매출 실적을 B2에 입력해줘", "매출 실적"), ("A1에 수도권에서 온 주문 넣어줘", "수도권에서 온 주문")],
    )
    def test_plain_writes_are_still_writes(self, text, value):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text))
        assert step and step["action"] == "excel_live.write_range", (text, step)
        assert step["params"]["values_2d"] == [[value]], (text, step)


class TestPasteValuePhrasings:
    """게이트 `paste_values` 12문장 중 8이 되묻기·4가 오실행이었다.

    사람은 "입력해바" · "적어 주세요" · "paste 해 주세요" · "input 부탁드려요"라고도 쓰고,
    자리를 "붙여넣은 셀들에" · "방금 잡은 칸에" · "방금 선택한 두 줄에"라고 부르며,
    앞에 상황 설명을 붙인다("… 추가해야 해서요, 지금 붙여넣은 자리에 …").
    좁게 잡으면 값이 한 칸에 박히거나("셀들에 서울") 통째로 되묻기로 샌다.
    """

    EXPECTED = [["서울", 100], ["부산", 200]]

    @pytest.mark.parametrize(
        "text",
        [
            "여기다 서울,100; 부산,200 입력해바 그대루",
            "방금 선택한 칸에 서울,100; 부산,200 이렇게 입력해 주실 수 있을까요?",
            "지역별 데이터 두 줄을 추가해야 해서요, 지금 붙여넣은 자리에 서울,100; 부산,200 순서대로 넣어 주세요",
            "선택 범위에 서울,100; 부산,200 값 paste 해 주세요.",
            "이 자리에 서울,100; 부산,200 input 부탁드려요.",
            "선택한 범위에 서울,100; 부산,200 값 입력해 주세요",
            "붙여넣은 셀들에 서울,100; 부산,200 순서대로 채워줘",
            "방금 잡은 칸에 서울,100; 부산,200 이렇게 넣어줘",
            "여기다가 서울,100; 부산,200 적어 주세요",
            "서울,100; 부산,200 입력해주새요",
            "여기에 서울,100; 부산,200 넣어도 될까요?",
            "팀장님이 서울이랑 부산 데이터를 추가하라고 하셔서요, 방금 선택한 두 줄에 서울,100; 부산,200 넣어 주세요",
        ],
    )
    def test_the_grid_lands_intact(self, text):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_rangeless_row_write,
        )

        step = parse_rangeless_row_write(normalize_common_typos(text), "A8:B9")
        assert step is not None, text
        assert step["params"]["values_2d"] == self.EXPECTED, (text, step["params"])

    @pytest.mark.parametrize("text", ["A1에 완료 넣어줘", "합계 줄 넣어줘", "여기에 넣어줘", "넣어줘 합계 줄, 이 표 아래에"])
    def test_non_grid_sentences_are_not_grabbed(self, text):
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_rangeless_row_write,
        )

        assert parse_rangeless_row_write(normalize_common_typos(text), "A8:B9") is None, text


class TestCreateSheetName:
    """2026-08-20 블라인드 게이트 `new_sheet` 24문장. 규칙이 이름을 통째로 집어야 한다.

    이전에는 24문장 중 8이 되묻기·3이 오실행이었고, 그중 하나는
    `'요약' 이름으로 시트 하나` 에서 **'요약 이름으로'라는 시트를 만들었다.**
    """

    @pytest.mark.parametrize(
        "text",
        [
            "요약 시트 하나 만들어줘",
            "요약 탭 추가해줘",
            "요약이라는 이름의 새 시트를 만들어 주세요",
            "'요약' 이름으로 시트 하나 파 주세요",
            "새 시트 하나 추가해서 요약이라고 불러줘",
            "시트 새로 하나 만들고 이름은 요약으로",
            "여기다 요약 탭 하나 추가해봐",
            "ㅇㅇ 요약 시트부터 하나 파줘",
            "요약 워크시트 생성",
            "요약 시트",
        ],
    )
    def test_extracts_the_whole_name(self, text: str) -> None:
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan, text
        assert plan[0]["action"] == "excel_live.create_sheet", text
        assert plan[0]["params"]["sheet_name"] == "요약", text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("재고 관리 시트도 하나 만들어줄래?", "재고 관리"),
            ("월간 보고 시트 하나 파줘", "월간 보고"),
            ("시트 새로 하나 파 주세요, 이름은 Requisition으로", "Requisition"),
        ],
    )
    def test_keeps_multi_word_names(self, text: str, expected: str) -> None:
        """`extend_sheet_name_leftward`가 앞 낱말까지 붙여야 하는 경우."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None)
        assert plan and plan[0]["params"]["sheet_name"] == expected, text

    @pytest.mark.parametrize(
        "text",
        [
            "A1에 시트 이름 써줘",
            "B2에 시트명 입력해줘",
            "시트 이름 요약으로 바꿔줘",
            "요약 시트 지워줘",
        ],
    )
    def test_does_not_fire_on_other_intents(self, text: str) -> None:
        """쓰기·이름변경·삭제를 생성으로 오인하면 엉뚱한 시트가 늘어난다."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None) or [{}]
        assert plan[0].get("action") != "excel_live.create_sheet", text

    def test_sheet_creation_plus_more_work_still_reaches_the_planner(self) -> None:
        """복합문은 규칙이 시트만 만들고 끝내면 안 된다 — underfit 판정으로 LLM에 넘긴다."""
        from office_claw_sidecar.routers.excel_live import (
            _build_quick_action_plan,
            _quick_plan_underfits_message,
        )

        message = "Summary 시트 만들어서 A1에 총매출이라고 쓰고 B1에 매출 합계 수식 넣어줘"
        plan = _build_quick_action_plan(message, None)
        assert plan and plan[0]["params"]["sheet_name"] == "Summary"
        assert _quick_plan_underfits_message(plan[0]["action"], message) is True


class TestTitleCellWrite:
    """2026-08-20 블라인드 게이트 `title_cell`. 한 칸에 제목 한 줄 — 어순·조사가 제각각이다.

    이전 실패:
      - `H1 물류 관제 대시보드`(조사·동사 없음) → 규칙이 못 잡아 모델로 갔다
      - `H1 셀 값에 … 넣어줘` → 값이 **'에 물류 관제 대시보드'** (조사가 값에 붙음)
      - `물류 관제 대시보드, 이걸 H1에 써주세요` → 값이 **'물류 관제 대시보드, 이걸'**
      - `H1 칸에 … 적어주세요` → 한글엔 낱말 경계가 없어 `적어(?:줘)?\\b`가 실패
    """

    @pytest.mark.parametrize(
        "text",
        [
            "H1에 물류 관제 대시보드라고 써줘",
            "H1 셀에 물류 관제 대시보드 입력",
            "H1 칸에 물류 관제 대시보드 적어주세요",
            "H1 셀에 물류 관제 대시보드 기입해주세요",
            "H1 셀 값에 물류 관제 대시보드 라고 텍스트 넣어줘",
            "물류 관제 대시보드, 이걸 H1에 써주세요",
            "제목은 물류 관제 대시보드, H1에 넣어줘",
            "H1 물류 관제 대시보드",
            "H1 셀 물류 관제 대시보드",
        ],
    )
    def test_writes_the_whole_title_into_the_named_cell(self, text: str) -> None:
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text))
        assert step and step["action"] == "excel_live.write_range", text
        assert step["params"]["start_cell"] == "H1", text
        assert step["params"]["values_2d"] == [["물류 관제 대시보드"]], text

    def test_bare_cell_text_accepts_a_colon(self) -> None:
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        step = parse_command_rule_based("A1: 분기 보고")
        assert step and step["params"] == {"start_cell": "A1", "values_2d": [["분기 보고"]]}

    @pytest.mark.parametrize(
        "text",
        [
            "B2 정렬해줘",
            "A1 굵게",
            "A1:C3 합계",
            "C3 확인 좀",
            "H1에 넣어줘",
            "A1에 이거 넣어줘",
        ],
    )
    def test_does_not_invent_a_title(self, text: str) -> None:
        """동사·명령이 붙은 문장을 제목 쓰기로 오인하면 엉뚱한 글자가 칸에 박힌다."""
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text)) or {}
        assert step.get("reason", "") != "단일 셀 값 입력 요청(조사 없는 최소 문장)", text

    def test_comma_list_still_goes_to_the_selected_cell(self) -> None:
        """`CO2 농도,512 입력` — CO2는 셀이 아니라 값이다(2026-08-19 ex4 실측).

        조사 없는 최소 문장 규칙이 이걸 가로채면 CO2 셀에 써 버린다.
        """
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        step = parse_command_rule_based("CO2 농도,512 입력")
        assert step and step["params"]["start_cell"] == "__ACTIVE_CELL__"
        assert step["params"]["values_2d"] == [["CO2 농도,512"]]

    def test_cell_clear_is_not_a_title_write(self) -> None:
        from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based

        step = parse_command_rule_based("A1 지워줘")
        assert step and step["reason"] == "셀 값 삭제 요청"
        assert step["params"]["values_2d"] == [[None]]


class TestConditionalHighlightKeepsItsCondition:
    """2026-08-20 게이트3: 조건을 못 읽으면 **A:Z를 통짜로 칠했다.**

    실측 피해: `클레임 10 넘어가는 데만 빨간색 강조` → F2(12)뿐 아니라 F3(5)도 빨강.
    사용자는 "~만"이라고 말했는데 규칙은 전부 칠하고 성공으로 보고했다.
    """

    @pytest.mark.parametrize(
        ("text", "operator", "threshold"),
        [
            ("클레임 10 넘어가는 데만 빨간색 강조", ">", 10.0),
            ("클레임10↑빨강", ">", 10.0),
            ("클레임 10 넘는 셀 빨갛게", ">", 10.0),
            ("지연건수 5 웃도는 값 노랗게 칠해줘", ">", 5.0),
            ("재고 3 밑도는 칸 빨간색", "<", 3.0),
            ("점수 60 못 미치는 셀 회색으로 강조", "<", 60.0),
            ("매출 100 이상인 셀 초록색 강조", ">=", 100.0),
            ("반품 2 이하인 셀 파란색 강조", "<=", 2.0),
            ("클레임 10↓ 파랑", "<", 10.0),
        ],
    )
    def test_the_threshold_survives(self, text: str, operator: str, threshold: float) -> None:
        # 라우터가 실제로 쓰는 순서 그대로 본다 — 빠른 계획이 먼저고, 없으면 에이전트 규칙이다.
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        clean = normalize_common_typos(text)
        plan = _build_quick_action_plan(clean, None)
        step = plan[0] if plan else parse_command_rule_based(clean)
        assert step, text
        assert step["action"] == "excel_live.highlight_by_condition", (text, step["action"])
        assert step["params"]["operator"] == operator, text
        assert float(step["params"]["threshold"]) == threshold, text

    @pytest.mark.parametrize(
        "text",
        [
            "상태 대기 분홍 강조!",
            "지연된 것만 빨갛게",
            "이상한 값만 노랗게 칠해줘",
        ],
    )
    def test_an_unparseable_condition_does_not_paint_everything(self, text: str) -> None:
        """조건을 못 만들면 **칠하지 않는다** — 물러나면 해석 카드/되묻기가 받는다."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        clean = normalize_common_typos(text)
        plan = _build_quick_action_plan(clean, None)
        step = (plan[0] if plan else parse_command_rule_based(clean)) or {}
        if step.get("action") == "excel_live.fill_range":
            target = str(step["params"].get("target_range", ""))
            raise AssertionError(f"조건을 잃고 통짜로 칠한다: {text} → {target}")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("A1:C3 노란색으로 칠해줘", "A1:C3"),
            ("표 전체를 노랗게 칠해줘", None),
        ],
    )
    def test_an_explicit_or_broad_fill_still_works(self, text: str, expected) -> None:
        """명시 범위·명시적 전체 표현이 있으면 통짜 칠은 정상 동작이다."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        clean = normalize_common_typos(text)
        plan = _build_quick_action_plan(clean, None)
        step = plan[0] if plan else parse_command_rule_based(clean)
        assert step and step["action"] == "excel_live.fill_range", text
        if expected:
            assert step["params"]["target_range"] == expected, text


class TestGate4Regressions:
    """게이트4가 잡은 것들. 전부 조용한 오실행이었다(카드도 되묻기도 없었다)."""

    @pytest.mark.parametrize(
        "text",
        [
            "H1에 물류 관제 대시보드",
            "H1에는 물류 관제 대시보드",
            "H1에다 물류 관제 대시보드",
            "아 그리고 H1에는 물류 관제 대시보드 라고 제목 박아",
        ],
    )
    def test_a_locative_particle_never_lands_in_the_value(self, text: str) -> None:
        """`H1에 물류 관제 대시보드` → 셀에 **'에 물류 관제 대시보드'**가 써졌다."""
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_command_rule_based,
        )

        step = parse_command_rule_based(normalize_common_typos(text))
        assert step and step["params"]["start_cell"] == "H1", text
        assert step["params"]["values_2d"] == [["물류 관제 대시보드"]], text

    def test_charts_only_when_the_data_must_stay(self) -> None:
        """`차트 전부 지워 주세요, 데이터는 그데로 두시고요.` → 표까지 지워졌다.

        오타('그데로')로 보호가 꺼졌고, 고친 뒤에는 한정사 가드가 먼저 걸려
        **아무것도 안 하게** 됐다. 정답은 차트 삭제 하나다.
        """
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        text = normalize_common_typos("차트 전부 지워 주세요, 데이터는 그데로 두시고요.")
        plan = _build_quick_action_plan(text, "A1:F6")
        assert plan and [s["action"] for s in plan] == ["excel_live.delete_charts"], plan

    def test_a_glued_range_is_split_only_when_merging(self) -> None:
        """`H1M1병합` → 콜론이 없어 붙여넣기 범위(A1:F6)를 병합했다."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos("H1M1병합"), "A1:F6")
        assert plan and plan[0]["params"]["target_range"] == "H1:M1", plan
        # 병합 문맥이 아니면 값이 망가지면 안 된다.
        assert normalize_common_typos("A1에 AB12CD34 입력") == "A1에 AB12CD34 입력"

    def test_the_average_typo_is_not_read_as_a_sum(self) -> None:
        """'평규도'가 '합계'로 읽혀 평균 대신 합계 줄을 또 썼다."""
        from office_claw_sidecar.services.excel_aggregate_below import match_aggregate_below
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        assert match_aggregate_below(normalize_common_typos("합계 아래 줄에 평규도 넣어주세요")) == (
            "AVERAGE",
            "평균",
        )

    @pytest.mark.parametrize(
        "text",
        [
            "표 첫 줄을 남색 배경에 흰색 굵은 글시로 바꿔 주세요.",
            "맨 위 제목 줄 눈에 띄게 남색 칸에 흰 글씨 진하게 해줘",
            "머리글 행은 남색 배경에 흰 글씨로 굵게 해줘",
        ],
    )
    def test_the_header_gets_both_the_fill_and_the_bold(self, text: str) -> None:
        """셋 중 둘만 적용되던 문장들 — 어미('굵은')와 색 어휘('흰')가 두 곳에서 갈라져 있었다."""
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        plan = _build_quick_action_plan(normalize_common_typos(text), None) or []
        actions = [s["action"] for s in plan]
        assert "excel_live.fill_range" in actions, (text, plan)
        assert "excel_live.set_font" in actions, (text, plan)
        fill = next(s for s in plan if s["action"] == "excel_live.fill_range")
        font = next(s for s in plan if s["action"] == "excel_live.set_font")
        assert fill["params"]["fill_color"] == "#002060", text
        assert fill["params"]["target_range"] == "1:1", text
        assert font["params"].get("bold") is True, text


class TestHighlightScopedToHeaderColumn:
    """조건부 강조의 **대상 열**을 머리글로 확정한다(2026-08-20 게이트5).

    게이트5에 남은 조건부 강조 실패 9건 중 7건은 조건이 이미 정확했다.
    `클레임이 10보다 큰 셀만 빨간색으로 칠혀 주세요.` → 규칙이 `> 10`까지 읽어 놓고
    범위만 `__ACTIVE_SELECTION__`이라, 플래너로 넘어가 F2 대신 **F3**이 빨개졌다.
    """

    DIGEST = {
        "active_sheet": "지역성과",
        "sheets": [
            {
                "name": "지역성과",
                "used_range": "A1:F6",
                "columns": [
                    {"letter": "A", "header": "지역"},
                    {"letter": "B", "header": "주문건수"},
                    {"letter": "C", "header": "출고건수"},
                    {"letter": "D", "header": "정시배송률"},
                    {"letter": "E", "header": "지연건수"},
                    {"letter": "F", "header": "클레임"},
                ],
            }
        ],
    }

    def _plan(self, **params):
        base = {"target_range": "__ACTIVE_SELECTION__", "operator": ">", "threshold": 10.0}
        base.update(params)
        return [{"action": "excel_live.highlight_by_condition", "params": base}]

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("클레임이 10보다 큰 셀만 빨간색으로 칠혀 주세요.", "F2:F6"),
            ("클레임10↑빨강", "F2:F6"),
            ("지연건수 3 넘는 칸 빨갛게", "E2:E6"),
            ("정시배송률 95 밑도는 셀 노랗게", "D2:D6"),
        ],
    )
    def test_the_named_header_decides_the_column(self, message: str, expected: str) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_highlight_to_header_column

        plan = self._plan()
        assert _scope_highlight_to_header_column(plan, message, self.DIGEST) == expected
        assert plan[0]["params"]["target_range"] == expected

    def test_an_explicit_range_is_left_alone(self) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_highlight_to_header_column

        plan = self._plan(target_range="F2:F6")
        assert _scope_highlight_to_header_column(plan, "F2:F6에서 10 넘는 것", self.DIGEST) == ""
        assert plan[0]["params"]["target_range"] == "F2:F6"

    @pytest.mark.parametrize("message", ["10 넘는 셀 빨갛게", "건수 10 넘는 셀 빨갛게"])
    def test_an_unresolvable_header_stays_with_the_planner(self, message: str) -> None:
        """머리글이 없거나 여러 개에 걸리면 추측하지 않는다 — 예전처럼 플래너 몫이다.

        '건수'는 주문건수·출고건수·지연건수 셋에 걸린다.
        """
        from office_claw_sidecar.routers.excel_live import _scope_highlight_to_header_column

        assert _scope_highlight_to_header_column(self._plan(), message, self.DIGEST) == ""

    def test_other_actions_are_untouched(self) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_highlight_to_header_column

        plan = [{"action": "excel_live.fill_range", "params": {"target_range": "__ACTIVE_SELECTION__"}}]
        assert _scope_highlight_to_header_column(plan, "클레임 빨갛게", self.DIGEST) == ""


class TestFormatCodeDecimalPlaces:
    """`0.1`은 표시 형식이 아니라 "소수 한 자리"라는 뜻이다(2026-08-20 게이트4·5).

    엑셀에서 `.` 뒤의 `1`은 자릿수가 아니라 리터럴이라 97.14가 `97.11`로 보인다.
    `percent_format` 5문장이 전부 이 값으로 틀렸다.
    """

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("0.1", "0.0"),
            ("0.2", "0.00"),
            ("0.3", "0.000"),
            ("#,##0.1", "#,##0.0"),
            ("0.0", "0.0"),
            ("0.00", "0.00"),
            ("#,##0", "#,##0"),
            ("0.0%", "0.0%"),
        ],
    )
    def test_a_digit_count_is_read_as_places(self, given: str, expected: str) -> None:
        from office_claw_sidecar.services.excel_live_plan_validator import _normalize_number_format

        assert _normalize_number_format(given) == expected


class TestPlanSanityCatchesPreamblesAndCircularFormulas:
    """게이트4·5가 드러낸 두 부류 — 둘 다 사후조건으로는 못 잡는다."""

    def test_a_preamble_is_not_a_value(self) -> None:
        """`새로 데이터를 받아서 다시 넣어야 해서요` 가 A1에 그대로 박혔다."""
        from office_claw_sidecar.services.excel_plan_sanity import check_plan_sanity

        issues = check_plan_sanity(
            [
                {
                    "action": "excel_live.write_range",
                    "params": {"start_cell": "A1", "values_2d": [["새로 데이터를 받아서 다시 넣어야 해서요"]]},
                }
            ],
            message="새로 데이터를 받아서 다시 넣어야 해서요, 지금 붙여넣은 표 안의 값을 전부 지워 주세요.",
        )
        assert [i.code for i in issues] == ["value_is_a_directive"]

    def test_a_formula_that_refers_to_its_own_cell_is_caught(self) -> None:
        """`요약!A2 = =SUM(A2:A2)` — 엑셀은 0을 보여 주고 사후조건은 통과시킨다."""
        from office_claw_sidecar.services.excel_plan_sanity import check_plan_sanity

        issues = check_plan_sanity(
            [{"action": "excel_live.set_formula", "params": {"range_ref": "A2", "formula_a1": "=SUM(A2:A2)"}}],
            message="A2에 지역성과 시트 주문건수 합계를 수식으로 넣어 주세요",
        )
        assert [i.code for i in issues] == ["formula_refers_to_itself"]

    @pytest.mark.parametrize(
        ("cell", "formula"),
        [
            ("A2", "=SUM(지역성과!B2:B6)"),
            ("B7", "=SUM(B2:B6)"),
            ("B8", "=AVERAGE(B2:B7)"),
        ],
    )
    def test_ordinary_formulas_pass(self, cell: str, formula: str) -> None:
        from office_claw_sidecar.services.excel_plan_sanity import check_plan_sanity

        issues = check_plan_sanity(
            [{"action": "excel_live.set_formula", "params": {"range_ref": cell, "formula_a1": formula}}],
            message="합계 넣어줘",
        )
        assert issues == []


class TestValueEqualsHighlightAndFormulaHead:
    """게이트6이 드러낸 세 결함 — 셋 다 표를 통째로 망친다."""

    STATUS_DIGEST = {
        "active_sheet": "지연경고",
        "sheets": [
            {
                "name": "지연경고",
                "used_range": "A1:D5",
                "columns": [
                    {"letter": "A", "header": "운송장"},
                    {"letter": "B", "header": "구간"},
                    {"letter": "C", "header": "지연시간"},
                    {"letter": "D", "header": "상태"},
                ],
                "sample_rows": [
                    ["T1", "서울", "2", "대기"],
                    ["T2", "부산", "1", "완료"],
                    ["T3", "대구", "3", "대기"],
                ],
            }
        ],
    }

    @pytest.mark.parametrize(
        "formula",
        ["=SUM(B2:B6)", "=B2-C3", "=100", "=(A1+B1)/2", "=지역성과!B2", '=IF(B2>=70,"통과","미달")', "=-5"],
    )
    def test_real_formulas_are_recognized(self, formula: str) -> None:
        from office_claw_sidecar.routers.excel_live import _looks_like_a_formula

        assert _looks_like_a_formula(formula) is True, formula

    @pytest.mark.parametrize("text", ["=대기", "=완료", "=진행중"])
    def test_a_korean_word_after_equals_is_not_a_formula(self, text: str) -> None:
        """`상태=대기`의 '='는 같다는 말이다.

        2026-08-20 게이트6: `ㅇㅇ 상태=대기 셀만 분홍 강조`가
        set_formula(A1:D5, '=대기')가 되어 **표 전체를 수식으로 덮었다.**
        """
        from office_claw_sidecar.routers.excel_live import _looks_like_a_formula

        assert _looks_like_a_formula(text) is False, text

    @pytest.mark.parametrize(
        "message",
        [
            "대기만 분홍",
            "상태 대기 분홍 강조!",
            "회의 때 대기 중인 운송장이 몇 개인지 봐야 해서요, 상태 열에서 대기인 셀만 분홍색으로 강조해 주세요",
        ],
    )
    def test_a_value_that_exists_in_one_column_decides_the_range(self, message: str) -> None:
        """값('대기')이 통합문서에 실제로 있고 그 열이 하나뿐이면 추측이 아니라 확인이다."""
        from office_claw_sidecar.routers.excel_live import _value_equals_highlight

        plan = _value_equals_highlight(message, self.STATUS_DIGEST)
        assert plan, message
        params = plan[0]["params"]
        assert params["target_range"] == "D2:D5", message
        assert params["value"] == "대기", message

    @pytest.mark.parametrize("message", ["표 전체를 노랗게 칠해줘", "머리글 남색으로", "A1:C3 노란색"])
    def test_it_does_not_fire_without_a_matching_value(self, message: str) -> None:
        from office_claw_sidecar.routers.excel_live import _value_equals_highlight

        assert _value_equals_highlight(message, self.STATUS_DIGEST) == []

    def test_the_named_column_wins_over_a_longer_header(self) -> None:
        """"…**운송장**이 몇 개인지 …, **상태 열에서** 대기인 셀만" — 길이로 고르면 운송장이 이긴다."""
        from office_claw_sidecar.routers.excel_live import _scope_highlight_to_header_column

        plan = [
            {
                "action": "excel_live.highlight_by_condition",
                "params": {"target_range": "__ACTIVE_SELECTION__", "operator": "==", "threshold": 0, "value": "대기"},
            }
        ]
        message = "회의 때 대기 중인 운송장이 몇 개인지 보여 줘야 해서요, 상태 열에서 대기인 셀만 분홍색으로 강조해 주세요"
        assert _scope_highlight_to_header_column(plan, message, self.STATUS_DIGEST) == "D2:D5"

    def test_an_aggregate_request_is_not_a_value_list(self) -> None:
        """`한 줄로 합계, 표 바로 아래에 넣어줘` → A1·B1에 **명령문이 써졌다**(게이트5)."""
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        assert parse_rangeless_row_write("한 줄로 합계, 표 바로 아래에 넣어줘", "A1:F6") is None

    def test_a_plain_value_list_still_writes(self) -> None:
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        step = parse_rangeless_row_write("지역,주문건수,출고건수 입력해줘", "A1:C1")
        assert step and step["params"]["values_2d"] == [["지역", "주문건수", "출고건수"]]

    @pytest.mark.parametrize(
        "text",
        ["ㅇㅇ 그 다음 줄엔 평균값 한 줄 더", "합계 다음 줄에는 평균도 같이 넣어 주세요"],
    )
    def test_the_next_row_counts_as_below(self, text: str) -> None:
        """사람은 '아래'만큼이나 '다음 줄'이라고 말한다."""
        from office_claw_sidecar.services.excel_aggregate_below import match_aggregate_below
        from office_claw_sidecar.services.excel_live_agent import normalize_common_typos

        assert match_aggregate_below(normalize_common_typos(text)) == ("AVERAGE", "평균")


class TestBinderDoesNotUndoAConfirmedHighlight:
    """게이트7: 규칙이 통합문서를 보고 확정한 강조를 **바인더가 되돌렸다.**

    - "…대기 건을 **빨**리 찾아야 해서요, … **분홍색**으로 칠해 주세요" → D2·D4가 **빨강**.
      색 어휘에 한 음절 `("빨", "#FF0000")`이 있어 '빨리'에 걸렸다.
    - "…**운송장**이 몇 개인지 …, **상태 열에서** 대기인 셀만" → 확정한 `D2:D5`가 `A:A`로
      되돌아가 0칸 강조가 됐다(머리글 첫 히트가 운송장).
    """

    ENTRY = {
        "name": "지연경고",
        "used_range": "A1:D5",
        "columns": [
            {"letter": "A", "header": "운송장"},
            {"letter": "B", "header": "구간"},
            {"letter": "C", "header": "지연시간"},
            {"letter": "D", "header": "상태"},
        ],
        "sample_rows": [["T1", "서울", "2", "대기"]],
    }

    def _bind(self, params, message):
        from office_claw_sidecar.services.excel_param_binder import _bind_condition_format

        _bind_condition_format(params, message=message, entry=self.ENTRY, digest={"sheets": [self.ENTRY]})
        return params

    def test_a_hurry_word_is_not_the_color_red(self) -> None:
        params = self._bind(
            {"target_range": "D2:D5", "operator": "==", "threshold": 0, "value": "대기", "fill_color": "#FFC0CB"},
            "지연경고 표에서 대기 건을 빨리 찾아야 해서요, 상태가 '대기'인 셀만 분홍색으로 칠해 주세요.",
        )
        assert params["fill_color"].upper() == "#FFC0CB", params

    @pytest.mark.parametrize(
        ("message", "expected"),
        [("빨간색으로 칠해줘", "#FF0000"), ("빨갛게 강조", "#FF0000"), ("빨강 강조", "#FF0000")],
    )
    def test_real_red_words_still_work(self, message: str, expected: str) -> None:
        params = self._bind({"target_range": "D2:D5", "value": "대기"}, message)
        assert params["fill_color"].upper() == expected.upper()

    def test_a_column_scoped_range_is_not_widened(self) -> None:
        params = self._bind(
            {"target_range": "D2:D5", "operator": "==", "threshold": 0, "value": "대기"},
            "회의 때 대기 중인 운송장이 몇 개인지 봐야 해서요, 상태 열에서 대기인 셀만 분홍색으로 강조해 주세요",
        )
        assert params["target_range"] == "D2:D5", params

    def test_a_broad_range_is_still_narrowed_to_the_named_column(self) -> None:
        params = self._bind(
            {"target_range": "A:Z", "operator": "==", "threshold": 0, "value": "대기"},
            "상태 열에서 대기인 셀만 분홍색으로 강조해 주세요",
        )
        assert params["target_range"] in {"D:D", "D2:D5"}, params

    def test_the_named_column_beats_the_first_mention(self) -> None:
        params = self._bind(
            {"target_range": "A:Z", "operator": "==", "threshold": 0, "value": "대기"},
            "대기 중인 운송장이 몇 개인지 보려고요, 상태 열에서 대기인 셀만 강조",
        )
        assert params["target_range"].startswith("D"), params


class TestBareAggregateAfterPaste:
    """붙여넣기 직후의 `합계!` 한 마디 — 뜻이 하나뿐이다(그 표 아래 합계 줄)."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("합계!", ("SUM", "합계")),
            ("합계", ("SUM", "합계")),
            ("ㅇㅇ 평균", ("AVERAGE", "평균")),
            ("총합 좀", ("SUM", "총합")),
        ],
    )
    def test_it_fires_only_with_a_paste_context(self, message: str, expected) -> None:
        from office_claw_sidecar.routers.excel_live import _bare_aggregate_after_paste

        assert _bare_aggregate_after_paste(message, "A1:F6") == expected
        # 맥락이 없으면 어디에 넣을지 모른다 — 추측하지 않는다.
        assert _bare_aggregate_after_paste(message, None) is None

    @pytest.mark.parametrize("message", ["합계를 어디에 넣지", "평균이 얼마야?", "합계 열 만들어줘", "지역"])
    def test_it_does_not_fire_on_longer_sentences(self, message: str) -> None:
        from office_claw_sidecar.routers.excel_live import _bare_aggregate_after_paste

        assert _bare_aggregate_after_paste(message, "A1:F6") is None


class TestMacroPlannerDoesNotEatSimpleRequests:
    """머리말의 '보고서'와 본문의 '만들어'를 합쳐 매크로로 오인하던 문제.

    2026-08-20 게이트8의 조용한 오실행 4건 중 3건이 이 하나였다.
    `보고서 앞쪽에 요약 페이지가 필요하다고 해서요, 요약이라는 이름의 새 시트를 만들어 주세요.`
    → 21단계 매크로로 분해되고 **시트는 안 만들어졌다**.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "보고서 앞쪽에 요약 페이지가 필요하다고 해서요, 요약이라는 이름의 새 시트를 만들어 주세요.",
            "보고서에 시각 자료가 있으면 좋겠다고 해서요, 정시배송률 열을 가지고 꺾은선 그래프를 하나 만들어 주시면 감사하겠습니다",
            "보고서에 그래프를 하나 넣으라고 하셔서요, 지연건수 열을 가지고 막대 그래프를 만들어 주시면 감사하겠습니다.",
            "요약 시트 만들어줘",
        ],
    )
    def test_a_reason_preamble_is_not_a_deliverable(self, message: str) -> None:
        from office_claw_sidecar.services.excel_macro_planner import looks_like_macro_request

        assert looks_like_macro_request(message) is False, message

    @pytest.mark.parametrize(
        "message",
        [
            "대시보드 만들어줘",
            "매출 대시보드 하나 만들어 주세요",
            "월간 보고서 만들어줘",
            "영업 현황판 좀 구성해줘",
        ],
    )
    def test_a_real_composite_request_still_goes_to_the_macro_planner(self, message: str) -> None:
        from office_claw_sidecar.services.excel_macro_planner import looks_like_macro_request

        assert looks_like_macro_request(message) is True, message


class TestNumberFormatScopedToNamedColumns:
    """표시 형식도 "어느 열"이 낱말로만 있다(2026-08-20 게이트8 `comma_cols`).

    `여기 주문건수 출고건수는 1000단위로 쉼표 넣어줘` → **B2:B3만** 바뀌었다.
    규칙은 `#,##0`을 정확히 냈는데 범위가 표 전체라 플래너로 넘어갔고, 플래너가 좁게 집었다.
    """

    DIGEST = {
        "active_sheet": "지역성과",
        "sheets": [
            {
                "name": "지역성과",
                "used_range": "A1:F6",
                "columns": [
                    {"letter": "A", "header": "지역"},
                    {"letter": "B", "header": "주문건수"},
                    {"letter": "C", "header": "출고건수"},
                    {"letter": "D", "header": "정시배송률"},
                    {"letter": "E", "header": "지연건수"},
                    {"letter": "F", "header": "클레임"},
                ],
            }
        ],
    }

    def _plan(self, target="__ACTIVE_SELECTION__", code="#,##0"):
        return [{"action": "excel_live.set_number_format", "params": {"target_range": target, "format_code": code}}]

    def test_two_named_columns_become_two_steps(self) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_number_format_to_headers

        plan = self._plan()
        out = _scope_number_format_to_headers(plan, "주문건수 출고건수는 천 단위 콤마로", self.DIGEST)
        assert out == "B2:B6,C2:C6", out
        assert [s["params"]["target_range"] for s in plan] == ["B2:B6", "C2:C6"]
        assert all(s["params"]["format_code"] == "#,##0" for s in plan)

    def test_one_named_column(self) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_number_format_to_headers

        plan = self._plan(code="0.0")
        assert _scope_number_format_to_headers(plan, "정시배송률 소수 한 자리!", self.DIGEST) == "D2:D6"

    @pytest.mark.parametrize(
        "message",
        ["표 전체 천 단위 콤마", "주문출고콤마", "B2:C6 콤마로", "전부 콤마"],
    )
    def test_it_does_not_guess(self, message: str) -> None:
        """머리글을 못 짚거나 원문이 범위를 적었으면 그대로 둔다."""
        from office_claw_sidecar.routers.excel_live import _scope_number_format_to_headers

        plan = self._plan()
        assert _scope_number_format_to_headers(plan, message, self.DIGEST) == ""
        assert len(plan) == 1

    def test_an_explicit_range_in_the_plan_is_left_alone(self) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_number_format_to_headers

        plan = self._plan(target="B2:B6")
        assert _scope_number_format_to_headers(plan, "주문건수 천 단위 콤마", self.DIGEST) == ""


class TestHeaderNamesToleranceForTypos:
    """머리글 이름의 오타 한 글자 — 게이트9가 잡은 내 회귀 2건.

    `주문건수랑 **출고겅수** 천단위 콤마` → 주문건수(B)만 서식이 걸렸다.
    열 좁히기를 넣기 전에는 표 전체에 걸려 우연히 맞았는데, 정확해지면서 오타난 쪽을 잃었다.
    """

    DIGEST = {
        "active_sheet": "지역성과",
        "sheets": [
            {
                "name": "지역성과",
                "used_range": "A1:F6",
                "columns": [
                    {"letter": "A", "header": "지역"},
                    {"letter": "B", "header": "주문건수"},
                    {"letter": "C", "header": "출고건수"},
                    {"letter": "D", "header": "정시배송률"},
                    {"letter": "E", "header": "지연건수"},
                    {"letter": "F", "header": "클레임"},
                ],
            }
        ],
    }

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("주문건수랑 출고겅수 천단위 콤마 표기로 바꺼", "B2:B6,C2:C6"),
            ("주문건슈 출고건수 천단의 컴마로 보이게 해", "B2:B6,C2:C6"),
            ("주문건수 출고건수는 천 단위 콤마로", "B2:B6,C2:C6"),
            ("지연건수만 콤마", "E2:E6"),
        ],
    )
    def test_one_wrong_character_still_finds_the_column(self, message: str, expected: str) -> None:
        from office_claw_sidecar.routers.excel_live import _scope_number_format_to_headers

        plan = [
            {
                "action": "excel_live.set_number_format",
                "params": {"target_range": "__ACTIVE_SELECTION__", "format_code": "#,##0"},
            }
        ]
        assert _scope_number_format_to_headers(plan, message, self.DIGEST) == expected, message

    @pytest.mark.parametrize(
        ("header", "message", "expected"),
        [
            ("주문건수", "지연건수만 콤마", False),
            ("출고건수", "지연건수만 콤마", False),
            ("지연건수", "지연건수만 콤마", True),
            ("지역", "지연건수만 콤마", False),
        ],
    )
    def test_similar_headers_do_not_cross_match(self, header: str, message: str, expected: bool) -> None:
        """두 글자 이상 다르면 남의 열이다 — 오차 허용이 열을 뒤바꾸면 안 된다."""
        from office_claw_sidecar.routers.excel_live import _header_in_message
        from office_claw_sidecar.services.excel_aggregate_below import _norm_header

        assert _header_in_message(header, _norm_header(message)) is expected


class TestAggregateGuardDoesNotEatPastedValues:
    """붙여넣은 **값 안의 '평균'**이 집계 가드를 켜던 회귀.

    2026-08-20 28각본 배터리(1,838턴)의 유일한 실패였다 — 내가 만든 것.
    `이 표 아래에 자산 요약,값; … 평균 임대료 평당(원),32500; … 입력해줘`
    → 값 나열이 집계 요청으로 읽혀 표 만들기 인터뷰로 샜다.
    """

    PASTE = (
        "이 표 아래에 자산 요약,값; 총 자산 가치 감정가(원),285430000000; "
        "총 대출 잔액(원),126800000000; LTV(%),44.4; WALE 가중 평균 임대 만료(년),2.83; "
        "평균 임대료 평당(원),32500; 관리비 회수율(%),78.6 입력해줘"
    )

    def test_a_long_value_list_is_still_a_write(self) -> None:
        from office_claw_sidecar.services.excel_live_agent import (
            normalize_common_typos,
            parse_rangeless_row_write,
        )

        step = parse_rangeless_row_write(normalize_common_typos(self.PASTE), "A9:B17")
        assert step and step["action"] == "excel_live.write_range"
        values = step["params"]["values_2d"]
        assert values[0] == ["자산 요약", "값"], values[0]
        assert ["평균 임대료 평당(원)", 32500] in values, values

    @pytest.mark.parametrize(
        "text",
        ["한 줄로 합계, 표 바로 아래에 넣어줘", "합계를 표 아래에 한 줄로 넣어줘", "평균 한 줄 밑에 넣어줘"],
    )
    def test_a_short_aggregate_command_is_not_a_value_list(self, text: str) -> None:
        from office_claw_sidecar.services.excel_live_agent import parse_rangeless_row_write

        assert parse_rangeless_row_write(text, "A1:F6") is None, text
