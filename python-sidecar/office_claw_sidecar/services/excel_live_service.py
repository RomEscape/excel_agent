"""
Excel Live Service — 실행 중인 Excel 실시간 제어용 서비스(xlwings).

MVP Day 1 범위:
  - Excel 연결 가능 여부 확인
  - 열린 통합문서 목록 조회
  - 통합문서 선택/조회 상태 관리
  - 기본 범위 읽기(read_range)

주의:
  - Windows/macOS + Excel Desktop 환경을 전제로 한다.
  - xlwings 의존성은 lazy import로 처리하여 비 Excel 테스트 환경에서도
    모듈 import 자체는 실패하지 않도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any

from office_claw_sidecar.services.excel_header_lexicon import resolve_header


class ExcelLiveError(Exception):
    """Excel Live 서비스 기본 예외."""


class ExcelDependencyError(ExcelLiveError):
    """xlwings 또는 OS별 자동화 의존성 누락/import 실패."""


class ExcelConnectionError(ExcelLiveError):
    """실행 중인 Excel 인스턴스 연결 실패."""


class WorkbookNotFoundError(ExcelLiveError):
    """요청한 통합문서를 찾지 못함."""


class WorksheetNotFoundError(ExcelLiveError):
    """요청한 시트를 찾지 못함."""


class AmbiguousWorkbookError(ExcelLiveError):
    """대상 통합문서를 특정하지 못함 — 후보가 여럿이라 되물어야 한다.

    대상을 지정하지 않았을 때 아무 파일이나 골라 편집하면, 사용자가 보고 있지도
    않은 통합문서가 조용히 바뀐다. 후보를 들고 되묻기 위해 별도 예외로 둔다.
    """

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass(frozen=True)
class WorkbookInfo:
    workbook_id: str
    name: str
    full_path: str
    active_sheet: str

    def as_dict(self) -> dict[str, str]:
        return {
            "workbook_id": self.workbook_id,
            "name": self.name,
            "full_path": self.full_path,
            "active_sheet": self.active_sheet,
        }


class ExcelLiveService:
    """실행 중인 Excel 제어 서비스(xlwings 기반)."""

    engine = "xlwings"

    def __init__(self, xw_module: Any | None = None) -> None:
        self._xw = xw_module
        self._selected_workbook_id: str | None = None

    def _xw_module(self) -> Any:
        if self._xw is not None:
            return self._xw
        try:
            import xlwings as xw  # type: ignore[import]
        except Exception as exc:  # pragma: no cover - 환경 의존
            raise ExcelDependencyError(
                "xlwings 모듈을 불러올 수 없습니다. "
                "Windows는 pywin32, macOS는 appscript 의존성을 함께 설치해 주세요."
            ) from exc
        self._xw = xw
        return xw

    def _app(self):
        xw = self._xw_module()
        try:
            app = xw.apps.active
        except Exception as exc:  # pragma: no cover - 환경 의존
            raise ExcelConnectionError(
                "실행 중인 Excel 인스턴스에 연결할 수 없습니다. Excel을 먼저 실행해 주세요."
            ) from exc
        if app is None:
            raise ExcelConnectionError(
                "실행 중인 Excel 인스턴스를 찾지 못했습니다. Excel을 먼저 실행해 주세요."
            )
        return app

    def is_available(self) -> bool:
        """Excel 연결 가능 여부를 bool로 반환한다 (예외 미전파)."""
        try:
            self._app()
            return True
        except ExcelLiveError:
            return False

    def list_workbooks(self) -> list[dict[str, str]]:
        """현재 열린 통합문서 목록을 반환한다."""
        app = self._app()
        rows: list[dict[str, str]] = []
        for wb in app.books:
            info = WorkbookInfo(
                workbook_id=self._workbook_id(wb),
                name=str(getattr(wb, "name", "")),
                full_path=str(getattr(wb, "fullname", "") or ""),
                active_sheet=self._active_sheet_name(wb),
            )
            rows.append(info.as_dict())
        return rows

    def select_workbook(self, workbook_id_or_name: str) -> dict[str, Any]:
        """작업 대상 통합문서를 선택하고 선택 결과를 반환한다."""
        wb = self._find_workbook(workbook_id_or_name)
        workbook_id = self._workbook_id(wb)
        self._selected_workbook_id = workbook_id
        return {"selected": True, "workbook_id": workbook_id}

    def get_selected_workbook_id(self) -> str | None:
        return self._selected_workbook_id

    def list_sheets(self, workbook_id: str | None) -> dict[str, Any]:
        """대상 통합문서의 시트 목록과 활성 시트를 반환한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            rows = self.list_workbooks()
            if not rows:
                raise WorkbookNotFoundError("열린 통합문서가 없습니다.")
            target_id = rows[0]["workbook_id"]

        wb = self._find_workbook(target_id)
        sheet_names = [str(getattr(sheet, "name", "") or "") for sheet in wb.sheets]
        return {
            "sheets": sheet_names,
            "count": len(sheet_names),
            "active_sheet": self._active_sheet_name(wb),
        }

    def select_sheet(
        self,
        workbook_id: str | None,
        sheet_name: str,
    ) -> dict[str, Any]:
        """대상 통합문서에서 작업 시트를 전환한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            rows = self.list_workbooks()
            if not rows:
                raise WorkbookNotFoundError("열린 통합문서가 없습니다.")
            target_id = rows[0]["workbook_id"]

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        try:
            sheet.activate()
        except Exception:
            pass
        return {
            "selected": True,
            "sheet_name": str(getattr(sheet, "name", "") or sheet_name),
            "active_sheet": self._active_sheet_name(wb),
        }

    def create_sheet(
        self,
        workbook_id: str | None,
        sheet_name: str,
        make_active: bool = True,
    ) -> dict[str, Any]:
        """시트를 생성하고(이미 있으면 재사용) 필요 시 활성화한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            rows = self.list_workbooks()
            if not rows:
                raise WorkbookNotFoundError("열린 통합문서가 없습니다.")
            target_id = rows[0]["workbook_id"]

        wb = self._find_workbook(target_id)
        target_name = self._sanitize_sheet_name(sheet_name)
        created = False
        try:
            sheet = self._find_sheet(wb, target_name)
        except WorksheetNotFoundError:
            sheet = wb.sheets.add(name=target_name)
            created = True

        if make_active:
            try:
                sheet.activate()
            except Exception:
                pass

        return {
            "created": created,
            "sheet_name": str(getattr(sheet, "name", "") or target_name),
            "active_sheet": self._active_sheet_name(wb),
        }

    def read_range(
        self,
        workbook_id: str | None,
        sheet_name: str,
        range_ref: str,
    ) -> dict[str, Any]:
        """
        지정 범위를 읽어 2차원 배열 형태로 반환한다.

        반환:
          {
            "values": [[...], ...],
            "address": "A1:C3",
            "row_count": 3,
            "col_count": 3
          }
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError(
                "선택된 통합문서가 없습니다. workbook_id를 지정하거나 select_workbook을 먼저 호출해 주세요."
            )

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = sheet.range(range_ref)
        raw = rng.options(ndim=2).value
        values = self._normalize_values(raw)
        if values == []:
            rows_obj = getattr(rng, "rows", None)
            cols_obj = getattr(rng, "columns", None)
            row_count = int(getattr(rows_obj, "count", 1) or 1)
            col_count = int(getattr(cols_obj, "count", 1) or 1)
            values = [[None for _ in range(col_count)] for _ in range(row_count)]

        row_count = len(values)
        col_count = len(values[0]) if values else 0
        return {
            "values": values,
            "address": str(range_ref),
            "row_count": row_count,
            "col_count": col_count,
        }

    def get_range_snapshot(
        self,
        workbook_id: str | None,
        sheet_name: str | None,
        range_ref: str,
    ) -> dict[str, Any]:
        """
        범위 상태 스냅샷(검증용)을 반환한다.
        - row/col 크기
        - 값이 채워진 셀 개수
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        if sheet_name:
            resolved_sheet = self._find_sheet(wb, sheet_name)
            resolved_sheet_name = str(getattr(resolved_sheet, "name", "") or "")
        else:
            resolved_sheet = wb.sheets.active
            resolved_sheet_name = str(getattr(resolved_sheet, "name", "") or "")
        data = self.read_range(target_id, resolved_sheet_name, range_ref)
        values = data.get("values", [])
        filled = 0
        for row in values:
            for v in row:
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
                filled += 1
        return {
            "address": data.get("address", range_ref),
            "row_count": int(data.get("row_count", 0) or 0),
            "col_count": int(data.get("col_count", 0) or 0),
            "filled_cells": filled,
        }

    def write_range(
        self,
        workbook_id: str | None,
        sheet_name: str,
        start_cell: str,
        values_2d: list[list[Any]],
    ) -> dict[str, Any]:
        """지정 시작 셀 기준으로 2차원 값을 기록한다."""
        if not values_2d:
            return {"written_cells": 0, "address": start_cell}

        rows = len(values_2d)
        cols = max(len(r) for r in values_2d)
        normalized = [row + [None] * (cols - len(row)) for row in values_2d]

        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = sheet.range(start_cell).resize(rows, cols)
        rng.value = normalized
        return {"written_cells": rows * cols, "address": str(rng.address)}

    def highlight_by_condition(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        operator: str,
        threshold: float,
        fill_color: str = "#FFFF00",
        compare_column: str | None = None,
    ) -> dict[str, Any]:
        """조건에 맞는 셀 배경색을 변경한다.

        compare_column을 주면 고정 기준값 대신 같은 행의 그 열 값과 비교한다
        ("현재고가 재주문점 이하").
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        rgb = self._hex_to_rgb(fill_color)
        compare_letter = str(compare_column or "").strip().upper() or None

        start_row = int(getattr(rng, "row", 1))
        start_col = int(getattr(rng, "column", 1))

        matched = 0
        changed = 0
        for r_idx, row in enumerate(values):
            for c_idx, cell_value in enumerate(row):
                absolute_row = start_row + r_idx
                limit = threshold
                if compare_letter:
                    other = sheet.range(f"{compare_letter}{absolute_row}").value
                    if not isinstance(other, (int, float)) or isinstance(other, bool):
                        continue
                    limit = float(other)
                if self._matches_condition(cell_value, operator, limit):
                    matched += 1
                    absolute_col = start_col + c_idx
                    cell_ref = f"{self._idx_to_col(absolute_col)}{absolute_row}"
                    cell = sheet.range(cell_ref)
                    cell.color = rgb
                    # 흰색/옅은색 채우기는 Excel 기본 격자선이 시각적으로 사라져 보일 수 있다.
                    # 변경 셀에 얇은 경계선을 유지해 "하얀 블록"처럼 보이지 않게 보정한다.
                    self._ensure_visual_gridline(cell, rgb)
                    changed += 1

        return {
            "matched_cells": matched,
            "changed_cells": changed,
            "address": str(rng.address),
        }

    def fill_range(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        fill_color: str = "#FFFF00",
    ) -> dict[str, Any]:
        """지정 범위 전체의 배경색을 변경한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        rgb = self._hex_to_rgb(fill_color)
        rng.color = rgb

        start_row = int(getattr(rng, "row", 1))
        start_col = int(getattr(rng, "column", 1))
        rows_obj = getattr(rng, "rows", None)
        cols_obj = getattr(rng, "columns", None)
        row_count = int(getattr(rows_obj, "count", 1) or 1)
        col_count = int(getattr(cols_obj, "count", 1) or 1)
        for r in range(row_count):
            for c in range(col_count):
                cell_ref = f"{self._idx_to_col(start_col + c)}{start_row + r}"
                self._ensure_visual_gridline(sheet.range(cell_ref), rgb)

        return {
            "changed_cells": row_count * col_count,
            "address": str(rng.address),
        }

    def clear_range(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
    ) -> dict[str, Any]:
        """지정 범위의 값/수식을 비운다(서식은 유지)."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)

        api_range = getattr(rng, "api", None)
        if api_range is not None and hasattr(api_range, "ClearContents"):
            api_range.ClearContents()
        else:
            # API 객체를 직접 쓸 수 없는 환경에서는 값 대입으로 비운다.
            rng.value = None

        rows_obj = getattr(rng, "rows", None)
        cols_obj = getattr(rng, "columns", None)
        row_count = int(getattr(rows_obj, "count", 1) or 1)
        col_count = int(getattr(cols_obj, "count", 1) or 1)
        return {
            "cleared_cells": row_count * col_count,
            "address": str(rng.address),
        }

    def apply_border(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        line_style: str = "continuous",
        weight: str = "medium",
        color: str = "#000000",
    ) -> dict[str, Any]:
        """지정 범위에 경계선을 적용한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        api_range = getattr(rng, "api", None)
        if api_range is None:
            raise ExcelLiveError("경계선을 적용할 수 없습니다. Excel API 객체를 찾지 못했습니다.")

        # Excel COM 상수 (late binding)
        # -4142: xlLineStyleNone (경계선 제거)
        style_map = {"continuous": 1, "none": -4142}
        weight_map = {"thin": 2, "medium": -4138, "thick": 4}
        line_style_value = style_map.get((line_style or "").strip().lower(), 1)
        weight_value = weight_map.get((weight or "").strip().lower(), 2)
        border_color = self._rgb_to_excel_color(self._hex_to_rgb(color))
        edges = (7, 8, 9, 10, 11, 12)  # left, top, bottom, right, inside_v, inside_h

        for edge in edges:
            border = api_range.Borders(edge)
            border.LineStyle = line_style_value
            if line_style_value != -4142:
                border.Weight = weight_value
                border.Color = border_color

        rows_obj = getattr(rng, "rows", None)
        cols_obj = getattr(rng, "columns", None)
        row_count = int(getattr(rows_obj, "count", 1) or 1)
        col_count = int(getattr(cols_obj, "count", 1) or 1)
        return {
            "changed_cells": row_count * col_count,
            "address": str(rng.address),
        }

    def set_formula(
        self,
        workbook_id: str | None,
        sheet_name: str,
        range_ref: str,
        formula_a1: str,
    ) -> dict[str, Any]:
        """지정 범위에 동일한 A1 수식을 설정한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = sheet.range(range_ref)
        rng.formula = formula_a1

        values = self._normalize_values(rng.options(ndim=2).value)
        row_count = len(values)
        col_count = len(values[0]) if values else 0
        return {
            "formula_applied_cells": row_count * col_count,
            "address": str(rng.address),
        }

    def verify_formula_result(
        self,
        workbook_id: str | None,
        sheet_name: str,
        range_ref: str,
    ) -> dict[str, Any]:
        """
        수식 적용 후 결과 범위를 읽어 값 검증 요약을 반환한다.
        - 비어있지 않은 셀 수
        - 숫자 셀 수 / 합계 / 평균
        - 샘플 값(최대 10개)
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = sheet.range(range_ref)
        values = self._normalize_values(rng.options(ndim=2).value)
        non_empty = 0
        numeric_values: list[float] = []
        samples: list[Any] = []
        for row in values:
            for cell in row:
                if not self._is_empty(cell):
                    non_empty += 1
                    if len(samples) < 10:
                        samples.append(cell)
                num = self._as_float(cell)
                if num is not None:
                    numeric_values.append(num)
        total = sum(numeric_values) if numeric_values else 0.0
        avg = (total / len(numeric_values)) if numeric_values else 0.0
        return {
            "address": str(rng.address),
            "non_empty_cells": non_empty,
            "numeric_cells": len(numeric_values),
            "sum": total,
            "average": avg,
            "sample_values": samples,
        }

    def create_table(
        self,
        workbook_id: str | None,
        sheet_name: str,
        start_cell: str,
        rows: int,
        cols: int,
        with_border: bool = True,
    ) -> dict[str, Any]:
        """시작 셀 기준으로 지정 크기의 표 영역을 생성한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        row_count = max(1, min(100, int(rows)))
        col_count = max(1, min(50, int(cols)))

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = sheet.range(start_cell).resize(row_count, col_count)
        rng.value = [["" for _ in range(col_count)] for _ in range(row_count)]

        if with_border:
            api_range = getattr(rng, "api", None)
            if api_range is not None:
                # left, top, bottom, right, inside_v, inside_h
                for edge in (7, 8, 9, 10, 11, 12):
                    border = api_range.Borders(edge)
                    border.LineStyle = 1
                    border.Weight = 2
                    border.Color = self._rgb_to_excel_color((0, 0, 0))

        return {
            "created": True,
            "address": str(rng.address),
            "rows": row_count,
            "cols": col_count,
        }

    def sort_range(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        key_column: str | int = 1,
        order: str = "asc",
        has_header: bool = True,
    ) -> dict[str, Any]:
        """범위를 지정 열 기준으로 정렬한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)

        values = self._normalize_values(rng.options(ndim=2).value)
        if not values:
            return {"sorted_rows": 0, "address": str(rng.address)}
        col_count = max(len(row) for row in values)
        normalized = [row + [None] * (col_count - len(row)) for row in values]
        header_row = normalized[0] if has_header else None
        body_rows = normalized[1:] if has_header else normalized
        if not body_rows:
            return {"sorted_rows": 0, "address": str(rng.address)}

        start_col = int(getattr(rng, "column", 1) or 1)
        key_idx = self._resolve_column_selector(key_column, start_col, col_count, header_row)
        reverse = str(order or "asc").strip().lower() in {"desc", "descending", "내림차순"}
        sorted_rows = sorted(body_rows, key=lambda row: self._sortable_value(row[key_idx]), reverse=reverse)
        final_values = [header_row, *sorted_rows] if has_header and header_row is not None else sorted_rows
        rng.value = final_values
        return {
            "sorted_rows": len(sorted_rows),
            "address": str(rng.address),
            "key_column_index": key_idx + 1,
            "order": "desc" if reverse else "asc",
        }

    def filter_rows(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        column: str | int = 1,
        operator: str = "==",
        value: Any = None,
        has_header: bool = True,
    ) -> dict[str, Any]:
        """범위에 자동 필터를 적용한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        if not values:
            return {"filtered_rows": 0, "address": str(rng.address)}

        col_count = max(len(row) for row in values)
        normalized = [row + [None] * (col_count - len(row)) for row in values]
        start_col = int(getattr(rng, "column", 1) or 1)
        header_row = normalized[0] if has_header else None
        field_idx = self._resolve_column_selector(column, start_col, col_count, header_row)
        op = str(operator or "==").strip()
        criteria = self._build_filter_criteria(op, value)

        api_range = getattr(rng, "api", None)
        if api_range is None:
            raise ExcelLiveError("필터를 적용할 수 없습니다. Excel API 객체를 찾지 못했습니다.")
        try:
            api_range.AutoFilter(Field=field_idx + 1, Criteria1=criteria)
        except Exception as exc:  # pragma: no cover - COM 환경 의존
            raise ExcelLiveError(f"필터 적용 실패: {exc}") from exc

        body_rows = normalized[1:] if has_header else normalized
        matched = 0
        for row in body_rows:
            if self._matches_generic_condition(row[field_idx], op, value):
                matched += 1
        return {
            "filtered_rows": matched,
            "address": str(rng.address),
            "column_index": field_idx + 1,
            "operator": op,
            "value": value,
        }

    def read_computed_range(self, workbook_id: str | None, sheet_name: str, range_ref: str) -> dict[str, Any]:
        """계산된 값 읽기. xlwings는 Excel이 이미 계산한 값을 주므로 일반 읽기와 같다."""
        return self.read_range(workbook_id, sheet_name, range_ref)

    def find_duplicates(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        key_columns: list[str | int] | None = None,
        has_header: bool = True,
        output_sheet: str | None = None,
    ) -> dict[str, Any]:
        """중복을 제거하지 않고 어디에 몇 건 있는지만 보고한다."""
        payload = self.read_range(workbook_id, sheet_name, target_range)
        values = payload.get("values", []) if isinstance(payload, dict) else []
        address = str(payload.get("address", target_range)) if isinstance(payload, dict) else target_range
        if not values:
            return {"duplicate_groups": 0, "duplicate_rows": 0, "address": address, "samples": []}
        col_count = max(len(row) for row in values)
        header_row = values[0] if has_header else None
        body = values[1:] if has_header else values
        if key_columns:
            indexes = [
                self._resolve_column_selector(col, 1, col_count, header_row) for col in key_columns
            ]
        else:
            indexes = list(range(col_count))
        seen: dict[tuple, list[int]] = {}
        for offset, row in enumerate(body):
            key = tuple(str(row[i]) if i < len(row) else "" for i in indexes)
            seen.setdefault(key, []).append(offset + (2 if has_header else 1))
        samples = [
            {"value": " / ".join(key), "count": len(rows), "rows": rows[:10]}
            for key, rows in seen.items()
            if len(rows) > 1
        ]
        return {
            "duplicate_groups": len(samples),
            "duplicate_rows": sum(int(s["count"]) for s in samples),
            "address": address,
            "samples": samples[:20],
        }

    def recalculate(self, workbook_id: str | None, sheet_name: str | None = None) -> dict[str, Any]:
        """Excel에 전체 재계산과 연결 새로고침을 요청한다."""
        target_id = workbook_id or self._selected_workbook_id
        wb = self._find_workbook(target_id) if target_id else None
        if wb is None:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        app = getattr(wb, "app", None)
        if app is not None:
            app.calculate()
        api = getattr(wb, "api", None)
        if api is not None:
            try:
                api.RefreshAll()
            except Exception:  # pragma: no cover - COM 환경 의존
                pass
        return {"recalculated": True, "workbook_id": str(target_id), "sheets": [sheet_name or ""]}

    def export_pdf(
        self,
        workbook_id: str | None,
        sheet_name: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Excel 네이티브 기능으로 PDF를 내보낸다."""
        target_id = workbook_id or self._selected_workbook_id
        wb = self._find_workbook(target_id) if target_id else None
        if wb is None:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        source = self._find_sheet(wb, sheet_name) if sheet_name else wb
        target = str(output_path or "").strip() or None
        if target:
            # 플래너가 폴더 없는 이름을 지어내면 프로세스 작업 폴더에 떨어진다. 통합문서 옆에 둔다.
            requested = Path(target)
            if not requested.is_absolute():
                book_path = Path(str(getattr(wb, "fullname", "") or ""))
                if book_path.parent.exists():
                    target = str(book_path.parent / requested.name)
        try:
            if target:
                source.to_pdf(target)
            else:
                target = str(source.to_pdf())
        except Exception as exc:  # pragma: no cover - COM 환경 의존
            raise ExcelLiveError(f"PDF 내보내기에 실패했습니다: {exc}") from exc
        return {"exported": True, "pdf_path": target, "sheet_name": sheet_name or "(전체)"}

    def dedupe_rows(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        key_columns: list[str | int] | None = None,
        has_header: bool = True,
    ) -> dict[str, Any]:
        """중복 행을 제거한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        if not values:
            return {"removed_rows": 0, "remaining_rows": 0, "address": str(rng.address)}

        col_count = max(len(row) for row in values)
        start_col = int(getattr(rng, "column", 1) or 1)
        header_row = values[0] if has_header else None
        if key_columns:
            cols = [
                self._resolve_column_selector(col, start_col, col_count, header_row) + 1
                for col in key_columns
            ]
        else:
            cols = list(range(1, col_count + 1))

        before_rows = max(0, len(values) - 1 if has_header else len(values))
        api_range = getattr(rng, "api", None)
        if api_range is None:
            raise ExcelLiveError("중복 제거를 실행할 수 없습니다. Excel API 객체를 찾지 못했습니다.")
        try:
            # xlYes=1, xlNo=2
            api_range.RemoveDuplicates(Columns=cols, Header=1 if has_header else 2)
        except Exception as exc:  # pragma: no cover - COM 환경 의존
            raise ExcelLiveError(f"중복 제거 실패: {exc}") from exc

        after_values = self._normalize_values(rng.options(ndim=2).value)
        after_rows = max(0, len(after_values) - 1 if has_header else len(after_values))
        removed = max(0, before_rows - after_rows)
        return {
            "removed_rows": removed,
            "remaining_rows": after_rows,
            "address": str(rng.address),
            "key_columns": cols,
        }

    def pivot_table(
        self,
        workbook_id: str | None,
        sheet_name: str,
        source_range: str,
        row_field: str | int,
        value_field: str | int,
        agg: str = "sum",
        column_field: str | int | None = None,
        output_sheet: str | None = None,
        output_start: str = "A1",
        has_header: bool = True,
    ) -> dict[str, Any]:
        """
        범위를 집계해 피벗 형태 요약표를 생성한다.
        (Excel PivotTable 오브젝트 대신 결정론적 집계 결과 표를 작성)
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        source_ws = self._find_sheet(wb, sheet_name)
        source_rng = self._resolve_target_range(source_ws, source_range)
        values = self._normalize_values(source_rng.options(ndim=2).value)
        if not values:
            raise ExcelLiveError("피벗 대상 데이터가 비어 있습니다.")

        col_count = max(len(row) for row in values)
        normalized = [row + [None] * (col_count - len(row)) for row in values]
        headers = normalized[0] if has_header else [f"column_{i+1}" for i in range(col_count)]
        body_rows = normalized[1:] if has_header else normalized
        start_col = int(getattr(source_rng, "column", 1) or 1)

        row_idx = self._resolve_column_selector(row_field, start_col, col_count, headers)
        value_idx = self._resolve_column_selector(value_field, start_col, col_count, headers)
        col_idx = (
            self._resolve_column_selector(column_field, start_col, col_count, headers)
            if column_field is not None
            else None
        )
        agg_name = str(agg or "sum").strip().lower()
        if agg_name not in {"sum", "avg", "count"}:
            agg_name = "sum"

        output_rows = self._build_pivot_rows(
            rows=body_rows,
            row_idx=row_idx,
            value_idx=value_idx,
            agg=agg_name,
            col_idx=col_idx,
            row_header=str(headers[row_idx]),
            value_header=str(headers[value_idx]),
        )
        out_ws = self._find_or_create_sheet(wb, output_sheet or sheet_name)
        out_rng = out_ws.range(output_start).resize(len(output_rows), len(output_rows[0]))
        out_rng.value = output_rows
        return {
            "created": True,
            "address": str(out_rng.address),
            "rows": len(output_rows),
            "cols": len(output_rows[0]),
            "sheet_name": str(getattr(out_ws, "name", "") or ""),
        }

    def create_chart(
        self,
        workbook_id: str | None,
        sheet_name: str,
        source_range: str,
        chart_type: str = "line",
        title: str | None = None,
        output_sheet: str | None = None,
    ) -> dict[str, Any]:
        """지정 범위 기반 차트를 생성한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        src_ws = self._find_sheet(wb, sheet_name)
        src_rng = self._resolve_target_range(src_ws, source_range)
        out_ws = self._find_or_create_sheet(wb, output_sheet or sheet_name)
        try:
            chart = out_ws.charts.add(left=300, top=40, width=520, height=320)
            chart.set_source_data(src_rng)
            chart_name = f"chart_{len(out_ws.charts)}"
            chart.name = chart_name
            chart.api.ChartType = self._chart_type_to_excel(chart_type)
            chart.api.HasTitle = True
            chart.api.ChartTitle.Text = str(title or "데이터 차트")
        except Exception as exc:  # pragma: no cover - COM 환경 의존
            raise ExcelLiveError(f"차트 생성 실패: {exc}") from exc
        return {
            "created": True,
            "chart_name": chart_name,
            "chart_type": str(chart_type or "line"),
            "source_address": str(src_rng.address),
            "sheet_name": str(getattr(out_ws, "name", "") or ""),
        }

    def validate_data(
        self,
        workbook_id: str | None,
        sheet_name: str,
        target_range: str,
        checks: list[str] | None = None,
        has_header: bool = True,
        date_min: str | None = None,
        date_max: str | None = None,
    ) -> dict[str, Any]:
        """범위 데이터 품질을 점검하고 이슈 요약을 반환한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        ws = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(ws, target_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        if not values:
            return {"address": str(rng.address), "issues": [], "total_issues": 0}

        col_count = max(len(row) for row in values)
        normalized = [row + [None] * (col_count - len(row)) for row in values]
        rows = normalized[1:] if has_header else normalized
        check_set = {str(c).strip().lower() for c in (checks or ["empty", "negative", "outlier"])}
        start_row = int(getattr(rng, "row", 1) or 1) + (1 if has_header else 0)
        start_col = int(getattr(rng, "column", 1) or 1)
        issues: list[dict[str, Any]] = []

        if "empty" in check_set:
            empty_cells: list[str] = []
            for r_idx, row in enumerate(rows):
                for c_idx, v in enumerate(row):
                    if self._is_empty(v):
                        empty_cells.append(f"{self._idx_to_col(start_col + c_idx)}{start_row + r_idx}")
            issues.append({"type": "empty", "count": len(empty_cells), "samples": empty_cells[:20]})

        if "negative" in check_set:
            negative_cells: list[str] = []
            for r_idx, row in enumerate(rows):
                for c_idx, v in enumerate(row):
                    num = self._as_float(v)
                    if num is not None and num < 0:
                        negative_cells.append(f"{self._idx_to_col(start_col + c_idx)}{start_row + r_idx}")
            issues.append({"type": "negative", "count": len(negative_cells), "samples": negative_cells[:20]})

        if "outlier" in check_set:
            outlier_cells: list[str] = []
            for c_idx in range(col_count):
                numeric_values: list[tuple[int, float]] = []
                for r_idx, row in enumerate(rows):
                    num = self._as_float(row[c_idx] if c_idx < len(row) else None)
                    if num is not None:
                        numeric_values.append((r_idx, num))
                if len(numeric_values) < 4:
                    continue
                mean = sum(v for _, v in numeric_values) / len(numeric_values)
                variance = sum((v - mean) ** 2 for _, v in numeric_values) / len(numeric_values)
                std = math.sqrt(variance)
                if std <= 0:
                    continue
                for r_idx, val in numeric_values:
                    z = abs((val - mean) / std)
                    if z >= 3.0:
                        outlier_cells.append(f"{self._idx_to_col(start_col + c_idx)}{start_row + r_idx}")
            issues.append({"type": "outlier", "count": len(outlier_cells), "samples": outlier_cells[:20]})

        if "date_range" in check_set and (date_min or date_max):
            min_dt = self._parse_datetime_value(date_min) if date_min else None
            max_dt = self._parse_datetime_value(date_max) if date_max else None
            invalid_cells: list[str] = []
            for r_idx, row in enumerate(rows):
                for c_idx, v in enumerate(row):
                    dt = self._parse_datetime_value(v)
                    if dt is None:
                        continue
                    if min_dt and dt < min_dt:
                        invalid_cells.append(f"{self._idx_to_col(start_col + c_idx)}{start_row + r_idx}")
                        continue
                    if max_dt and dt > max_dt:
                        invalid_cells.append(f"{self._idx_to_col(start_col + c_idx)}{start_row + r_idx}")
            issues.append({"type": "date_range", "count": len(invalid_cells), "samples": invalid_cells[:20]})

        total = sum(int(it.get("count", 0) or 0) for it in issues)
        return {"address": str(rng.address), "issues": issues, "total_issues": total}

    def protect_sheet(
        self,
        workbook_id: str | None,
        sheet_name: str,
        *,
        password: str | None = None,
        lock_formula_cells: bool = True,
        unlock_range: str | None = None,
    ) -> dict[str, Any]:
        """시트 보호/잠금 설정을 적용한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        ws = self._find_sheet(wb, sheet_name)
        ws_api = getattr(ws, "api", None)
        if ws_api is None:
            raise ExcelLiveError("시트 보호를 적용할 수 없습니다. Excel API 객체를 찾지 못했습니다.")
        pwd = str(password or "")
        try:
            ws_api.Unprotect(Password=pwd)
        except Exception:
            pass

        used = getattr(ws, "used_range", None) or ws.range("A1")
        try:
            used.api.Locked = False
        except Exception:
            pass

        if lock_formula_cells:
            try:
                # xlCellTypeFormulas = -4123
                formula_cells = used.api.SpecialCells(-4123)
                formula_cells.Locked = True
            except Exception:
                # 수식 셀이 없으면 무시
                pass

        unlocked_address = ""
        if unlock_range:
            try:
                unlock_rng = self._resolve_target_range(ws, unlock_range)
                unlock_rng.api.Locked = False
                unlocked_address = str(unlock_rng.address)
            except Exception as exc:
                raise ExcelLiveError(f"잠금 해제 범위 적용 실패: {exc}") from exc

        try:
            ws_api.Protect(Password=pwd, UserInterfaceOnly=True)
        except Exception as exc:
            raise ExcelLiveError(f"시트 보호 적용 실패: {exc}") from exc
        return {
            "protected": True,
            "sheet_name": str(getattr(ws, "name", "") or ""),
            "lock_formula_cells": bool(lock_formula_cells),
            "unlock_range": unlocked_address,
        }

    def set_data_validation(
        self,
        workbook_id: str | None,
        sheet_name: str,
        *,
        target_range: str,
        validation_type: str = "list",
        source: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        allow_blank: bool = True,
        show_error: bool = True,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """입력 유효성(드롭다운/숫자/날짜 제한)을 설정한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        ws = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(ws, target_range)
        api_rng = getattr(rng, "api", None)
        if api_rng is None:
            raise ExcelLiveError("유효성 검사를 적용할 수 없습니다. Excel API 객체를 찾지 못했습니다.")

        vtype = str(validation_type or "list").strip().lower()
        type_map = {
            "list": 3,  # xlValidateList
            "whole": 1,  # xlValidateWholeNumber
            "decimal": 2,  # xlValidateDecimal
            "date": 4,  # xlValidateDate
        }
        if vtype not in type_map:
            raise ExcelLiveError(f"지원하지 않는 validation_type: {validation_type}")
        try:
            api_rng.Validation.Delete()
        except Exception:
            pass

        try:
            if vtype == "list":
                formula1 = str(source or "").strip()
                if not formula1:
                    raise ExcelLiveError("list 유효성은 source(예: 완료,진행중,지연)가 필요합니다.")
                api_rng.Validation.Add(
                    Type=type_map[vtype],
                    AlertStyle=1,  # xlValidAlertStop
                    Operator=1,  # xlBetween
                    Formula1=formula1,
                )
            elif vtype in {"whole", "decimal"}:
                if minimum is None or maximum is None:
                    raise ExcelLiveError("숫자 유효성은 minimum/maximum이 필요합니다.")
                api_rng.Validation.Add(
                    Type=type_map[vtype],
                    AlertStyle=1,
                    Operator=1,
                    Formula1=str(minimum),
                    Formula2=str(maximum),
                )
            else:  # date
                if minimum is None or maximum is None:
                    raise ExcelLiveError("날짜 유효성은 minimum/maximum(Excel serial 또는 YYYYMMDD 숫자)이 필요합니다.")
                api_rng.Validation.Add(
                    Type=type_map[vtype],
                    AlertStyle=1,
                    Operator=1,
                    Formula1=str(minimum),
                    Formula2=str(maximum),
                )
            api_rng.Validation.IgnoreBlank = bool(allow_blank)
            api_rng.Validation.ShowError = bool(show_error)
            if error_message:
                api_rng.Validation.ErrorMessage = str(error_message)
        except Exception as exc:
            raise ExcelLiveError(f"유효성 검사 설정 실패: {exc}") from exc
        return {
            "applied": True,
            "address": str(rng.address),
            "validation_type": vtype,
        }

    def consolidate_sheets(
        self,
        workbook_id: str | None,
        *,
        source_sheets: list[str],
        output_sheet: str = "통합결과",
        include_header_once: bool = True,
        add_source_sheet_col: bool = True,
    ) -> dict[str, Any]:
        """같은 통합문서 내 여러 시트를 하나로 합친다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        if not source_sheets:
            raise ExcelLiveError("source_sheets가 비어 있습니다.")
        wb = self._find_workbook(target_id)
        out_ws = self._find_or_create_sheet(wb, output_sheet)
        merged: list[list[Any]] = []
        header_written = False
        body_rows = 0
        for sname in source_sheets:
            ws = self._find_sheet(wb, sname)
            values = self._normalize_values(getattr(ws, "used_range").options(ndim=2).value)
            if not values:
                continue
            header = values[0]
            body = values[1:] if len(values) > 1 else []
            if include_header_once and not header_written:
                out_header = (["source_sheet"] if add_source_sheet_col else []) + list(header)
                merged.append(out_header)
                header_written = True
            elif not include_header_once:
                out_header = (["source_sheet"] if add_source_sheet_col else []) + list(header)
                merged.append(out_header)
            for row in body:
                merged.append(( [sname] if add_source_sheet_col else [] ) + list(row))
                body_rows += 1
        if not merged:
            raise ExcelLiveError("통합할 데이터가 없습니다.")
        max_cols = max(len(r) for r in merged)
        normalized = [r + [None] * (max_cols - len(r)) for r in merged]
        out_rng = out_ws.range("A1").resize(len(normalized), max_cols)
        out_rng.value = normalized
        return {
            "created": True,
            "sheet_name": str(getattr(out_ws, "name", "") or ""),
            "address": str(out_rng.address),
            "rows": len(normalized),
            "cols": max_cols,
            "merged_rows": body_rows,
        }

    def consolidate_workbooks_from_folder(
        self,
        workbook_id: str | None,
        *,
        folder_path: str,
        pattern: str = "*.xlsx",
        source_sheet: str | None = None,
        output_sheet: str = "파일통합결과",
        include_header_once: bool = True,
        add_source_file_col: bool = True,
    ) -> dict[str, Any]:
        """폴더 내 여러 파일의 시트를 현재 통합문서로 합친다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        root = Path(folder_path).expanduser()
        if not root.exists() or not root.is_dir():
            raise ExcelLiveError(f"유효하지 않은 폴더 경로: {folder_path}")
        files = sorted(root.glob(pattern))
        if not files:
            raise ExcelLiveError("통합할 파일이 없습니다.")

        xw = self._xw_module()
        wb_out = self._find_workbook(target_id)
        out_ws = self._find_or_create_sheet(wb_out, output_sheet)
        merged: list[list[Any]] = []
        header_written = False
        merged_rows = 0
        opened_count = 0
        for fp in files:
            wb_in = None
            try:
                wb_in = xw.Book(str(fp))
                opened_count += 1
                if source_sheet:
                    ws_in = self._find_sheet(wb_in, source_sheet)
                else:
                    ws_in = wb_in.sheets[0]
                values = self._normalize_values(getattr(ws_in, "used_range").options(ndim=2).value)
                if not values:
                    continue
                header = values[0]
                body = values[1:] if len(values) > 1 else []
                if include_header_once and not header_written:
                    out_header = (["source_file"] if add_source_file_col else []) + list(header)
                    merged.append(out_header)
                    header_written = True
                elif not include_header_once:
                    out_header = (["source_file"] if add_source_file_col else []) + list(header)
                    merged.append(out_header)
                for row in body:
                    merged.append(([fp.name] if add_source_file_col else []) + list(row))
                    merged_rows += 1
            finally:
                try:
                    if wb_in is not None:
                        wb_in.close()
                except Exception:
                    pass
        if not merged:
            raise ExcelLiveError("파일에서 읽은 데이터가 없습니다.")
        max_cols = max(len(r) for r in merged)
        normalized = [r + [None] * (max_cols - len(r)) for r in merged]
        out_rng = out_ws.range("A1").resize(len(normalized), max_cols)
        out_rng.value = normalized
        return {
            "created": True,
            "sheet_name": str(getattr(out_ws, "name", "") or ""),
            "address": str(out_rng.address),
            "rows": len(normalized),
            "cols": max_cols,
            "opened_files": opened_count,
            "merged_rows": merged_rows,
        }

    def refresh_power_query(self, workbook_id: str | None) -> dict[str, Any]:
        """통합문서의 연결/쿼리를 새로고침한다(RefreshAll)."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        api_wb = getattr(wb, "api", None)
        if api_wb is None:
            raise ExcelLiveError("Power Query 새로고침을 실행할 수 없습니다.")
        try:
            api_wb.RefreshAll()
        except Exception as exc:
            raise ExcelLiveError(f"Power Query 새로고침 실패: {exc}") from exc
        return {"refreshed": True, "workbook_id": self._workbook_id(wb)}

    def run_vba_macro(
        self,
        workbook_id: str | None,
        *,
        macro_name: str,
        args: list[Any] | None = None,
    ) -> dict[str, Any]:
        """VBA 매크로를 실행한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        self._find_workbook(target_id)
        app = self._app()
        macro = str(macro_name or "").strip()
        if not macro:
            raise ExcelLiveError("macro_name이 필요합니다.")
        macro_args = list(args or [])
        try:
            app.api.Run(macro, *macro_args)
        except Exception as exc:
            raise ExcelLiveError(f"VBA 매크로 실행 실패: {exc}") from exc
        return {"executed": True, "macro_name": macro, "args_count": len(macro_args)}

    def compare_ranges(
        self,
        workbook_id: str | None,
        *,
        left_sheet: str,
        left_range: str,
        right_sheet: str,
        right_range: str,
        output_sheet: str | None = None,
    ) -> dict[str, Any]:
        """두 범위를 셀 단위 비교해 차이를 반환/기록한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        ws_l = self._find_sheet(wb, left_sheet)
        ws_r = self._find_sheet(wb, right_sheet)
        rng_l = self._resolve_target_range(ws_l, left_range)
        rng_r = self._resolve_target_range(ws_r, right_range)
        vals_l = self._normalize_values(rng_l.options(ndim=2).value)
        vals_r = self._normalize_values(rng_r.options(ndim=2).value)
        rows = max(len(vals_l), len(vals_r))
        cols = max(max((len(r) for r in vals_l), default=0), max((len(r) for r in vals_r), default=0))
        diffs: list[list[Any]] = [["row", "col", "left_value", "right_value"]]
        diff_count = 0
        for r in range(rows):
            for c in range(cols):
                lv = vals_l[r][c] if r < len(vals_l) and c < len(vals_l[r]) else None
                rv = vals_r[r][c] if r < len(vals_r) and c < len(vals_r[r]) else None
                if str(lv) != str(rv):
                    diff_count += 1
                    diffs.append([r + 1, c + 1, lv, rv])
        result: dict[str, Any] = {
            "left_address": str(rng_l.address),
            "right_address": str(rng_r.address),
            "diff_cells": diff_count,
            "sample_diffs": diffs[1:21],
        }
        if output_sheet:
            out_ws = self._find_or_create_sheet(wb, output_sheet)
            out_rng = out_ws.range("A1").resize(len(diffs), len(diffs[0]))
            out_rng.value = diffs
            result["output_sheet"] = str(getattr(out_ws, "name", "") or "")
            result["output_address"] = str(out_rng.address)
        return result

    def forecast_linear(
        self,
        workbook_id: str | None,
        *,
        sheet_name: str,
        source_range: str,
        horizon: int = 3,
        output_sheet: str | None = None,
        output_start: str = "A1",
    ) -> dict[str, Any]:
        """단순 선형회귀 기반 예측값을 생성한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        ws = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(ws, source_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        series: list[float] = []
        for row in values:
            if not row:
                continue
            num = self._as_float(row[-1])
            if num is not None:
                series.append(num)
        if len(series) < 2:
            raise ExcelLiveError("예측을 위해 최소 2개 이상의 숫자 데이터가 필요합니다.")
        n = len(series)
        xs = list(range(1, n + 1))
        sum_x = sum(xs)
        sum_y = sum(series)
        sum_xx = sum(x * x for x in xs)
        sum_xy = sum(x * y for x, y in zip(xs, series))
        denom = (n * sum_xx) - (sum_x * sum_x)
        slope = ((n * sum_xy) - (sum_x * sum_y)) / denom if denom else 0.0
        intercept = (sum_y - slope * sum_x) / n
        horizon_n = max(1, min(36, int(horizon)))
        out_rows: list[list[Any]] = [["step", "forecast"]]
        for step in range(1, horizon_n + 1):
            x = n + step
            out_rows.append([x, slope * x + intercept])
        out_ws = self._find_or_create_sheet(wb, output_sheet or sheet_name)
        out_rng = out_ws.range(output_start).resize(len(out_rows), 2)
        out_rng.value = out_rows
        return {
            "created": True,
            "sheet_name": str(getattr(out_ws, "name", "") or ""),
            "address": str(out_rng.address),
            "horizon": horizon_n,
            "slope": slope,
            "intercept": intercept,
        }

    def save_workbook(self, workbook_id: str | None) -> dict[str, Any]:
        """현재 통합문서를 디스크에 저장한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError(
                "저장할 통합문서를 찾지 못했습니다. workbook_id를 지정하거나 select_workbook을 먼저 호출해 주세요."
            )

        wb = self._find_workbook(target_id)
        full_path = str(getattr(wb, "fullname", "") or "").strip()
        if not full_path:
            raise ExcelLiveError(
                "아직 파일 경로가 없는 통합문서입니다. Excel에서 먼저 다른 이름으로 저장해 주세요."
            )
        try:
            wb.save()
        except Exception as exc:  # pragma: no cover - COM 환경 의존
            raise ExcelLiveError(f"통합문서 저장에 실패했습니다: {exc}") from exc

        return {
            "saved": True,
            "workbook_id": self._workbook_id(wb),
            "name": str(getattr(wb, "name", "") or ""),
            "full_path": full_path,
        }

    def create_workbook_backup(
        self,
        workbook_id: str | None,
        *,
        label: str = "auto",
    ) -> dict[str, Any]:
        """
        현재 통합문서의 복구용 백업 사본을 생성한다.

        - 기본 저장 위치: 원본 파일 폴더의 `officeclaw_backups/`
        - 저장 방식: Excel COM SaveCopyAs 우선, 실패 시 파일 복사 fallback
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("백업할 통합문서를 찾지 못했습니다. workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        full_path = str(getattr(wb, "fullname", "") or "").strip()
        if not full_path:
            raise ExcelLiveError(
                "아직 파일 경로가 없는 통합문서입니다. 자동 복구 백업을 위해 먼저 다른 이름으로 저장해 주세요."
            )
        src = Path(full_path)
        if not src.exists() or not src.is_file():
            raise ExcelLiveError(f"원본 파일 경로가 유효하지 않습니다: {full_path}")

        backup_dir = src.parent / "officeclaw_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label or "auto")).strip("_") or "auto"
        backup_name = f"{src.stem}.{safe_label}.{stamp}{src.suffix}"
        backup_path = backup_dir / backup_name
        suffix = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{src.stem}.{safe_label}.{stamp}_{suffix}{src.suffix}"
            suffix += 1

        try:
            api_wb = getattr(wb, "api", None)
            if api_wb is not None and hasattr(api_wb, "SaveCopyAs"):
                api_wb.SaveCopyAs(str(backup_path))
            else:
                shutil.copy2(src, backup_path)
        except Exception as exc:
            raise ExcelLiveError(f"자동 복구 백업 생성 실패: {exc}") from exc

        return {
            "backup_created": True,
            "backup_path": str(backup_path),
            "source_path": str(src),
            "workbook_id": self._workbook_id(wb),
        }

    def list_workbook_backups(
        self,
        workbook_id: str | None,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """대상 통합문서의 복구 백업 목록을 최신순으로 반환한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("백업 목록을 조회할 통합문서를 찾지 못했습니다. workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        full_path = str(getattr(wb, "fullname", "") or "").strip()
        if not full_path:
            raise ExcelLiveError("아직 파일 경로가 없는 통합문서입니다. 먼저 다른 이름으로 저장해 주세요.")

        src = Path(full_path).resolve()
        backup_dir = src.parent / "officeclaw_backups"
        max_items = max(1, min(200, int(limit)))
        rows: list[dict[str, Any]] = []

        if backup_dir.exists() and backup_dir.is_dir():
            pattern = f"{src.stem}.*{src.suffix}"
            for fp in backup_dir.glob(pattern):
                if not fp.is_file():
                    continue
                try:
                    stat = fp.stat()
                    rows.append(
                        {
                            "backup_path": str(fp.resolve()),
                            "backup_name": fp.name,
                            "size_bytes": int(stat.st_size),
                            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "modified_ts": float(stat.st_mtime),
                        }
                    )
                except Exception:
                    continue

        rows.sort(key=lambda item: float(item.get("modified_ts", 0.0)), reverse=True)
        for row in rows:
            row.pop("modified_ts", None)

        return {
            "workbook_id": self._workbook_id(wb),
            "source_path": str(src),
            "backup_dir": str(backup_dir),
            "backups": rows[:max_items],
        }

    def restore_workbook_from_backup(
        self,
        workbook_id: str | None,
        *,
        backup_path: str | None = None,
    ) -> dict[str, Any]:
        """
        백업 파일로 통합문서를 복구한다.

        동작:
        1) 현재 파일을 pre_restore 백업으로 1회 보존
        2) 현재 통합문서를 저장하지 않고 닫음
        3) 선택한 백업 파일을 원본 파일 경로로 덮어씀
        4) 원본 파일을 다시 열어 작업 계속
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("복구할 통합문서를 찾지 못했습니다. workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        source_path = str(getattr(wb, "fullname", "") or "").strip()
        if not source_path:
            raise ExcelLiveError("파일 경로가 없는 통합문서는 복구할 수 없습니다.")
        src = Path(source_path).resolve()
        if not src.exists() or not src.is_file():
            raise ExcelLiveError(f"원본 통합문서 경로가 유효하지 않습니다: {source_path}")

        if backup_path:
            chosen_backup = Path(str(backup_path)).expanduser().resolve()
        else:
            listed = self.list_workbook_backups(target_id, limit=1)
            backups = listed.get("backups", [])
            if not backups:
                raise ExcelLiveError("복구 가능한 백업이 없습니다.")
            chosen_backup = Path(str(backups[0].get("backup_path", ""))).resolve()

        if not chosen_backup.exists() or not chosen_backup.is_file():
            raise ExcelLiveError(f"백업 파일을 찾을 수 없습니다: {chosen_backup}")

        pre_restore_info = self.create_workbook_backup(target_id, label="pre_restore")

        try:
            api_wb = getattr(wb, "api", None)
            if api_wb is not None and hasattr(api_wb, "Close"):
                api_wb.Close(SaveChanges=False)
            else:
                wb.close()
        except Exception as exc:
            raise ExcelLiveError(f"복구 전 통합문서 닫기 실패: {exc}") from exc

        try:
            shutil.copy2(chosen_backup, src)
        except Exception as exc:
            raise ExcelLiveError(f"백업 파일 복구 실패: {exc}") from exc

        try:
            reopened = self._xw_module().Book(str(src))
            self._selected_workbook_id = self._workbook_id(reopened)
        except Exception as exc:
            raise ExcelLiveError(f"복구 후 통합문서 다시 열기 실패: {exc}") from exc

        return {
            "restored": True,
            "workbook_id": self._workbook_id(reopened),
            "name": str(getattr(reopened, "name", "") or ""),
            "full_path": str(src),
            "restored_from_backup_path": str(chosen_backup),
            "pre_restore_backup_path": str(pre_restore_info.get("backup_path", "")),
            "active_sheet": self._active_sheet_name(reopened),
        }

    def get_active_selection_ref(
        self,
        workbook_id: str | None,
        sheet_name: str | None,
    ) -> str:
        """
        현재 Excel 선택 범위를 A1 표기 문자열로 반환한다.

        - 사용자가 범위를 말하지 않은 자연어 명령의 기본 타깃으로 사용.
        - 선택 정보를 얻지 못하면 A1로 폴백.
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            return "A1"

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name) if sheet_name else wb.sheets.active
        try:
            app = self._app()
            selection = getattr(app, "selection", None)
            if selection is None:
                return "A1"
            address = str(getattr(selection, "address", "") or "")
            cleaned = self._normalize_address_ref(address)
            if cleaned:
                return cleaned
        except Exception:
            pass

        try:
            active = sheet.range("A1")
            return self._normalize_address_ref(str(getattr(active, "address", "") or "")) or "A1"
        except Exception:
            return "A1"

    def get_used_range_ref(
        self,
        workbook_id: str | None,
        sheet_name: str | None,
    ) -> str:
        """
        현재 시트의 사용 영역(used_range)을 A1 표기 문자열로 반환한다.

        - "전체 지우기/초기화"처럼 범위를 명시하지 않은 명령의 기본 타깃으로 사용.
        - used_range를 얻지 못하면 A1로 폴백.
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            return "A1"

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name) if sheet_name else wb.sheets.active
        try:
            used = getattr(sheet, "used_range", None)
            if used is None:
                return "A1"
            address = str(getattr(used, "address", "") or "")
            cleaned = self._normalize_address_ref(address)
            if cleaned:
                return cleaned
        except Exception:
            pass
        return "A1"

    def _find_workbook(self, workbook_id_or_name: str):
        candidate = (workbook_id_or_name or "").strip().lower()
        for wb in self._app().books:
            wb_id = self._workbook_id(wb).lower()
            wb_name = str(getattr(wb, "name", "")).strip().lower()
            if candidate in {wb_id, wb_name}:
                return wb
        raise WorkbookNotFoundError(f"통합문서를 찾을 수 없습니다: {workbook_id_or_name}")

    @staticmethod
    def _workbook_id(workbook: Any) -> str:
        fullname = str(getattr(workbook, "fullname", "") or "").strip()
        name = str(getattr(workbook, "name", "") or "").strip()
        return fullname or name

    @staticmethod
    def _active_sheet_name(workbook: Any) -> str:
        try:
            sheet = getattr(workbook, "sheets").active
            return str(getattr(sheet, "name", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _find_sheet(workbook: Any, sheet_name: str):
        target = (sheet_name or "").strip().lower()
        for sheet in workbook.sheets:
            if str(getattr(sheet, "name", "")).strip().lower() == target:
                return sheet
        raise WorksheetNotFoundError(f"시트를 찾을 수 없습니다: {sheet_name}")

    @staticmethod
    def _normalize_values(raw: Any) -> list[list[Any]]:
        # xlwings range.value 특성:
        # - 단일 셀: scalar
        # - 단일 행/열: 1차원 list
        # - 다중 범위: 2차원 list
        if raw is None:
            return []
        if isinstance(raw, list):
            if not raw:
                return []
            if isinstance(raw[0], list):
                return raw
            return [raw]
        return [[raw]]

    @staticmethod
    def _normalize_address_ref(address: str) -> str:
        """
        Excel 주소 문자열을 A1/A1:C3 형태로 정규화한다.
        예: "$C$3" -> "C3", "'Sheet1'!$B$2:$D$3" -> "B2:D3"
        """
        text = str(address or "").strip()
        if not text:
            return ""
        text = text.lstrip("=")
        if "!" in text:
            text = text.split("!")[-1]
        text = text.replace("$", "").strip()
        # 다중 선택(콤마 구분)인 경우 첫 영역만 사용
        if "," in text:
            text = text.split(",")[0].strip()
        return text or ""

    @staticmethod
    def _sanitize_sheet_name(sheet_name: str) -> str:
        text = str(sheet_name or "").strip()
        if not text:
            raise ExcelLiveError("sheet_name이 비어 있습니다.")
        # Excel 시트명 금지 문자: : \ / ? * [ ]
        text = re.sub(r"[:\\/?*\[\]]", "_", text)
        text = text[:31].strip()
        if not text:
            raise ExcelLiveError("유효한 sheet_name이 필요합니다.")
        return text

    @staticmethod
    def _matches_condition(value: Any, operator: str, threshold: float) -> bool:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False

        op = operator.strip()
        if op == ">":
            return numeric > threshold
        if op == ">=":
            return numeric >= threshold
        if op == "<":
            return numeric < threshold
        if op == "<=":
            return numeric <= threshold
        if op == "==":
            return numeric == threshold
        if op == "!=":
            return numeric != threshold
        raise ValueError(f"지원하지 않는 연산자: {operator}")

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        value = color.strip().lstrip("#")
        if len(value) != 6:
            raise ValueError(f"색상 형식이 올바르지 않습니다: {color}")
        return (
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )

    @staticmethod
    def _ensure_visual_gridline(cell: Any, rgb: tuple[int, int, int]) -> None:
        """셀 경계선이 비어 있으면 얇은 보더를 적용해 시인성을 높인다."""
        try:
            api_range = getattr(cell, "api", None)
            if api_range is None:
                return
            # Excel COM 상수 (late binding 용 숫자 상수 사용)
            xl_edge_left = 7
            xl_edge_top = 8
            xl_edge_bottom = 9
            xl_edge_right = 10
            xl_continuous = 1
            xl_thin = 2
            xl_line_style_none = -4142
            border_color = ExcelLiveService._rgb_to_excel_color((217, 217, 217))

            for edge in (xl_edge_left, xl_edge_top, xl_edge_bottom, xl_edge_right):
                border = api_range.Borders(edge)
                # 기존 보더가 이미 있으면 유지한다.
                line_style = getattr(border, "LineStyle", xl_line_style_none)
                if line_style not in (None, 0, xl_line_style_none):
                    continue
                border.LineStyle = xl_continuous
                border.Weight = xl_thin
                border.Color = border_color
        except Exception:
            # COM/테마 환경에 따라 보더 설정 실패 가능 — 비치명적
            return

    @staticmethod
    def _rgb_to_excel_color(rgb: tuple[int, int, int]) -> int:
        """(R,G,B) 튜플을 Excel COM Color 정수로 변환한다."""
        r, g, b = rgb
        return int(r) + (int(g) << 8) + (int(b) << 16)

    @classmethod
    def _col_to_idx(cls, col: str) -> int:
        n = 0
        for ch in str(col or "").strip().upper():
            if not ("A" <= ch <= "Z"):
                raise ValueError(f"유효하지 않은 열 식별자: {col}")
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n

    @classmethod
    def _resolve_column_selector(
        cls,
        selector: str | int,
        start_col_idx: int,
        col_count: int,
        header_row: list[Any] | None,
    ) -> int:
        if isinstance(selector, int):
            raw = selector
        else:
            text = str(selector or "").strip()
            headers = [str(h or "").strip() for h in (header_row or [])]
            if not text:
                raw = 1
            elif re.fullmatch(r"\d+", text):
                raw = int(text)
            else:
                raw = 0
                lowered = text.lower()
                for idx, header in enumerate(headers, start=1):
                    if header.lower() == lowered:
                        raw = idx
                        break
                if raw == 0 and headers:
                    # "매출"→Sales 처럼 한국어 개념어로 부른 열을 잇는다.
                    mapped = resolve_header(text, [h for h in headers if h])
                    if mapped:
                        raw = headers.index(mapped) + 1
                if raw == 0 and re.fullmatch(r"[A-Z]{1,3}", text.upper()):
                    # 열 문자 해석은 마지막이다. 'Sales' 같은 영문 머리글을 열 문자로 읽으면
                    # 범위 끝 열로 잘려 엉뚱한 열이 집계된다.
                    abs_idx = cls._col_to_idx(text.upper())
                    raw = abs_idx - start_col_idx + 1
                if raw == 0:
                    raw = 1
        raw = max(1, min(col_count, int(raw)))
        return raw - 1  # zero-based

    @staticmethod
    def _sortable_value(value: Any) -> tuple[int, Any]:
        if value is None:
            return (3, "")
        if isinstance(value, (int, float)):
            return (0, float(value))
        text = str(value).strip()
        if text == "":
            return (3, "")
        try:
            return (1, float(text))
        except Exception:
            return (2, text.lower())

    @staticmethod
    def _build_filter_criteria(operator: str, value: Any) -> str:
        op = str(operator or "==").strip()
        if op in {"=", "=="}:
            return str(value)
        if op == "!=":
            return f"<>{value}"
        if op in {">", ">=", "<", "<="}:
            return f"{op}{value}"
        return str(value)

    @staticmethod
    def _matches_generic_condition(cell_value: Any, operator: str, target_value: Any) -> bool:
        op = str(operator or "==").strip()
        if op in {"=", "=="}:
            return str(cell_value) == str(target_value)
        if op == "!=":
            return str(cell_value) != str(target_value)
        left_num = ExcelLiveService._as_float(cell_value)
        right_num = ExcelLiveService._as_float(target_value)
        if left_num is None or right_num is None:
            return False
        if op == ">":
            return left_num > right_num
        if op == ">=":
            return left_num >= right_num
        if op == "<":
            return left_num < right_num
        if op == "<=":
            return left_num <= right_num
        return False

    @staticmethod
    def _build_pivot_rows(
        *,
        rows: list[list[Any]],
        row_idx: int,
        value_idx: int,
        agg: str,
        col_idx: int | None,
        row_header: str,
        value_header: str,
    ) -> list[list[Any]]:
        if col_idx is None:
            groups: dict[str, list[float]] = {}
            for row in rows:
                key = str(row[row_idx] if row_idx < len(row) else "")
                val = ExcelLiveService._as_float(row[value_idx] if value_idx < len(row) else None)
                if key not in groups:
                    groups[key] = []
                if val is not None:
                    groups[key].append(val)
            out: list[list[Any]] = [[row_header, f"{agg}_{value_header}"]]
            for key in sorted(groups.keys()):
                vals = groups[key]
                if agg == "count":
                    agg_val: Any = len(vals)
                elif agg == "avg":
                    agg_val = (sum(vals) / len(vals)) if vals else 0
                else:
                    agg_val = sum(vals)
                out.append([key, agg_val])
            return out

        row_keys: set[str] = set()
        col_keys: set[str] = set()
        groups2d: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            r_key = str(row[row_idx] if row_idx < len(row) else "")
            c_key = str(row[col_idx] if col_idx < len(row) else "")
            val = ExcelLiveService._as_float(row[value_idx] if value_idx < len(row) else None)
            row_keys.add(r_key)
            col_keys.add(c_key)
            group_key = (r_key, c_key)
            if group_key not in groups2d:
                groups2d[group_key] = []
            if val is not None:
                groups2d[group_key].append(val)
        sorted_rows = sorted(row_keys)
        sorted_cols = sorted(col_keys)
        out = [[row_header, *sorted_cols]]
        for r_key in sorted_rows:
            row_out: list[Any] = [r_key]
            for c_key in sorted_cols:
                vals = groups2d.get((r_key, c_key), [])
                if agg == "count":
                    row_out.append(len(vals))
                elif agg == "avg":
                    row_out.append((sum(vals) / len(vals)) if vals else 0)
                else:
                    row_out.append(sum(vals))
            out.append(row_out)
        return out

    @staticmethod
    def _find_or_create_sheet(workbook: Any, sheet_name: str):
        target = str(sheet_name or "").strip()
        for sheet in workbook.sheets:
            if str(getattr(sheet, "name", "") or "").strip().lower() == target.lower():
                return sheet
        return workbook.sheets.add(name=target)

    @staticmethod
    def _chart_type_to_excel(chart_type: str) -> int:
        """
        Excel COM ChartType 상수 매핑.
        - line: 4 (xlLine)
        - bar: 51 (xlColumnClustered)
        - pie: 5 (xlPie)
        """
        normalized = str(chart_type or "line").strip().lower()
        if normalized in {"bar", "column"}:
            return 51
        if normalized in {"pie", "donut"}:
            return 5
        return 4

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        return False

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            if isinstance(value, bool):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _parse_datetime_value(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace("/", "-")
        try:
            return datetime.fromisoformat(normalized)
        except Exception:
            return None

    @classmethod
    def _idx_to_col(cls, idx: int) -> str:
        if idx < 1:
            raise ValueError(f"유효하지 않은 열 인덱스: {idx}")
        result = ""
        n = idx
        while n > 0:
            n, rem = divmod(n - 1, 26)
            result = chr(ord("A") + rem) + result
        return result

    @staticmethod
    def _resolve_target_range(sheet: Any, target_range: str):
        """A:A 같은 전체 열 범위를 used_range 기준으로 축소한다."""
        text = str(target_range or "").strip().upper()
        col_match = re.fullmatch(r"([A-Z]+):([A-Z]+)", text)
        if not col_match:
            return sheet.range(text)

        used = getattr(sheet, "used_range", None)
        if used is None:
            return sheet.range(text)

        used_start_row = int(getattr(used, "row", 1) or 1)
        used_rows_obj = getattr(used, "rows", None)
        used_row_count = int(getattr(used_rows_obj, "count", 1) or 1)
        used_end_row = max(used_start_row, used_start_row + used_row_count - 1)
        left_col, right_col = col_match.groups()
        return sheet.range(f"{left_col}{used_start_row}:{right_col}{used_end_row}")


_excel_live_service: ExcelLiveService | None = None
_excel_live_service_engine: str | None = None


def get_excel_live_service() -> ExcelLiveService:
    """
    Excel Live 서비스 싱글톤 반환.

    환경변수:
    - EXCEL_LIVE_ENGINE=file (기본): openpyxl로 파일을 직접 편집. Excel 앱이 없어도 된다.
    - EXCEL_LIVE_ENGINE=xlwings: 실행 중인 Excel 앱을 제어. 피벗·매크로 등 앱 기능이 필요할 때.

    예전 설정 파일이 쓰던 "pandas"는 "file"과 같은 뜻으로 받는다.
    """
    global _excel_live_service, _excel_live_service_engine
    engine = str(os.getenv("EXCEL_LIVE_ENGINE", "file") or "file").strip().lower()
    if engine == "pandas":
        engine = "file"
    if engine not in {"xlwings", "file"}:
        engine = "file"

    if _excel_live_service is None or _excel_live_service_engine != engine:
        if engine == "file":
            from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService

            _excel_live_service = FileExcelLiveService()
        else:
            _excel_live_service = ExcelLiveService()
        _excel_live_service_engine = engine
    return _excel_live_service

