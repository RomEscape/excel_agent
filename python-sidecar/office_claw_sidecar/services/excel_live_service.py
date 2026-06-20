"""
Excel Live Service — 실행 중인 Excel(COM) 실시간 제어용 서비스.

MVP Day 1 범위:
  - Excel 연결 가능 여부 확인
  - 열린 통합문서 목록 조회
  - 통합문서 선택/조회 상태 관리
  - 기본 범위 읽기(read_range)

주의:
  - Windows + Excel Desktop 환경을 전제로 한다.
  - xlwings 의존성은 lazy import로 처리하여 비-Windows 테스트 환경에서도
    모듈 import 자체는 실패하지 않도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


class ExcelLiveError(Exception):
    """Excel Live 서비스 기본 예외."""


class ExcelDependencyError(ExcelLiveError):
    """xlwings/pywin32 의존성 누락 또는 import 실패."""


class ExcelConnectionError(ExcelLiveError):
    """실행 중인 Excel 인스턴스 연결 실패."""


class WorkbookNotFoundError(ExcelLiveError):
    """요청한 통합문서를 찾지 못함."""


class WorksheetNotFoundError(ExcelLiveError):
    """요청한 시트를 찾지 못함."""


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
    """실행 중인 Excel(COM) 제어 서비스."""

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
                "xlwings 모듈을 불러올 수 없습니다. Windows 환경에서 xlwings/pywin32를 설치해 주세요."
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
        values = self._normalize_values(rng.value)

        row_count = len(values)
        col_count = len(values[0]) if values else 0
        return {
            "values": values,
            "address": str(range_ref),
            "row_count": row_count,
            "col_count": col_count,
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
    ) -> dict[str, Any]:
        """조건에 맞는 셀 배경색을 변경한다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)
        rng = self._resolve_target_range(sheet, target_range)
        values = self._normalize_values(rng.options(ndim=2).value)
        rgb = self._hex_to_rgb(fill_color)

        start_row = int(getattr(rng, "row", 1))
        start_col = int(getattr(rng, "column", 1))

        matched = 0
        changed = 0
        for r_idx, row in enumerate(values):
            for c_idx, cell_value in enumerate(row):
                if self._matches_condition(cell_value, operator, threshold):
                    matched += 1
                    absolute_row = start_row + r_idx
                    absolute_col = start_col + c_idx
                    cell_ref = f"{self._idx_to_col(absolute_col)}{absolute_row}"
                    cell = sheet.range(cell_ref)
                    cell.color = rgb
                    changed += 1

        return {
            "matched_cells": matched,
            "changed_cells": changed,
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


def get_excel_live_service() -> ExcelLiveService:
    """ExcelLiveService 싱글톤 반환."""
    global _excel_live_service
    if _excel_live_service is None:
        _excel_live_service = ExcelLiveService()
    return _excel_live_service

