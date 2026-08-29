from office_claw_sidecar.services.excel_live_executor import PlanStep
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
    explicit_sheet_mentions,
    resolve_sheet_from_message,
)

DIGEST = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:H9",
            "columns": [
                {"letter": "A", "header": "월", "categories": ["1월", "2월"]},
                {"letter": "B", "header": "카테고리", "categories": ["A", "B"]},
                {"letter": "C", "header": "수량", "numeric": True},
                {"letter": "D", "header": "단가", "numeric": True},
                {"letter": "E", "header": "금액", "numeric": True},
                {"letter": "F", "header": "상태", "categories": ["완료", "진행중", "지연"]},
                {"letter": "G", "header": "담당자", "categories": ["김민수", "이지은"]},
            ],
            "sample_rows": [["1월", "A", "10", "100", "1000", "완료", "김민수"]],
        },
        {"name": "전월", "used_range": "A1:H9", "columns": [{"letter": "A", "header": "월"}]},
        {"name": "피벗1", "used_range": "A1", "columns": []},
    ],
}


def _bind(steps, message, sheet="매출"):
    return bind_plan_steps(steps, digest=DIGEST, message=message, sheet_name=sheet)


def test_numeric_key_column_is_rebound_to_mentioned_header():
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": 1, "order": "asc"})]
    bound, _notes = _bind(steps, "매출 시트 A1:H9 범위를 금액 열 기준 오름차순 정렬해줘")
    assert bound[0].params["key_column"] == "금액"


def test_invented_source_range_is_widened_to_the_whole_table():
    """플래너가 지어낸 좁은 범위로 집계하면 일부 행만 더해 놓고 성공했다고 답한다."""
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={"source_range": "A1:E37", "row_field": "카테고리", "value_field": "금액"},
        )
    ]
    bound, _notes = _bind(steps, "카테고리별 금액 합계를 요약 시트에 만들어줘")
    assert bound[0].params["source_range"] == "__ACTIVE_SELECTION__"


def test_range_stated_in_the_message_is_kept():
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={"source_range": "A1:E37", "row_field": "카테고리", "value_field": "금액"},
        )
    ]
    bound, _notes = _bind(steps, "A1:E37 범위만 카테고리별로 합계 내줘")
    assert bound[0].params["source_range"] == "A1:E37"


def test_invented_sort_range_is_widened_to_the_whole_table():
    steps = [
        PlanStep(action="excel_live.sort_range", params={"target_range": "A1:H20", "key_column": "금액"})
    ]
    bound, _notes = _bind(steps, "금액 내림차순으로 정렬해줘")
    assert bound[0].params["target_range"] == "__ACTIVE_SELECTION__"


def test_sheet_name_is_not_mistaken_for_column():
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": 2})]
    bound, _notes = _bind(steps, "매출 시트를 금액 기준으로 정렬해줘")
    assert bound[0].params["key_column"] == "금액"


def test_dataset_label_is_not_used_as_sort_key():
    # "금액 데이터를 월 오름차순으로" — 앞의 금액은 대상을 부르는 말이다.
    # 첫 언급을 그냥 집으면 금액으로 정렬해 표가 통째로 뒤섞인다.
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": 1, "order": "asc"})]
    bound, _notes = _bind(steps, "금액 데이터를 월 오름차순으로 정렬해줘")
    assert bound[0].params["key_column"] == "월"


def test_sort_key_is_the_column_next_to_the_order_phrase():
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": "수량"})]
    bound, _notes = _bind(steps, "담당자별 표에서 금액 내림차순으로 정렬해줘")
    assert bound[0].params["key_column"] == "금액"


def test_nonexistent_column_is_replaced_with_message_header():
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "점수", "operator": ">=", "value": 1000},
        )
    ]
    bound, _notes = _bind(steps, "금액이 1000 이상인 행만 남겨줘")
    assert bound[0].params["column"] == "금액"


def test_existing_header_is_kept():
    steps = [PlanStep(action="excel_live.filter_rows", params={"column": "상태", "value": "완료"})]
    bound, notes = _bind(steps, "완료된 것만 남겨줘")
    assert bound[0].params["column"] == "상태"
    # 열은 그대로 두되, 필터가 지울지 숨길지는 기록한다(2026-08-20: 기본 숨기기).
    assert [n["changes"] for n in notes] == [["mode=hide"]]
    assert bound[0].params["mode"] == "hide"


def test_missing_filter_value_is_bound_from_column_categories():
    steps = [PlanStep(action="excel_live.filter_rows", params={"column": 1, "operator": "=="})]
    bound, _notes = _bind(steps, "완료된 건만 보여줘")
    assert bound[0].params["column"] == "상태"
    assert bound[0].params["value"] == "완료"


def test_pivot_fields_are_bound_from_roles():
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={
                "row_field": 1,
                "value_field": 2,
                "column_field": None,
                "agg": "sum",
                "output_sheet": "피벗1!A1",
                "output_start": "A1",
            },
        )
    ]
    bound, _notes = _bind(
        steps,
        "매출 시트 A1:E9에서 월을 행으로, 카테고리를 열로, 금액 합계 피벗을 피벗1 시트 A1에 만들어줘",
    )
    params = bound[0].params
    assert params["row_field"] == "월"
    assert params["column_field"] == "카테고리"
    assert params["value_field"] == "금액"
    assert params["output_sheet"] == "피벗1"
    assert params["output_start"] == "A1"


def test_exclusion_phrasing_flips_the_filter_to_remove():
    """ "완료된 건 빼줘"를 그대로 두면 완료만 남기고 나머지를 다 지운다 — 정반대 편집이다."""
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "상태", "operator": "==", "value": "완료"},
        )
    ]
    bound, _notes = _bind(steps, "상태가 완료인 것들은 다 빼줘")
    # 제외 판정은 그대로 — 다만 **지우라는 말이 없으므로** 숨기기판으로 간다
    # (2026-08-20: 되돌릴 수 없는 삭제는 지우라고 말했을 때만).
    assert bound[0].params["mode"] == "hide_exclude"


def test_inclusion_phrasing_keeps_the_filter_as_keep():
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "상태", "operator": "==", "value": "완료"},
        )
    ]
    bound, _notes = _bind(steps, "완료된 건만 남기고 나머지는 지워줘")
    assert bound[0].params.get("mode") != "remove"


def test_exclusion_wins_when_the_only_particle_is_on_the_remainder():
    # "나머지만 남겨줘"의 '만'은 값이 아니라 여집합에 붙어 있다.
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "상태", "operator": "==", "value": "취소"},
        )
    ]
    bound, _notes = _bind(steps, "취소된 주문은 빼고 나머지만 남겨줘")
    assert bound[0].params["mode"] == "hide_exclude"
    # 지우라고 말하면 그때는 지운다.
    bound2, _n2 = _bind(steps, "취소된 주문은 지우고 나머지만 남겨줘")
    assert bound2[0].params["mode"] == "remove"


def test_invented_output_start_falls_back_to_a1():
    """플래너가 근거 없이 B1을 채우면 결과표가 한 칸 밀려 A열이 빈다."""
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={
                "row_field": "카테고리",
                "value_field": "금액",
                "output_sheet": "요약",
                "output_start": "B1",
            },
        )
    ]
    bound, _notes = _bind(steps, "카테고리별 금액 합계를 요약 시트에 만들어줘")
    assert bound[0].params["output_start"] == "A1"


def test_output_start_stated_in_the_message_is_kept():
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={
                "row_field": "카테고리",
                "value_field": "금액",
                "output_sheet": "요약",
                "output_start": "C3",
            },
        )
    ]
    bound, _notes = _bind(steps, "카테고리별 금액 합계를 요약 시트 C3부터 채워줘")
    assert bound[0].params["output_start"] == "C3"


def test_dedupe_key_columns_are_resolved():
    steps = [PlanStep(action="excel_live.dedupe_rows", params={"key_columns": [3]})]
    bound, _notes = _bind(steps, "담당자 열 기준으로 중복 제거해줘")
    assert bound[0].params["key_columns"] == ["담당자"]


def test_binding_is_skipped_when_digest_is_empty():
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": 1})]
    bound, notes = bind_plan_steps(
        steps, digest={"active_sheet": "", "sheets": []}, message="금액 기준 정렬", sheet_name=None
    )
    assert bound[0].params["key_column"] == 1
    assert notes == []


def test_write_value_is_filled_from_message():
    steps = [PlanStep(action="excel_live.write_range", params={"start_cell": "__ACTIVE_CELL__"})]
    bound, _notes = _bind(steps, "C3에 120 입력해줘")
    assert bound[0].params["values_2d"] == [[120]]
    assert bound[0].params["start_cell"] == "C3"


def test_write_value_is_filled_even_without_digest():
    steps = [PlanStep(action="excel_live.write_range", params={})]
    bound, _notes = bind_plan_steps(
        steps, digest={"active_sheet": "", "sheets": []}, message="A1에 안녕 써줘", sheet_name=None
    )
    assert bound[0].params["values_2d"] == [["안녕"]]


def test_range_write_splits_comma_separated_values_across_cells():
    steps = [PlanStep(action="excel_live.write_range", params={"start_cell": "__ACTIVE_CELL__"})]
    bound, _notes = _bind(steps, "E2:G2에 이름,수량,금액 입력")
    assert bound[0].params["values_2d"] == [["이름", "수량", "금액"]]
    assert bound[0].params["start_cell"] == "E2"


def test_range_write_start_cell_is_the_top_left_not_the_range_end():
    """플래너가 범위 끝(G2)을 시작 셀로 잡아 와도 좌상단으로 되돌린다."""
    steps = [PlanStep(action="excel_live.write_range", params={"start_cell": "G2"})]
    bound, _notes = _bind(steps, "E2:G2에 이름,수량,금액 입력")
    assert bound[0].params["start_cell"] == "E2"


def test_vertical_range_write_produces_a_column():
    steps = [PlanStep(action="excel_live.write_range", params={})]
    bound, _notes = _bind(steps, "E2:E4에 사과,배,감 입력")
    assert bound[0].params["values_2d"] == [["사과"], ["배"], ["감"]]
    assert bound[0].params["start_cell"] == "E2"


def test_reversed_range_still_anchors_at_the_top_left():
    steps = [PlanStep(action="excel_live.write_range", params={})]
    bound, _notes = _bind(steps, "G2:E2에 1,2,3 입력")
    assert bound[0].params["values_2d"] == [[1, 2, 3]]
    assert bound[0].params["start_cell"] == "E2"


def test_single_cell_keeps_thousand_separator_intact():
    """단일 셀에서는 콤마가 값 구분자가 아니라 천 단위 구분자다."""
    steps = [PlanStep(action="excel_live.write_range", params={})]
    bound, _notes = _bind(steps, "C3에 1,000 입력")
    assert bound[0].params["values_2d"] == [[1000]]
    assert bound[0].params["start_cell"] == "C3"


def test_existing_write_values_are_not_overwritten():
    steps = [
        PlanStep(
            action="excel_live.write_range",
            params={"start_cell": "A1", "values_2d": [["항목", "수량"]]},
        )
    ]
    bound, _notes = _bind(steps, "A1에 항목 입력해줘")
    assert bound[0].params["values_2d"] == [["항목", "수량"]]


def test_chart_stays_on_the_source_sheet_when_no_sheet_was_named():
    """플래너가 지어낸 차트 전용 시트는 버린다 — 차트는 표를 덮어쓰지 않는다."""
    steps = [
        PlanStep(
            action="excel_live.create_chart",
            params={"source_range": "A5:C11", "chart_type": "bar", "output_sheet": "Rep_Chart"},
        )
    ]
    bound, _notes = _bind(steps, "A5:C11로 막대 차트 만들어줘")
    assert "output_sheet" not in bound[0].params


def test_chart_honors_an_explicitly_named_result_sheet():
    steps = [
        PlanStep(
            action="excel_live.create_chart",
            params={"source_range": "A5:C11", "chart_type": "bar"},
        )
    ]
    bound, _notes = _bind(steps, "A5:C11로 막대 차트를 Region_Chart 시트에 만들어줘")
    assert bound[0].params["output_sheet"] == "Region_Chart"


def test_pivot_keeps_its_separate_output_sheet():
    """집계는 결과표를 쓰므로 별도 시트가 필요하다 — 차트 규칙을 여기 적용하면 안 된다."""
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={"row_field": "카테고리", "value_field": "금액", "output_sheet": "요약"},
        )
    ]
    bound, _notes = _bind(steps, "카테고리별 금액 합계 내줘")
    assert bound[0].params["output_sheet"] == "요약"


def test_filter_without_any_column_hint_is_reported_unresolved():
    """기준 열을 못 정한 filter_rows는 되물어야 한다 — 행을 지우는 작업이라서."""
    steps = [PlanStep(action="excel_live.filter_rows", params={})]
    _bound, notes = _bind(steps, "필요없는 행 지워줘")
    unresolved = {(n["action"], n["slot"]) for n in notes if n.get("status") == "unresolved"}
    assert ("excel_live.filter_rows", "column") in unresolved


def test_hallucinated_sort_key_does_not_clear_the_unresolved_report():
    """플래너가 학습 데이터의 열 이름('Qty')을 지어내도 그건 기준이 될 수 없다."""
    steps = [
        PlanStep(
            action="excel_live.sort_range",
            params={"key_column": "Qty", "order": "desc", "has_header": True},
        )
    ]
    _bound, notes = _bind(steps, "정렬해줘")
    unresolved = {(n["action"], n["slot"]) for n in notes if n.get("status") == "unresolved"}
    assert ("excel_live.sort_range", "key_column") in unresolved


def test_sort_key_stated_in_the_message_is_not_reported_unresolved():
    steps = [PlanStep(action="excel_live.sort_range", params={"key_column": "금액", "order": "desc"})]
    _bound, notes = _bind(steps, "금액 열 기준 내림차순으로 정렬해줘")
    unresolved = {(n["action"], n.get("slot")) for n in notes if n.get("status") == "unresolved"}
    assert ("excel_live.sort_range", "key_column") not in unresolved


def test_filter_column_inferred_from_value_is_not_reported_unresolved():
    """값이 한 열에만 있으면 기준 열이 정해진 것이니 묻지 않는다."""
    steps = [PlanStep(action="excel_live.filter_rows", params={"value": "완료"})]
    bound, notes = _bind(steps, "완료된 것만 남겨줘")
    unresolved = {(n["action"], n["slot"]) for n in notes if n.get("status") == "unresolved"}
    assert bound[0].params["column"] == "상태"
    assert ("excel_live.filter_rows", "column") not in unresolved


def test_filter_value_not_in_sheet_is_corrected_to_real_cell_value():
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "월", "operator": "==", "value": "완료된"},
        )
    ]
    bound, _notes = _bind(steps, "완료된 판매만 남겨줘")
    assert bound[0].params["value"] == "완료"
    assert bound[0].params["column"] == "상태"


def test_filter_value_already_matching_sheet_is_kept():
    steps = [
        PlanStep(
            action="excel_live.filter_rows",
            params={"column": "상태", "operator": "==", "value": "지연"},
        )
    ]
    bound, _notes = _bind(steps, "완료 건만 빼고 지연만 보여줘")
    assert bound[0].params["value"] == "지연"


def _highlight(message, params=None):
    base = {"target_range": "A:Z", "operator": "greater_than", "threshold": 0, "fill_color": "#FFFF00"}
    base.update(params or {})
    bound, _notes = _bind([PlanStep(action="excel_live.highlight_by_condition", params=base)], message)
    return bound[0].params


def test_condition_highlight_narrows_range_to_mentioned_column():
    assert _highlight("매출 시트 E열에서 1100 이상은 빨간색으로 표시해줘")["target_range"] == "E:E"


def test_condition_highlight_uses_color_from_message():
    assert _highlight("E열에서 1100 이상은 빨간색으로 표시해줘")["fill_color"] == "#FF0000"


def test_condition_operator_and_threshold_follow_the_message():
    params = _highlight("E열에서 1100 이상은 빨간색으로 표시해줘")
    assert params["operator"] == ">="
    assert params["threshold"] == 1100.0


def test_condition_operator_respects_exclusive_wording():
    assert _highlight("금액이 500 초과면 노란색")["operator"] == ">"
    assert _highlight("금액이 500 미만이면 파란색")["operator"] == "<"


def test_condition_range_is_kept_when_planner_already_narrowed_it():
    assert _highlight("1100 이상 빨간색", {"target_range": "E2:E9"})["target_range"] == "E2:E9"


def test_condition_column_from_message_beats_planner_guess():
    """플래너가 그럴듯한 다른 열을 집어도 원문이 부른 열이 이긴다.

    "진행률이 80% 미만" 을 옆 열인 잔여일수에 걸면 전 행이 칠해지고도 성공으로 보고된다.
    """
    params = _highlight("금액이 1100 이상이면 빨간색으로 표시해줘", {"target_range": "C2:C9"})
    assert params["target_range"] == "E:E"


def test_condition_keeps_planner_range_when_message_states_one():
    params = _highlight("E2:E9 범위에서 1100 이상 빨간색", {"target_range": "E2:E9"})
    assert params["target_range"] == "E2:E9"


CROSS_SHEET_DIGEST = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:C9",
            "columns": [
                {"letter": "A", "header": "월"},
                {"letter": "B", "header": "금액", "numeric": True},
            ],
        },
        {
            "name": "재고",
            "used_range": "A1:F9",
            "columns": [
                {"letter": "A", "header": "품목"},
                {"letter": "E", "header": "현재고", "numeric": True},
                {"letter": "F", "header": "재주문점", "numeric": True},
            ],
        },
    ],
}


def test_condition_moves_to_the_sheet_that_owns_the_mentioned_column():
    """ "재고가 재주문점 이하" 는 활성 시트가 아니라 재고 시트 이야기다."""
    steps = [
        PlanStep(
            action="excel_live.highlight_by_condition",
            params={"target_range": "A:Z", "operator": ">=", "threshold": 0, "fill_color": "#FFFF00"},
        )
    ]
    bound, _notes = bind_plan_steps(
        steps, digest=CROSS_SHEET_DIGEST, message="현재고가 재주문점 이하인 품목을 빨간색으로 표시해줘", sheet_name="매출"
    )
    params = bound[0].params
    assert params["target_range"] == "재고!E:E"
    assert params["compare_column"] == "F"
    assert params["operator"] == "<="


def test_colloquial_comparison_words_map_to_the_right_operator():
    """ "보다 적거나 같은"이 '같'에 먼저 걸려 ==로 새면 한 건도 안 잡힌다."""
    for phrase, expected in (
        ("재고가 재주문점보다 적거나 같은 제품 표시해줘", "<="),
        ("재고가 재주문점보다 적은 제품 표시해줘", "<"),
        ("재고가 재주문점보다 많은 제품 표시해줘", ">"),
    ):
        steps = [
            PlanStep(
                action="excel_live.highlight_by_condition",
                params={"target_range": "A:Z", "operator": ">=", "threshold": 0, "fill_color": "#FF0000"},
            )
        ]
        bound, _notes = bind_plan_steps(steps, digest=CROSS_SHEET_DIGEST, message=phrase, sheet_name="매출")
        assert bound[0].params["operator"] == expected, phrase
        assert bound[0].params["target_range"] == "재고!E:E", phrase


def test_operator_holding_a_whole_expression_is_unpacked():
    """플래너가 operator에 '현재고 < 재주문점' 처럼 식을 통째로 넣어도 실행되어야 한다."""
    steps = [
        PlanStep(
            action="excel_live.highlight_by_condition",
            params={
                "target_range": "재고!E2:E11",
                "operator": "현재고 <= 재주문점",
                "fill_color": "#FF0000",
            },
        )
    ]
    bound, _notes = bind_plan_steps(
        steps, digest=CROSS_SHEET_DIGEST, message="발주 필요한 품목 표시해줘", sheet_name="재고"
    )
    params = bound[0].params
    assert params["operator"] == "<="
    assert params["target_range"] == "재고!E:E"
    assert params["compare_column"] == "F"


def test_operator_expression_finds_the_sheet_that_owns_both_columns():
    """활성 시트 머리글이 우연히 걸려도, 식의 두 열을 다 가진 시트로 간다."""
    steps = [
        PlanStep(
            action="excel_live.highlight_by_condition",
            params={
                "target_range": "A:Z",
                "operator": "Current_Stock<Reorder_Point",
                "fill_color": "#FF0000",
            },
        )
    ]
    digest = {
        "active_sheet": "매출",
        "sheets": [
            {
                "name": "매출",
                "used_range": "A1:C9",
                "columns": [{"letter": "A", "header": "품목"}, {"letter": "B", "header": "금액"}],
            },
            {
                "name": "재고",
                "used_range": "A1:F9",
                "columns": [
                    {"letter": "A", "header": "품목"},
                    {"letter": "E", "header": "Current_Stock", "numeric": True},
                    {"letter": "F", "header": "Reorder_Point", "numeric": True},
                ],
            },
        ],
    }
    bound, _notes = bind_plan_steps(
        steps, digest=digest, message="발주 필요한 품목을 빨간색으로 표시해줘", sheet_name="매출"
    )
    params = bound[0].params
    assert params["target_range"] == "재고!E:E"
    assert params["compare_column"] == "F"
    assert params["operator"] == "<"


def test_operator_expression_with_a_number_becomes_a_threshold():
    steps = [
        PlanStep(
            action="excel_live.highlight_by_condition",
            params={"target_range": "A:Z", "operator": "금액 >= 1100", "fill_color": "#FF0000"},
        )
    ]
    bound, _notes = _bind(steps, "기준 넘는 행 표시해줘")
    params = bound[0].params
    assert params["operator"] == ">="
    assert params["threshold"] == 1100.0
    assert params["target_range"] == "E:E"


def test_condition_stays_put_when_the_column_is_ambiguous():
    """여러 시트에 같은 열 이름이 있으면 옮기지 않는다 — 추측이 더 위험하다."""
    digest = {
        "active_sheet": "매출",
        "sheets": [
            {"name": "매출", "used_range": "A1:B9", "columns": [{"letter": "A", "header": "월"}]},
            {"name": "재고1", "used_range": "A1:B9", "columns": [{"letter": "B", "header": "수량", "numeric": True}]},
            {"name": "재고2", "used_range": "A1:B9", "columns": [{"letter": "C", "header": "수량", "numeric": True}]},
        ],
    }
    steps = [
        PlanStep(
            action="excel_live.highlight_by_condition",
            params={"target_range": "A:Z", "operator": ">=", "threshold": 0, "fill_color": "#FFFF00"},
        )
    ]
    bound, _notes = bind_plan_steps(
        steps, digest=digest, message="수량이 10 미만이면 빨간색", sheet_name="매출"
    )
    assert "!" not in bound[0].params["target_range"]


def test_resolve_sheet_from_message_prefers_first_real_sheet():
    assert resolve_sheet_from_message("전월 시트 정리해줘", DIGEST, default="매출") == "전월"
    assert resolve_sheet_from_message("없는 시트 정리해줘", DIGEST, default="매출") == "매출"
    assert (
        resolve_sheet_from_message("매출 시트와 전월 시트를 비교해줘", DIGEST, default=None) == "매출"
    )


def test_single_token_sheet_name_without_sheet_word():
    """'Inventory를 표로'는 '시트'가 없어도 Inventory여야 한다. 아니면 Dashboard에 표가 생긴다."""
    digest = {
        "active_sheet": "Dashboard",
        "sheets": [
            {"name": "Dashboard", "used_range": "A1", "columns": [{"letter": "A", "header": "대시보드"}]},
            {"name": "Inventory", "used_range": "A1:K11", "columns": [{"letter": "A", "header": "SKU"}]},
            {"name": "Sales_Data", "used_range": "A1:Q11", "columns": [{"letter": "A", "header": "Order_ID"}]},
        ],
    }
    assert (
        resolve_sheet_from_message(
            "Inventory를 InventoryTable 이름으로 엑셀 표 테이블로 만들어줘",
            digest,
            default="Dashboard",
        )
        == "Inventory"
    )
    assert (
        resolve_sheet_from_message(
            "Dashboard 시트 I6에 =COUNTIF(Inventory!H2:H11,\"발주필요\") 수식 넣어줘",
            digest,
            default="Dashboard",
        )
        == "Dashboard"
    )


def test_writes_after_create_sheet_land_on_the_new_sheet():
    """시트를 만든 뒤 이어지는 쓰기는 새 시트로 간다. 안 그러면 원본 A1을 덮어쓴다."""
    steps = [
        PlanStep(action="excel_live.create_sheet", params={"sheet_name": "요약"}),
        PlanStep(
            action="excel_live.write_range",
            params={"start_cell": "A1", "values_2d": [["총매출"]]},
        ),
        PlanStep(action="excel_live.set_formula", params={"range_ref": "B1", "formula_a1": "=1"}),
    ]
    bound, _notes = _bind(steps, "요약 시트 만들어서 A1에 총매출 쓰고 B1에 합계 수식 넣어줘")
    assert bound[1].params["sheet_name"] == "요약"
    assert bound[2].params["sheet_name"] == "요약"


def test_explicit_sheet_on_a_later_step_is_not_overwritten():
    steps = [
        PlanStep(action="excel_live.create_sheet", params={"sheet_name": "요약"}),
        PlanStep(
            action="excel_live.write_range",
            params={"start_cell": "A1", "values_2d": [["x"]], "sheet_name": "매출"},
        ),
    ]
    bound, _notes = _bind(steps, "요약 시트 만들고 매출 시트 A1에 x 써줘")
    assert bound[1].params["sheet_name"] == "매출"


def test_pivot_source_is_not_dragged_onto_the_new_sheet():
    """집계는 원본을 읽어야 한다. 새 시트로 끌고 가면 빈 시트를 읽고 0행이 된다."""
    steps = [
        PlanStep(action="excel_live.create_sheet", params={"sheet_name": "요약"}),
        PlanStep(
            action="excel_live.pivot_table",
            params={"row_field": "카테고리", "value_field": "금액", "output_sheet": "요약"},
        ),
    ]
    bound, _notes = _bind(steps, "요약 시트 만들어서 카테고리별 금액 합계 정리해줘")
    assert bound[1].params.get("sheet_name") in (None, "")


def test_pivot_retargets_to_the_sheet_that_owns_the_headers():
    """이전 턴이 Inventory를 켜 둔 채 '지역별 매출'을 말하면 Sales_Data로 옮겨야 한다."""
    digest = {
        "active_sheet": "Inventory",
        "sheets": [
            {
                "name": "Inventory",
                "used_range": "A1:K11",
                "columns": [
                    {"letter": "A", "header": "SKU"},
                    {"letter": "H", "header": "Stock_Status"},
                    {"letter": "E", "header": "Current_Stock", "numeric": True},
                ],
            },
            {
                "name": "Sales_Data",
                "used_range": "A1:Q11",
                "columns": [
                    {"letter": "D", "header": "Region"},
                    {"letter": "L", "header": "Sales", "numeric": True},
                    {"letter": "N", "header": "Gross_Profit", "numeric": True},
                ],
            },
        ],
    }
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={"row_field": "지역", "value_field": "매출"},
        )
    ]
    bound, _notes = bind_plan_steps(
        steps,
        digest=digest,
        message="지역별 매출과 이익을 집계해서 새 시트와 차트를 만들어줘",
        sheet_name="Inventory",
    )
    assert bound[0].params["source_sheet"] == "Sales_Data"
    assert bound[0].params["row_field"] == "Region"
    assert bound[0].params["value_field"] in {"Sales", "Gross_Profit"}
    assert bound[0].params["output_sheet"] == "Sales_Data_집계"


def test_pivot_overwrites_wrong_active_source_sheet():
    """슬롯이 Dashboard를 원본으로 넣어도 머리글이 있는 Sales_Data로 바꿔야 한다."""
    digest = {
        "active_sheet": "Dashboard",
        "sheets": [
            {
                "name": "Dashboard",
                "used_range": "A1",
                "columns": [{"letter": "A", "header": "AI 기반 통합 운영 대시보드"}],
            },
            {
                "name": "Sales_Data",
                "used_range": "A1:Q11",
                "columns": [
                    {"letter": "D", "header": "Region"},
                    {"letter": "L", "header": "Sales", "numeric": True},
                    {"letter": "N", "header": "Gross_Profit", "numeric": True},
                ],
            },
        ],
    }
    steps = [
        PlanStep(
            action="excel_live.pivot_table",
            params={
                "source_range": "__ACTIVE_SELECTION__",
                "row_field": "지역",
                "value_field": "매출",
                "source_sheet": "Dashboard",
            },
        )
    ]
    bound, _notes = bind_plan_steps(
        steps,
        digest=digest,
        message="지역별 매출과 이익을 집계해서 새 시트와 차트를 만들어줘",
        sheet_name=None,
    )
    assert bound[0].params["source_sheet"] == "Sales_Data"
    assert bound[0].params["row_field"] == "Region"
    assert bound[0].params["value_field"] in {"Sales", "Gross_Profit"}
    assert bound[0].params["output_sheet"] == "Sales_Data_집계"


class TestExplicitSheetMentions:
    """원문이 "<이름> 시트"로 지목한 이름은, 그 시트가 아직 없어도 잃으면 안 된다.

    resolve_sheet_from_message는 **있는** 시트만 고르므로 없는 시트 지목은 통째로
    버려진다. 그 상태로 활성 시트에 쓰면 사용자가 말한 적 없는 시트를 덮어쓴다 —
    2026-08-16 실측에서 "Dashboard 시트 B4에 합계 수식"이 Sales_Data!B4의
    주문일자를 지우고도 성공으로 보고됐다.
    """

    def test_it_picks_up_a_sheet_that_does_not_exist_yet(self):
        assert explicit_sheet_mentions("Dashboard 시트 A4에 총 매출 입력해줘") == ["Dashboard"]

    def test_it_keeps_existing_sheets_too(self):
        # 존재 여부는 호출부가 판정한다. 여기서는 지목만 뽑는다.
        assert explicit_sheet_mentions("매출 시트 정렬해줘") == ["매출"]

    def test_demonstratives_are_not_sheet_names(self):
        # "새 시트로 만들어줘"의 '새'를 이름으로 읽으면 멀쩡한 요청이 막힌다.
        assert explicit_sheet_mentions("지역별 매출을 집계해서 새 시트로 만들어줘") == []
        assert explicit_sheet_mentions("이 시트에 표 만들어줘") == []
        assert explicit_sheet_mentions("현재 시트 저장해줘") == []

    def test_several_mentions_keep_their_order(self):
        assert explicit_sheet_mentions("Sales_Data 시트를 Dashboard 시트로 복사해줘") == [
            "Sales_Data",
            "Dashboard",
        ]

    def test_a_message_without_any_sheet_word_yields_nothing(self):
        assert explicit_sheet_mentions("A1:C10 합계 구해줘") == []
        assert explicit_sheet_mentions("") == []


class TestTextColumnEqualityNarrowing:
    """값 일치 조건("상태가 대기인 애들만")의 기준 열은 글자 열이다 —
    숫자 열만 인정하면 텍스트 조건이 못 좁혀져 0건 강조가 된다(2026-08-18)."""

    def test_a_text_column_is_accepted_when_value_matching(self):
        from office_claw_sidecar.services.excel_param_binder import _bind_condition_format

        entry = {
            "name": "지연경고",
            "used_range": "A1:E6",
            "columns": [
                {"letter": "A", "header": "경고시간", "numeric": False},
                {"letter": "E", "header": "상태", "numeric": False},
            ],
        }
        params = {"target_range": "A:Z", "value": "대기", "operator": "==", "fill_color": "#FFC0CB"}
        _bind_condition_format(params, message="상태가 대기인 애들만 분홍으로 칠해주라", entry=entry)
        assert params["target_range"] == "E:E", params
