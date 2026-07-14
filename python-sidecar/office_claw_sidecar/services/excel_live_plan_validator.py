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


SUPPORTED_ACTIONS = {
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
    "excel_live.save_workbook",
}

EDIT_ACTIONS = {
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
    "excel_live.save_workbook",
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


def _coerce_operator(op: Any) -> str:
    raw = str(op or "").strip()
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
    }
    return aliases.get(raw.lower(), raw)


def _validate_action(action: str) -> None:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")


def _validate_step(step: PlanStep, ctx: ValidationContext) -> PlanStep:
    action = str(step.action or "").strip()
    params = dict(step.params or {})
    reason = str(step.reason or "").strip()
    _validate_action(action)

    preferred_range = _preferred_range(ctx)
    lowered = str(ctx.message or "").lower()

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
        operator = _coerce_operator(params.get("operator") or params.get("condition") or ">=")
        threshold_raw = params.get("threshold", params.get("value", 0))
        try:
            threshold = float(threshold_raw)
        except Exception as exc:
            raise ValueError("highlight_by_condition.threshold는 숫자여야 합니다.") from exc
        fill_color = str(params.get("fill_color") or params.get("color") or "#FFFF00").strip()
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "operator": operator,
                "threshold": threshold,
                "fill_color": fill_color,
            },
            reason=reason,
        )

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
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "column": column,
                "operator": operator,
                "value": value,
                "has_header": has_header,
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

