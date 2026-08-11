"""Excel Live 계획 검증기(Validator).

LLM 계획(action_plan)을 실행 가능한 결정적 형태로 정규화한다.
- action 화이트리스트
- action별 params 스키마 강제
- 기본값/문맥(context_range, recent_range) 보정
- 명백한 오분류(write_range + table intent) 복구
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from office_claw_sidecar.services.excel_live_executor import PlanStep

# 플래너만 고를 수 있고 실행기에는 내려가지 않는 액션.
# 라우터가 실행 직전에 가로채 되묻기 응답으로 바꾼다.
PLANNER_ONLY_ACTIONS = {
    "excel_live.clarify",
}

SUPPORTED_ACTIONS = {
    "excel_live.clarify",
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.list_sheets",
    "excel_live.select_sheet",
    "excel_live.create_sheet",
    "excel_live.read_range",
    "excel_live.write_range",
    "excel_live.create_table",
    "excel_live.highlight_by_condition",
    "excel_live.fill_range",
    "excel_live.clear_range",
    "excel_live.apply_border",
    "excel_live.set_formula",
    "excel_live.verify_formula_result",
    "excel_live.sort_range",
    "excel_live.filter_rows",
    "excel_live.dedupe_rows",
    "excel_live.find_duplicates",
    "excel_live.recalculate",
    "excel_live.export_pdf",
    "excel_live.pivot_table",
    "excel_live.create_chart",
    "excel_live.validate_data",
    "excel_live.protect_sheet",
    "excel_live.set_data_validation",
    "excel_live.consolidate_sheets",
    "excel_live.consolidate_workbooks_from_folder",
    "excel_live.refresh_power_query",
    "excel_live.run_vba_macro",
    "excel_live.compare_ranges",
    "excel_live.forecast_linear",
    # 나중에 추가된 열/집계 도구. 여기 없으면 플래너가 골라도 실행 전에 반려된다.
    "excel_live.calculate_column_stat",
    "excel_live.group_by_aggregate",
    "excel_live.sort_rows",
    "excel_live.drop_column",
    "excel_live.rename_column",
    "excel_live.add_column",
    "excel_live.save_workbook",
    # 셀 병합/찾아바꾸기/틀고정/열너비/이름정의 — "아직 없는 도구" 보강.
    "excel_live.find_replace",
    "excel_live.merge_cells",
    "excel_live.unmerge_cells",
    "excel_live.freeze_panes",
    "excel_live.autofit_columns",
    "excel_live.define_named_range",
    # 인쇄 설정/셀 메모/색조·데이터 막대/숫자 서식 — 2차 보강.
    "excel_live.set_print_area",
    "excel_live.add_cell_comment",
    "excel_live.apply_color_scale",
    "excel_live.apply_data_bar",
    "excel_live.set_number_format",
}

EDIT_ACTIONS = {
    "excel_live.recalculate",
    "excel_live.export_pdf",
    "excel_live.create_sheet",
    "excel_live.write_range",
    "excel_live.create_table",
    "excel_live.highlight_by_condition",
    "excel_live.fill_range",
    "excel_live.clear_range",
    "excel_live.apply_border",
    "excel_live.set_formula",
    "excel_live.sort_range",
    "excel_live.filter_rows",
    "excel_live.dedupe_rows",
    "excel_live.pivot_table",
    "excel_live.create_chart",
    "excel_live.protect_sheet",
    "excel_live.set_data_validation",
    "excel_live.consolidate_sheets",
    "excel_live.consolidate_workbooks_from_folder",
    "excel_live.refresh_power_query",
    "excel_live.run_vba_macro",
    "excel_live.compare_ranges",
    "excel_live.forecast_linear",
    "excel_live.sort_rows",
    "excel_live.drop_column",
    "excel_live.rename_column",
    "excel_live.add_column",
    "excel_live.save_workbook",
    "excel_live.find_replace",
    "excel_live.merge_cells",
    "excel_live.unmerge_cells",
    "excel_live.freeze_panes",
    "excel_live.autofit_columns",
    "excel_live.define_named_range",
    "excel_live.set_print_area",
    "excel_live.add_cell_comment",
    "excel_live.apply_color_scale",
    "excel_live.apply_data_bar",
    "excel_live.set_number_format",
}

PASSIVE_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.list_sheets",
    "excel_live.select_sheet",
    "excel_live.read_range",
    "excel_live.verify_formula_result",
    "excel_live.validate_data",
    "excel_live.compare_ranges",
    "excel_live.find_duplicates",
    "excel_live.calculate_column_stat",
    "excel_live.group_by_aggregate",
}


@dataclass
class ValidationContext:
    message: str
    workbook_id: str | None = None
    sheet_name: str | None = None
    context_range: str | None = None
    recent_range: str | None = None


def _normalize_range_text(value: Any) -> str:
    return str(value or "").strip().upper().replace("$", "")


# 소형 모델은 Excel 서식 코드 문법(#,##0 등)을 모르고 "퍼센트로", "천단위로" 같은
# 개념어만 내놓는 경우가 많다. 개념어 → 실제 서식 코드 매핑을 여기서 흡수한다.
_NUMBER_FORMAT_ALIASES: dict[str, str] = {
    "percent": "0.00%",
    "percentage": "0.00%",
    "퍼센트": "0.00%",
    "백분율": "0.00%",
    "comma": "#,##0",
    "천단위": "#,##0",
    "천단위구분": "#,##0",
    "숫자": "#,##0",
    "currency": "#,##0",
    "통화": "#,##0",
    "원": '#,##0"원"',
    "date": "yyyy-mm-dd",
    "날짜": "yyyy-mm-dd",
    "text": "@",
    "텍스트": "@",
    "일반": "General",
    "general": "General",
}
# 이미 Excel 서식 코드 문법으로 보이는 문자(#, 0, %, y/m/d, @, General)를 하나라도
# 포함하면 alias 매핑을 건너뛰고 그대로 사용한다 — 모델이 정확한 코드를 직접 준 경우다.
_LOOKS_LIKE_FORMAT_CODE = re.compile(r"[#0%@]|yyyy|mm|dd|General", re.IGNORECASE)


def _normalize_number_format(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _LOOKS_LIKE_FORMAT_CODE.search(text):
        return text
    return _NUMBER_FORMAT_ALIASES.get(text.lower(), _NUMBER_FORMAT_ALIASES.get(text, text))


def _extract_table_shape(message: str) -> tuple[int, int] | None:
    text = str(message or "").lower()
    m = re.search(r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})\s*(?:표|테이블|table)", text)
    if not m:
        m = re.search(r"(\d{1,3})\s*행\s*(\d{1,3})\s*열\s*(?:표|테이블|table)", text)
    if not m:
        return None
    rows = max(1, min(100, int(m.group(1))))
    cols = max(1, min(50, int(m.group(2))))
    return rows, cols


def _preferred_range(ctx: ValidationContext) -> str:
    return _normalize_range_text(ctx.context_range) or _normalize_range_text(ctx.recent_range)


def _unwrap_operator_payload(op: Any) -> Any:
    """플래너가 operator에 {'operator': '>=', 'value': 1100} 같은 객체를 넣는 경우를 편다."""
    if isinstance(op, dict):
        for key in ("operator", "op", "comparator", "comparison", "condition", "type"):
            if op.get(key) is not None:
                return op.get(key)
    return op


def _operator_payload_value(op: Any) -> Any:
    """operator 객체 안에 같이 들어온 기준값을 꺼낸다."""
    if isinstance(op, dict):
        for key in ("value", "threshold", "number", "criteria"):
            if op.get(key) is not None:
                return op.get(key)
    return None


def _coerce_operator(op: Any) -> str:
    raw = str(_unwrap_operator_payload(op) or "").strip()
    aliases = {
        ">": ">",
        ">=": ">=",
        "<": "<",
        "<=": "<=",
        "==": "==",
        "!=": "!=",
        "eq": "==",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "이상": ">=",
        "초과": ">",
        "이하": "<=",
        "미만": "<",
        "같음": "==",
        "다름": "!=",
        "greater than or equal": ">=",
        "greater than or equal to": ">=",
        "greater than": ">",
        "less than or equal": "<=",
        "less than or equal to": "<=",
        "less than": "<",
        "equal": "==",
        "equals": "==",
        "not equal": "!=",
        "at least": ">=",
        "at most": "<=",
    }
    # greater_than / GREATER THAN / greater-than 을 모두 같은 키로 본다.
    normalized = re.sub(r"[\s_\-]+", " ", raw.lower()).strip()
    resolved = aliases.get(normalized)
    if resolved:
        return resolved
    # LLM이 ">=1100"처럼 연산자와 기준값을 붙여서 내보내는 경우가 있다.
    merged = re.match(r"^(>=|<=|==|!=|>|<)\s*-?\d", raw)
    if merged:
        return merged.group(1)
    return raw


def _validate_action(action: str) -> None:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")


# 열 단위 도구는 기준 열을 `column`으로 받는다. 플래너는 같은 뜻으로 column_name·key_column·
# 열이름을 번갈아 쓴다. 여기서 하나로 모아두지 않으면 열 이름이 빈 문자열로 내려가
# "첫 번째 열"이 조용히 선택된다.
_COLUMN_TOOL_ACTIONS = frozenset(
    {
        "excel_live.sort_rows",
        "excel_live.drop_column",
        "excel_live.rename_column",
        "excel_live.add_column",
        "excel_live.group_by_aggregate",
        "excel_live.calculate_column_stat",
    }
)
_COLUMN_ALIASES = (
    "column",
    "column_name",
    "key_column",
    "group_column",
    "target_column",
    "열",
    "열이름",
)
_NEW_NAME_ALIASES = ("new_name", "new_column_name", "rename_to", "to", "new_header")
_ADD_NAME_ALIASES = ("name", "column_name", "new_name", "header", "column")


def _first_text(params: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = str(params.get(key) or "").strip()
        if text:
            return text
    return ""


def _normalize_column_tool_step(action: str, params: dict[str, Any], reason: str) -> PlanStep:
    sheet_name = str(params.get("sheet_name") or "").strip() or None

    if action == "excel_live.add_column":
        name = _first_text(params, _ADD_NAME_ALIASES)
        if not name:
            raise ValueError("add_column에는 새 열 이름이 필요합니다.")
        formula = str(params.get("formula_a1") or params.get("formula") or "").strip()
        out = {"name": name, "formula_a1": formula or None, "sheet_name": sheet_name}
        return PlanStep(action=action, params=out, reason=reason)

    column = _first_text(params, _COLUMN_ALIASES)
    if not column:
        raise ValueError(f"{action}에는 기준 열(column)이 필요합니다.")

    if action == "excel_live.rename_column":
        new_name = _first_text(params, _NEW_NAME_ALIASES)
        if not new_name:
            raise ValueError("rename_column에는 new_name이 필요합니다.")
        out = {"column": column, "new_name": new_name, "sheet_name": sheet_name}
    elif action == "excel_live.sort_rows":
        order = str(params.get("order") or "asc").strip().lower()
        descending = order.startswith("desc") or order in {"내림차순", "내림"}
        out = {"column": column, "order": "desc" if descending else "asc", "sheet_name": sheet_name}
    elif action == "excel_live.group_by_aggregate":
        value = _first_text(params, ("value_column", "value_field", "value"))
        out = {
            "group_column": column,
            "agg": str(params.get("agg") or "sum").strip().lower(),
            "value_column": value or None,
            "sheet_name": sheet_name,
        }
    elif action == "excel_live.calculate_column_stat":
        out = {
            "column": column,
            "stat": str(params.get("stat") or params.get("agg") or "sum").strip().lower(),
            "sheet_name": sheet_name,
        }
    else:  # drop_column
        out = {"column": column, "sheet_name": sheet_name}
    return PlanStep(action=action, params=out, reason=reason)


_SHEET_TARGETABLE_ACTIONS = frozenset(
    {
        "excel_live.write_range",
        "excel_live.create_table",
        "excel_live.fill_range",
        "excel_live.apply_border",
        "excel_live.set_formula",
        "excel_live.find_replace",
        "excel_live.merge_cells",
        "excel_live.unmerge_cells",
        "excel_live.freeze_panes",
        "excel_live.autofit_columns",
        "excel_live.define_named_range",
        "excel_live.set_print_area",
        "excel_live.add_cell_comment",
        "excel_live.apply_color_scale",
        "excel_live.apply_data_bar",
        "excel_live.set_number_format",
    }
)


def _validate_step(step: PlanStep, ctx: ValidationContext) -> PlanStep:
    action = str(step.action or "").strip()
    params = dict(step.params or {})
    reason = str(step.reason or "").strip()
    _validate_action(action)

    preferred_range = _preferred_range(ctx)
    lowered = str(ctx.message or "").lower()
    incoming_sheet_name = str(params.get("sheet_name") or "").strip()

    validated = _validate_step_body(action, params, reason, ctx, preferred_range, lowered)

    # write_range/set_formula 등의 분기는 params를 새로 지어 반환하면서 sheet_name을 빠뜨린다.
    # 바인더가 "방금 만든 시트로 이어서 써라"고 정한 값이 여기서 조용히 사라지면
    # 실행은 활성 시트(대개 원본)에 떨어진다. 원래 있던 값을 그대로 되살린다.
    if (
        incoming_sheet_name
        and action in _SHEET_TARGETABLE_ACTIONS
        and "sheet_name" not in validated.params
    ):
        validated.params["sheet_name"] = incoming_sheet_name
    return validated


def _validate_step_body(
    action: str,
    params: dict[str, Any],
    reason: str,
    ctx: ValidationContext,
    preferred_range: str | None,
    lowered: str,
) -> PlanStep:

    if action == "excel_live.clarify":
        # 되묻기는 실행할 게 없고 질문 한 줄이 전부다. 질문이 비면 라우터가
        # "무엇을 할까요?" 수준의 빈 되묻기를 내보내게 되므로 계획 자체를 반려한다.
        question = str(params.get("question") or params.get("follow_up_question") or "").strip()
        if not question:
            raise ValueError("clarify에는 question이 필요합니다.")
        return PlanStep(action=action, params={"question": question[:240]}, reason=reason)

    if action == "excel_live.select_workbook":
        workbook = str(params.get("workbook_id") or params.get("name") or "").strip()
        if not workbook:
            raise ValueError("select_workbook에는 workbook_id가 필요합니다.")
        return PlanStep(action=action, params={"workbook_id": workbook}, reason=reason)

    if action == "excel_live.list_sheets":
        workbook = str(params.get("workbook_id") or "").strip() or None
        out_params: dict[str, Any] = {}
        if workbook:
            out_params["workbook_id"] = workbook
        return PlanStep(action=action, params=out_params, reason=reason)

    if action == "excel_live.select_sheet":
        sheet_name = str(params.get("sheet_name") or params.get("name") or "").strip()
        if not sheet_name:
            raise ValueError("select_sheet.sheet_name이 필요합니다.")
        workbook = str(params.get("workbook_id") or "").strip() or None
        out_params: dict[str, Any] = {"sheet_name": sheet_name}
        if workbook:
            out_params["workbook_id"] = workbook
        return PlanStep(action=action, params=out_params, reason=reason)

    if action == "excel_live.create_sheet":
        sheet_name = str(params.get("sheet_name") or params.get("name") or "").strip()
        if not sheet_name:
            raise ValueError("create_sheet.sheet_name이 필요합니다.")
        workbook = str(params.get("workbook_id") or "").strip() or None
        out_params: dict[str, Any] = {
            "sheet_name": sheet_name,
            "make_active": bool(params.get("make_active", True)),
        }
        if workbook:
            out_params["workbook_id"] = workbook
        return PlanStep(action=action, params=out_params, reason=reason)

    if action == "excel_live.read_range":
        range_ref = _normalize_range_text(params.get("range_ref"))
        if not range_ref:
            range_ref = preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(action=action, params={"range_ref": range_ref}, reason=reason)

    if action == "excel_live.write_range":
        # LLM이 표 생성 의도를 write_range + 불완전 파라미터로 내는 경우를 복구한다.
        values_2d = params.get("values_2d")
        if not isinstance(values_2d, list):
            shape = _extract_table_shape(ctx.message)
            if shape and any(t in lowered for t in ["표", "테이블", "table", "만들", "생성", "create"]):
                rows, cols = shape
                return PlanStep(
                    action="excel_live.create_table",
                    params={
                        "start_cell": "__ACTIVE_CELL__",
                        "rows": rows,
                        "cols": cols,
                        "with_border": True,
                    },
                    reason=reason or "표 생성 의도 자동 복구",
                )
            raise ValueError("write_range에는 values_2d(2차원 배열)가 필요합니다.")
        start_cell = _normalize_range_text(params.get("start_cell")) or "__ACTIVE_CELL__"
        normalized_rows: list[list[Any]] = []
        for row in values_2d:
            if isinstance(row, list):
                normalized_rows.append(row)
            else:
                normalized_rows.append([row])
        return PlanStep(
            action=action,
            params={"start_cell": start_cell, "values_2d": normalized_rows},
            reason=reason,
        )

    if action == "excel_live.create_table":
        rows_raw = params.get("rows", 5)
        cols_raw = params.get("cols", 5)
        rows = max(1, min(100, int(5 if rows_raw is None else rows_raw)))
        cols = max(1, min(50, int(5 if cols_raw is None else cols_raw)))
        start_cell = _normalize_range_text(params.get("start_cell")) or "A1"
        with_border = bool(params.get("with_border", True))
        raw_headers = params.get("headers")
        headers: list[str] = []
        if isinstance(raw_headers, list):
            headers = [str(h).strip() for h in raw_headers if str(h).strip()]
        if headers:
            headers = headers[:cols]
            if len(headers) < cols:
                headers.extend([""] * (cols - len(headers)))
        return PlanStep(
            action=action,
            params={
                "start_cell": start_cell,
                "rows": rows,
                "cols": cols,
                "with_border": with_border,
                "headers": headers,
            },
            reason=reason,
        )

    if action == "excel_live.highlight_by_condition":
        target_range = _normalize_range_text(params.get("target_range")) or _normalize_range_text(
            params.get("range_ref")
        )
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "A:Z"
        operator_raw = params.get("operator") or params.get("condition") or ">="
        operator = _coerce_operator(operator_raw)
        threshold_raw = params.get("threshold", params.get("value", None))
        if threshold_raw is None:
            # operator가 객체로 온 경우 그 안의 value를 먼저 본다.
            threshold_raw = _operator_payload_value(operator_raw)
        if threshold_raw is None:
            # ">=1100"처럼 연산자에 붙어 온 기준값을 회수한다.
            embedded = re.search(r"-?\d+(?:\.\d+)?", str(operator_raw))
            if embedded is None:
                # 여기서 0을 채우면 ">= 0"이 되어 모든 행이 칠해진다. 실행기는 "칠한
                # 셀 1개 이상"이라 성공을 보고하고 사후조건 검증도 통과하므로, 사용자만
                # 전부 노랗게 칠해진 시트를 보게 된다. 기준값은 지어내면 안 된다.
                raise ValueError("highlight_by_condition에는 기준값(threshold)이 필요합니다.")
            threshold_raw = embedded.group(0)
        try:
            threshold = float(threshold_raw)
        except Exception as exc:
            raise ValueError("highlight_by_condition.threshold는 숫자여야 합니다.") from exc
        fill_color = str(params.get("fill_color") or params.get("color") or "#FFFF00").strip()
        # 기준이 같은 행의 다른 열이면(재고 ≤ 재주문점) 열 문자로만 받는다.
        compare_column = str(params.get("compare_column") or "").strip().upper()
        if compare_column and not re.fullmatch(r"[A-Z]{1,3}", compare_column):
            raise ValueError("highlight_by_condition.compare_column은 열 문자여야 합니다.")
        step_params: dict[str, Any] = {
            "target_range": target_range,
            "operator": operator,
            "threshold": threshold,
            "fill_color": fill_color,
        }
        if compare_column:
            step_params["compare_column"] = compare_column
        return PlanStep(action=action, params=step_params, reason=reason)

    if action == "excel_live.fill_range":
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        fill_color = str(params.get("fill_color") or "#FFFF00").strip()
        return PlanStep(
            action=action,
            params={"target_range": target_range, "fill_color": fill_color},
            reason=reason,
        )

    if action == "excel_live.clear_range":
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(
            action=action,
            params={"target_range": target_range},
            reason=reason,
        )

    if action == "excel_live.apply_border":
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        line_style = str(params.get("line_style") or "continuous").strip().lower()
        weight = str(params.get("weight") or "medium").strip().lower()
        color = str(params.get("color") or "#000000").strip()
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "line_style": line_style,
                "weight": weight,
                "color": color,
            },
            reason=reason,
        )

    if action == "excel_live.set_formula":
        formula_a1 = str(params.get("formula_a1") or "").strip()
        if not formula_a1 or not formula_a1.startswith("="):
            raise ValueError("set_formula.formula_a1은 '='로 시작해야 합니다.")
        range_ref = _normalize_range_text(params.get("range_ref")) or preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(
            action=action,
            params={"range_ref": range_ref, "formula_a1": formula_a1},
            reason=reason,
        )

    if action == "excel_live.verify_formula_result":
        range_ref = _normalize_range_text(params.get("range_ref", params.get("target_range")))
        if not range_ref:
            range_ref = preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(
            action=action,
            params={"range_ref": range_ref},
            reason=reason,
        )

    if action == "excel_live.find_replace":
        find_text = str(params.get("find_text") or params.get("find") or "").strip()
        if not find_text:
            raise ValueError("find_replace.find_text가 필요합니다.")
        replace_text = str(params.get("replace_text") or params.get("replace") or "")
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__USED_RANGE__"
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "find_text": find_text,
                "replace_text": replace_text,
                "match_case": bool(params.get("match_case", False)),
                "whole_cell": bool(params.get("whole_cell", False)),
            },
            reason=reason,
        )

    if action in {"excel_live.merge_cells", "excel_live.unmerge_cells"}:
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(action=action, params={"target_range": target_range}, reason=reason)

    if action == "excel_live.freeze_panes":
        freeze_at = _normalize_range_text(params.get("freeze_at") or params.get("cell_ref")) or "A2"
        return PlanStep(action=action, params={"freeze_at": freeze_at}, reason=reason)

    if action == "excel_live.autofit_columns":
        target_range = _normalize_range_text(params.get("target_range")) or "__USED_RANGE__"
        return PlanStep(action=action, params={"target_range": target_range}, reason=reason)

    if action == "excel_live.set_print_area":
        print_area = _normalize_range_text(params.get("print_area") or params.get("target_range")) or None
        orientation = str(params.get("orientation") or "").strip().lower() or None
        fit_to_page = bool(params.get("fit_to_page", False))
        return PlanStep(
            action=action,
            params={"print_area": print_area, "orientation": orientation, "fit_to_page": fit_to_page},
            reason=reason,
        )

    if action == "excel_live.add_cell_comment":
        text = str(params.get("text") or params.get("comment") or "").strip()
        if not text:
            raise ValueError("add_cell_comment.text가 필요합니다.")
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_CELL__"
        author = str(params.get("author") or "OfficeClaw AI").strip() or "OfficeClaw AI"
        return PlanStep(
            action=action,
            params={"target_range": target_range, "text": text, "author": author},
            reason=reason,
        )

    if action in {"excel_live.apply_color_scale", "excel_live.apply_data_bar"}:
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        out: dict[str, Any] = {"target_range": target_range}
        if action == "excel_live.apply_color_scale":
            out["min_color"] = str(params.get("min_color") or "#F8696B")
            out["mid_color"] = str(params.get("mid_color") or "#FFEB84")
            out["max_color"] = str(params.get("max_color") or "#63BE7B")
        else:
            out["color"] = str(params.get("color") or "#638EC6")
        return PlanStep(action=action, params=out, reason=reason)

    if action == "excel_live.set_number_format":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        format_code = _normalize_number_format(params.get("format_code") or params.get("format") or "")
        if not format_code:
            raise ValueError("set_number_format.format_code가 필요합니다.")
        return PlanStep(action=action, params={"target_range": target_range, "format_code": format_code}, reason=reason)

    if action == "excel_live.define_named_range":
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("define_named_range.name이 필요합니다.")
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(
            action=action,
            params={"name": name, "target_range": target_range},
            reason=reason,
        )

    if action == "excel_live.sort_range":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        key_column = params.get("key_column", params.get("column", 1))
        order = str(params.get("order") or "asc").strip().lower()
        if order in {"descending", "desc", "내림차순"}:
            order = "desc"
        else:
            order = "asc"
        has_header = bool(params.get("has_header", True))
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "key_column": key_column,
                "order": order,
                "has_header": has_header,
            },
            reason=reason,
        )

    if action == "excel_live.filter_rows":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        column = params.get("column", params.get("key_column", 1))
        operator = _coerce_operator(params.get("operator") or "==")
        value = params.get("value")
        if value is None:
            raise ValueError("filter_rows.value는 필수입니다.")
        has_header = bool(params.get("has_header", True))
        mode = str(params.get("mode") or "keep").strip().lower()
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "column": column,
                "operator": operator,
                "value": value,
                "has_header": has_header,
                "mode": "remove" if mode in {"remove", "exclude", "drop"} else "keep",
            },
            reason=reason,
        )

    if action == "excel_live.dedupe_rows":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        key_columns = params.get("key_columns", params.get("columns", []))
        if not isinstance(key_columns, list):
            key_columns = []
        has_header = bool(params.get("has_header", True))
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "key_columns": key_columns,
                "has_header": has_header,
            },
            reason=reason,
        )

    if action == "excel_live.find_duplicates":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        key_columns = params.get("key_columns", params.get("columns", []))
        if not isinstance(key_columns, list):
            key_columns = [key_columns] if key_columns else []
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "key_columns": key_columns,
                "has_header": bool(params.get("has_header", True)),
                "output_sheet": str(params.get("output_sheet") or "").strip() or None,
            },
            reason=reason,
        )

    if action == "excel_live.recalculate":
        return PlanStep(action=action, params={}, reason=reason)

    if action == "excel_live.export_pdf":
        return PlanStep(
            action=action,
            params={"output_path": str(params.get("output_path") or "").strip() or None},
            reason=reason,
        )

    if action == "excel_live.pivot_table":
        source_range = _normalize_range_text(params.get("source_range", params.get("target_range")))
        if not source_range:
            source_range = preferred_range or "__ACTIVE_SELECTION__"
        row_field = params.get("row_field", 1)
        value_field = params.get("value_field", 2)
        column_field = params.get("column_field")
        agg = str(params.get("agg") or "sum").strip().lower()
        if agg not in {"sum", "avg", "count"}:
            agg = "sum"
        output_sheet = str(params.get("output_sheet") or ctx.sheet_name or "").strip() or None
        output_start = _normalize_range_text(params.get("output_start")) or "A1"
        has_header = bool(params.get("has_header", True))
        return PlanStep(
            action=action,
            params={
                "source_range": source_range,
                "row_field": row_field,
                "value_field": value_field,
                "column_field": column_field,
                "agg": agg,
                # 결과 시트를 먼저 만드는 계획에서 활성 시트가 바뀌어도 원본을 잃지 않도록 남긴다.
                "source_sheet": str(params.get("source_sheet") or "").strip() or None,
                "output_sheet": output_sheet,
                "output_start": output_start,
                "has_header": has_header,
            },
            reason=reason,
        )

    if action == "excel_live.create_chart":
        source_range = _normalize_range_text(params.get("source_range", params.get("target_range")))
        if not source_range:
            source_range = preferred_range or "__ACTIVE_SELECTION__"
        chart_type = str(params.get("chart_type") or "line").strip().lower()
        if chart_type not in {"line", "bar", "column", "pie", "donut"}:
            chart_type = "line"
        title = str(params.get("title") or "데이터 차트").strip()
        output_sheet = str(params.get("output_sheet") or ctx.sheet_name or "").strip() or None
        return PlanStep(
            action=action,
            params={
                "source_range": source_range,
                "chart_type": chart_type,
                "title": title,
                "output_sheet": output_sheet,
            },
            reason=reason,
        )

    if action == "excel_live.validate_data":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        checks = params.get("checks")
        if not isinstance(checks, list):
            checks = ["empty", "negative", "outlier"]
        checks = [str(c).strip().lower() for c in checks if str(c).strip()]
        if not checks:
            checks = ["empty", "negative", "outlier"]
        has_header = bool(params.get("has_header", True))
        date_min = str(params.get("date_min") or "").strip() or None
        date_max = str(params.get("date_max") or "").strip() or None
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "checks": checks,
                "has_header": has_header,
                "date_min": date_min,
                "date_max": date_max,
            },
            reason=reason,
        )

    if action == "excel_live.protect_sheet":
        unlock_range = _normalize_range_text(params.get("unlock_range")) or None
        password = str(params.get("password") or "").strip() or None
        return PlanStep(
            action=action,
            params={
                "password": password,
                "lock_formula_cells": bool(params.get("lock_formula_cells", True)),
                "unlock_range": unlock_range,
            },
            reason=reason,
        )

    if action == "excel_live.set_data_validation":
        target_range = _normalize_range_text(params.get("target_range")) or preferred_range or "__ACTIVE_SELECTION__"
        validation_type = str(params.get("validation_type") or "list").strip().lower()
        source = str(params.get("source") or "").strip() or None
        minimum = params.get("minimum")
        maximum = params.get("maximum")
        allow_blank = bool(params.get("allow_blank", True))
        show_error = bool(params.get("show_error", True))
        error_message = str(params.get("error_message") or "").strip() or None
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "validation_type": validation_type,
                "source": source,
                "minimum": minimum,
                "maximum": maximum,
                "allow_blank": allow_blank,
                "show_error": show_error,
                "error_message": error_message,
            },
            reason=reason,
        )

    if action in _COLUMN_TOOL_ACTIONS:
        return _normalize_column_tool_step(action, params, reason)

    if action == "excel_live.consolidate_sheets":
        source_sheets = params.get("source_sheets")
        if not isinstance(source_sheets, list) or not source_sheets:
            raise ValueError("consolidate_sheets.source_sheets는 비어있지 않은 배열이어야 합니다.")
        return PlanStep(
            action=action,
            params={
                "source_sheets": [str(s).strip() for s in source_sheets if str(s).strip()],
                "output_sheet": str(params.get("output_sheet") or "통합결과").strip(),
                "include_header_once": bool(params.get("include_header_once", True)),
                "add_source_sheet_col": bool(params.get("add_source_sheet_col", True)),
            },
            reason=reason,
        )

    if action == "excel_live.consolidate_workbooks_from_folder":
        folder_path = str(params.get("folder_path") or "").strip()
        if not folder_path:
            raise ValueError("consolidate_workbooks_from_folder.folder_path가 필요합니다.")
        return PlanStep(
            action=action,
            params={
                "folder_path": folder_path,
                "pattern": str(params.get("pattern") or "*.xlsx").strip(),
                "source_sheet": str(params.get("source_sheet") or "").strip() or None,
                "output_sheet": str(params.get("output_sheet") or "파일통합결과").strip(),
                "include_header_once": bool(params.get("include_header_once", True)),
                "add_source_file_col": bool(params.get("add_source_file_col", True)),
            },
            reason=reason,
        )

    if action == "excel_live.refresh_power_query":
        return PlanStep(action=action, params={}, reason=reason)

    if action == "excel_live.run_vba_macro":
        macro_name = str(params.get("macro_name") or "").strip()
        if not macro_name:
            raise ValueError("run_vba_macro.macro_name이 필요합니다.")
        args = params.get("args", [])
        if not isinstance(args, list):
            args = []
        return PlanStep(action=action, params={"macro_name": macro_name, "args": args}, reason=reason)

    if action == "excel_live.compare_ranges":
        left_sheet = str(params.get("left_sheet") or ctx.sheet_name or "").strip()
        right_sheet = str(params.get("right_sheet") or ctx.sheet_name or "").strip()
        left_range = _normalize_range_text(params.get("left_range"))
        right_range = _normalize_range_text(params.get("right_range"))
        if not left_sheet or not right_sheet or not left_range or not right_range:
            raise ValueError("compare_ranges에는 left/right sheet와 range가 필요합니다.")
        return PlanStep(
            action=action,
            params={
                "left_sheet": left_sheet,
                "left_range": left_range,
                "right_sheet": right_sheet,
                "right_range": right_range,
                "output_sheet": str(params.get("output_sheet") or "").strip() or None,
            },
            reason=reason,
        )

    if action == "excel_live.forecast_linear":
        source_range = _normalize_range_text(params.get("source_range")) or preferred_range or "__ACTIVE_SELECTION__"
        horizon = max(1, min(36, int(params.get("horizon", 3) or 3)))
        output_sheet = str(params.get("output_sheet") or ctx.sheet_name or "").strip() or None
        output_start = _normalize_range_text(params.get("output_start")) or "A1"
        return PlanStep(
            action=action,
            params={
                "source_range": source_range,
                "horizon": horizon,
                "output_sheet": output_sheet,
                "output_start": output_start,
            },
            reason=reason,
        )

    if action in {"excel_live.list_workbooks", "excel_live.save_workbook"}:
        return PlanStep(action=action, params={}, reason=reason)

    return PlanStep(action=action, params=params, reason=reason)


def validate_plan(
    steps: list[PlanStep],
    *,
    context: ValidationContext,
) -> list[PlanStep]:
    if not steps:
        return []
    validated: list[PlanStep] = []
    for step in steps:
        validated.append(_validate_step(step, context))
    return validated

