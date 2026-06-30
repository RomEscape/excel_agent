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
        style_map = {"continuous": 1}
        weight_map = {"thin": 2, "medium": -4138, "thick": 4}
        line_style_value = style_map.get((line_style or "").strip().lower(), 1)
        weight_value = weight_map.get((weight or "").strip().lower(), 2)
        border_color = self._rgb_to_excel_color(self._hex_to_rgb(color))
        edges = (7, 8, 9, 10, 11, 12)  # left, top, bottom, right, inside_v, inside_h

        for edge in edges:
            border = api_range.Borders(edge)
            border.LineStyle = line_style_value
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

