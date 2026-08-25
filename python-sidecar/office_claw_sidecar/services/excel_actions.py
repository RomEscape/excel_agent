"""
Excel 액션 실행 디스패처 — tool_registry 액션 이름 → ExcelLiveService 호출.

라우터(/excel-live)와 tool-calling 에이전트(excel_tool_agent)가 공유하는
단일 실행 경로. 파라미터 정규화(워크북/시트 해석, __ACTIVE_SELECTION__
센티널 처리, 수식 검증)를 이 모듈이 소유한다.
"""

from __future__ import annotations

from typing import Any

from office_claw_sidecar.services.excel_live_service import (
    ExcelConnectionError,
    ExcelLiveError,
    WorkbookNotFoundError,
    get_excel_live_service,
)

# 사용자의 현재 선택 범위/셀을 뜻하는 센티널 값
ACTIVE_SELECTION = "__ACTIVE_SELECTION__"
ACTIVE_CELL = "__ACTIVE_CELL__"


def resolve_sheet_name(service, workbook_id: str | None, sheet_name: str | None) -> str:
    if sheet_name:
        return sheet_name
    rows = service.list_workbooks()
    if not rows:
        raise ExcelConnectionError("열린 통합문서가 없습니다.")
    # workbook_id가 비면 **선택된 통합문서**의 활성 시트다 — 목록의 첫 파일이 아니다.
    # 2026-08-25 실측(커버리지 v2): GUI는 workbook_id를 항상 null로 보내는데, 이 함수가
    # rows[0](워크스페이스에서 이름순 첫 파일 `_probe_ex14.xlsx`)의 활성 시트 '콘텐츠 일정'을
    # 돌려줘 "비고 열 빼줘"가 "시트를 찾을 수 없습니다: 콘텐츠 일정"으로 죽었다. 이름이
    # 우연히 겹치면(Sheet1 등) 엉뚱한 시트에 조용히 실행되는 부류다.
    target = workbook_id or (service.get_selected_workbook_id() if hasattr(service, "get_selected_workbook_id") else None)
    if target:
        lowered = str(target).lower()
        for row in rows:
            if str(row.get("workbook_id", "")).lower() == lowered or str(row.get("name", "")).lower() == lowered:
                return row.get("active_sheet") or "Sheet1"
        # 목록에 없는 통합문서(워크스페이스 폴더 밖 파일)면 그 통합문서에 직접 묻는다 —
        # 여기서 rows[0]으로 떨어지면 남의 파일 시트가 된다(2026-08-25 재측정에서 재발).
        if hasattr(service, "list_sheets"):
            try:
                info = service.list_sheets(target)
                active = str((info or {}).get("active_sheet") or "").strip()
                if active:
                    return active
            except Exception:
                pass
    return rows[0].get("active_sheet") or "Sheet1"


def resolve_workbook_id(service, workbook_id: str | None) -> str:
    if workbook_id:
        return workbook_id
    selected = service.get_selected_workbook_id()
    if selected:
        return selected
    rows = service.list_workbooks()
    if not rows:
        raise WorkbookNotFoundError("열린 통합문서가 없습니다.")
    return rows[0]["workbook_id"]


def top_left_cell(range_ref: str) -> str:
    text = str(range_ref or "").strip().upper()
    if not text:
        return "A1"
    return text.split(":")[0]


def execute_excel_action(
    *,
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
) -> dict[str, Any]:
    """레지스트리 액션 이름으로 ExcelLiveService 작업을 실행한다."""
    service = get_excel_live_service()

    if action == "excel_live.list_workbooks":
        return {"workbooks": service.list_workbooks()}

    if action == "excel_live.select_workbook":
        target = params.get("workbook_id") or params.get("name") or workbook_id
        if not isinstance(target, str) or not target.strip():
            raise WorkbookNotFoundError("select_workbook에는 workbook_id 또는 name이 필요합니다.")
        return service.select_workbook(target.strip())

    if action == "excel_live.read_range":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == ACTIVE_SELECTION:
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        return service.read_range(resolved_wb, resolved_sheet, range_ref)

    if action == "excel_live.write_range":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        start_cell = str(params.get("start_cell", "")).strip().upper()
        if not start_cell or start_cell in {ACTIVE_CELL, ACTIVE_SELECTION}:
            selected = service.get_active_selection_ref(resolved_wb, resolved_sheet)
            start_cell = top_left_cell(selected)
        values_2d = params.get("values_2d")
        if not isinstance(values_2d, list):
            raise ExcelLiveError("write_range에는 values_2d(2차원 배열)가 필요합니다.")
        normalized_rows: list[list[Any]] = []
        for row in values_2d:
            if isinstance(row, list):
                normalized_rows.append(row)
            else:
                normalized_rows.append([row])
        return service.write_range(resolved_wb, resolved_sheet, start_cell, normalized_rows)

    if action == "excel_live.highlight_by_condition":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "A:A")).strip().upper()
        operator = str(params.get("operator", ">=")).strip()
        threshold = float(params.get("threshold", 0))
        fill_color = str(params.get("fill_color") or "#FFFF00")
        return service.highlight_by_condition(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            operator=operator,
            threshold=threshold,
            fill_color=fill_color,
        )

    if action == "excel_live.apply_border":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        target_range = str(params.get("target_range", "")).strip().upper()
        if not target_range or target_range == ACTIVE_SELECTION:
            target_range = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        line_style = str(params.get("line_style", "continuous")).strip().lower()
        weight = str(params.get("weight", "medium")).strip().lower()
        color = str(params.get("color", "#000000")).strip()
        return service.apply_border(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            target_range=target_range,
            line_style=line_style,
            weight=weight,
            color=color,
        )

    if action == "excel_live.set_formula":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        range_ref = str(params.get("range_ref", "")).strip().upper()
        if not range_ref or range_ref == ACTIVE_SELECTION:
            range_ref = service.get_active_selection_ref(resolved_wb, resolved_sheet)
        formula_a1 = str(params.get("formula_a1", "")).strip()
        if not formula_a1.startswith("="):
            raise ExcelLiveError("formula_a1은 '='로 시작해야 합니다.")
        return service.set_formula(resolved_wb, resolved_sheet, range_ref, formula_a1)

    if action == "excel_live.filter_rows":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.filter_rows(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            column=str(params.get("column", "")).strip(),
            operator=str(params.get("operator", "")).strip(),
            value=params.get("value"),
        )

    if action == "excel_live.sort_rows":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.sort_rows(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            column=str(params.get("column", "")).strip(),
            order=str(params.get("order") or "asc"),
        )

    if action == "excel_live.dedupe_rows":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        columns = params.get("columns")
        return service.dedupe_rows(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            columns=[str(c) for c in columns] if isinstance(columns, list) else None,
        )

    if action == "excel_live.drop_column":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.drop_column(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            column=str(params.get("column", "")).strip(),
        )

    if action == "excel_live.rename_column":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        return service.rename_column(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            column=str(params.get("column", "")).strip(),
            new_name=str(params.get("new_name", "")).strip(),
        )

    if action == "excel_live.add_column":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        formula_a1 = params.get("formula_a1")
        return service.add_column(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            name=str(params.get("name", "")).strip(),
            formula_a1=str(formula_a1).strip() if formula_a1 else None,
        )

    if action == "excel_live.group_by_aggregate":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        value_column = params.get("value_column")
        return service.group_by_aggregate(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            group_column=str(params.get("group_column", "")).strip(),
            agg=str(params.get("agg") or "sum"),
            value_column=str(value_column).strip() if value_column else None,
        )

    if action == "excel_live.calculate_column_stat":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        resolved_sheet = resolve_sheet_name(service, resolved_wb, sheet_name)
        column = str(params.get("column", "")).strip()
        if not column:
            raise ExcelLiveError("calculate_column_stat에는 column(머리글 이름 또는 열 문자)이 필요합니다.")
        stat = str(params.get("stat", "sum")).strip().lower()
        return service.calculate_column_stat(
            workbook_id=resolved_wb,
            sheet_name=resolved_sheet,
            column=column,
            stat=stat,
        )

    if action == "excel_live.save_workbook":
        resolved_wb = resolve_workbook_id(service, workbook_id)
        return service.save_workbook(resolved_wb)

    raise ExcelLiveError(f"지원하지 않는 action: {action}")
