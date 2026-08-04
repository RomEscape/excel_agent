from office_claw_sidecar.services.excel_live_executor import PlanStep
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
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
    assert notes == []


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
    assert bound[0].params["mode"] == "remove"


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
    assert bound[0].params["mode"] == "remove"


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


def test_existing_write_values_are_not_overwritten():
    steps = [
        PlanStep(
            action="excel_live.write_range",
            params={"start_cell": "A1", "values_2d": [["항목", "수량"]]},
        )
    ]
    bound, _notes = _bind(steps, "A1에 항목 입력해줘")
    assert bound[0].params["values_2d"] == [["항목", "수량"]]


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
