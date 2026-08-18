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
