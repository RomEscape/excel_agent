"""
Excel Live Service — 실행 중인 Excel Desktop을 실시간 제어하는 서비스.

MVP Day 1 범위:
  - Excel 연결 가능 여부 확인
  - 열린 통합문서 목록 조회
  - 통합문서 선택/조회 상태 관리
  - 기본 범위 읽기(read_range)

플랫폼:
  - **Windows(COM)와 macOS(Apple Events) 양쪽을 지원한다.** 두 경로의 차이는
    xlwings의 고수준 API가 대부분 흡수하므로 이 모듈은 플랫폼을 몰라도 된다.
    흡수되지 않는 유일한 영역이 테두리 서식이고, 그건 `excel_border`가 전부
    떠안는다(같은 모듈의 docstring 참조).
  - 색을 직접 다룰 때는 `cell.color = (r, g, b)`처럼 **xlwings 고수준 API**를
    쓴다. COM 정수(BGR)를 만들어 넘기면 macOS에서 예외 없이 조용히 검게
    칠해진다 — 그래서 COM 색 변환 헬퍼를 이 모듈에 두지 않는다.
  - xlwings 의존성은 lazy import로 처리해 Excel이 없는 CI·테스트 환경에서도
    모듈 import 자체는 실패하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from typing import Any

from office_claw_sidecar.services import excel_border


class ExcelLiveError(Exception):
    """Excel Live 서비스 기본 예외."""


class ExcelDependencyError(ExcelLiveError):
    """xlwings 및 그 플랫폼 백엔드(pywin32 / appscript) 누락 또는 import 실패."""


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
    """실행 중인 Excel 제어 서비스 (Windows COM / macOS Apple Events)."""

    def __init__(self, xw_module: Any | None = None) -> None:
        self._xw = xw_module
        self._selected_workbook_id: str | None = None

    def _xw_module(self) -> Any:
        if self._xw is not None:
            return self._xw
        try:
            import xlwings as xw  # type: ignore[import]
        except Exception as exc:  # pragma: no cover - 환경 의존
            # 백엔드 이름을 플랫폼에 맞게 말한다 — macOS 사용자에게 pywin32를
            # 설치하라고 하면 해결할 수 없는 지시가 된다.
            backend = "appscript" if sys.platform == "darwin" else "pywin32"
            raise ExcelDependencyError(
                f"xlwings 모듈을 불러올 수 없습니다. xlwings와 {backend}가 "
                "설치돼 있는지 확인해 주세요."
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

        # 테두리는 Windows(COM)와 macOS(AppleScript)의 API가 완전히 달라서
        # excel_border 모듈이 플랫폼 분기를 전부 흡수한다. 특히 색은 COM 정수를
        # macOS에 그대로 넘기면 예외 없이 검정이 되므로 여기서 변환하지 않는다.
        try:
            excel_border.apply_borders(
                api_range,
                line_style=line_style,
                weight=weight,
                rgb=self._hex_to_rgb(color),
            )
        except excel_border.BorderUnsupportedError as exc:
            raise ExcelLiveError(str(exc)) from exc

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

    def calculate_column_stat(
        self,
        workbook_id: str | None,
        sheet_name: str,
        column: str,
        stat: str = "sum",
    ) -> dict[str, Any]:
        """
        지정 열의 숫자 통계(sum/average/min/max/count)를 계산한다.

        column은 머리글 이름(예: '매출') 또는 열 문자(예: 'B')를 허용한다.
        머리글 이름이 우선 매칭되고, 매칭 실패 시 열 문자로 해석한다.
        """
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")

        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)

        col_letter, header = self._resolve_column_letter(sheet, column)
        rng = self._resolve_target_range(sheet, f"{col_letter}:{col_letter}")
        values = self._normalize_values(rng.options(ndim=2).value)

        numbers: list[float] = []
        for row in values:
            cell = row[0] if row else None
            if isinstance(cell, bool):
                continue
            try:
                numbers.append(float(cell))
            except (TypeError, ValueError):
                continue

        stat_key = (stat or "sum").strip().lower()
        if stat_key == "count":
            value: float = float(len(numbers))
        elif not numbers:
            raise ExcelLiveError(f"'{column}' 열에서 숫자 데이터를 찾지 못했습니다.")
        elif stat_key == "sum":
            value = sum(numbers)
        elif stat_key == "average":
            value = sum(numbers) / len(numbers)
        elif stat_key == "min":
            value = min(numbers)
        elif stat_key == "max":
            value = max(numbers)
        else:
            raise ExcelLiveError(f"지원하지 않는 통계: {stat} (허용: sum, average, min, max, count)")

        return {
            "column": col_letter,
            "header": header,
            "stat": stat_key,
            "value": value,
            "numeric_count": len(numbers),
            "address": str(getattr(rng, "address", f"{col_letter}:{col_letter}")),
        }

    def _resolve_column_letter(self, sheet: Any, column: str) -> tuple[str, str | None]:
        """
        열 지정 문자열을 (열 문자, 매칭된 머리글) 튜플로 해석한다.

        1) used_range 첫 행에서 머리글 이름 매칭 시도
        2) 실패 시 열 문자(A~XFD 형식)로 해석
        """
        text = str(column or "").strip()
        if not text:
            raise ExcelLiveError("열 이름 또는 열 문자가 필요합니다.")

        used = getattr(sheet, "used_range", None)
        if used is not None:
            try:
                start_row = int(getattr(used, "row", 1) or 1)
                start_col = int(getattr(used, "column", 1) or 1)
                cols_obj = getattr(used, "columns", None)
                col_count = int(getattr(cols_obj, "count", 1) or 1)
                first_letter = self._idx_to_col(start_col)
                last_letter = self._idx_to_col(start_col + col_count - 1)
                header_rng = sheet.range(f"{first_letter}{start_row}:{last_letter}{start_row}")
                header_rows = self._normalize_values(header_rng.options(ndim=2).value)
                headers = header_rows[0] if header_rows else []
                lowered = text.lower()
                for idx, header in enumerate(headers):
                    if isinstance(header, str) and header.strip().lower() == lowered:
                        return self._idx_to_col(start_col + idx), header.strip()
            except ExcelLiveError:
                raise
            except Exception:
                # 머리글 조회 실패는 비치명적 — 열 문자 해석으로 폴백
                pass

        if re.fullmatch(r"[A-Za-z]{1,3}", text):
            return text.upper(), None

        raise ExcelLiveError(f"열을 찾을 수 없습니다: {column} (머리글 이름 또는 열 문자를 지정해 주세요)")

    # ── 데이터 변환 (used range 테이블 → 메모리 변환 → write-back) ──────────
    #
    # 공통 전제: used range의 첫 행을 머리글(header)로 본다.
    # 변환 결과는 원래 영역 크기로 패딩(None)해 한 번에 다시 써서,
    # 삭제된 행/열이 화면에서 즉시 비워지도록 한다 (라이브 UX).

    def filter_rows(
        self,
        workbook_id: str | None,
        sheet_name: str,
        column: str,
        operator: str,
        value: Any,
        keep_matching: bool = True,
    ) -> dict[str, Any]:
        """조건에 맞는 데이터 행만 남기고 나머지 행을 제거한다."""
        table = self._read_table(workbook_id, sheet_name)
        col_idx = self._table_col_index(table, column)

        kept: list[list[Any]] = []
        removed = 0
        for row in table["rows"]:
            cell = row[col_idx] if col_idx < len(row) else None
            matched = self._matches_filter(cell, operator, value)
            if matched == keep_matching:
                kept.append(row)
            else:
                removed += 1

        self._write_table(table, kept)
        return {
            "kept_rows": len(kept),
            "removed_rows": removed,
            "column": table["headers"][col_idx],
            "address": table["address"],
        }

    def sort_rows(
        self,
        workbook_id: str | None,
        sheet_name: str,
        column: str,
        order: str = "asc",
    ) -> dict[str, Any]:
        """지정 열 기준으로 데이터 행을 정렬한다 (머리글 제외)."""
        table = self._read_table(workbook_id, sheet_name)
        col_idx = self._table_col_index(table, column)
        descending = str(order or "asc").strip().lower() == "desc"

        def sort_key(row: list[Any]):
            cell = row[col_idx] if col_idx < len(row) else None
            if isinstance(cell, bool) or cell is None:
                return (2, "", 0.0)
            try:
                return (0, "", float(cell))
            except (TypeError, ValueError):
                return (1, str(cell), 0.0)

        ordered = sorted(table["rows"], key=sort_key, reverse=descending)
        self._write_table(table, ordered)
        return {
            "sorted_rows": len(ordered),
            "column": table["headers"][col_idx],
            "order": "desc" if descending else "asc",
            "address": table["address"],
        }

    def dedupe_rows(
        self,
        workbook_id: str | None,
        sheet_name: str,
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """중복 데이터 행을 제거한다 (첫 등장 행 유지). columns 생략 시 전체 열 기준."""
        table = self._read_table(workbook_id, sheet_name)
        if columns:
            key_indexes = [self._table_col_index(table, c) for c in columns]
        else:
            key_indexes = list(range(len(table["headers"])))

        seen: set[tuple] = set()
        kept: list[list[Any]] = []
        removed = 0
        for row in table["rows"]:
            key = tuple(
                str(row[i]).strip() if i < len(row) and row[i] is not None else ""
                for i in key_indexes
            )
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(row)

        self._write_table(table, kept)
        return {
            "kept_rows": len(kept),
            "removed_duplicates": removed,
            "address": table["address"],
        }

    def drop_column(
        self,
        workbook_id: str | None,
        sheet_name: str,
        column: str,
    ) -> dict[str, Any]:
        """지정 열을 테이블에서 제거한다 (오른쪽 열들이 왼쪽으로 당겨진 값으로 재기록)."""
        table = self._read_table(workbook_id, sheet_name)
        col_idx = self._table_col_index(table, column)
        dropped_header = table["headers"][col_idx]

        new_headers = [h for i, h in enumerate(table["headers"]) if i != col_idx]
        new_rows = [
            [cell for i, cell in enumerate(row) if i != col_idx] for row in table["rows"]
        ]
        self._write_table(table, new_rows, headers=new_headers)
        return {
            "dropped_column": dropped_header,
            "remaining_columns": len(new_headers),
            "address": table["address"],
        }

    def rename_column(
        self,
        workbook_id: str | None,
        sheet_name: str,
        column: str,
        new_name: str,
    ) -> dict[str, Any]:
        """지정 열의 머리글 이름을 변경한다."""
        if not str(new_name or "").strip():
            raise ExcelLiveError("new_name(새 머리글 이름)이 필요합니다.")
        table = self._read_table(workbook_id, sheet_name)
        col_idx = self._table_col_index(table, column)
        old_name = table["headers"][col_idx]

        header_cell = f"{self._idx_to_col(table['start_col'] + col_idx)}{table['start_row']}"
        table["sheet"].range(header_cell).value = str(new_name).strip()
        return {
            "old_name": old_name,
            "new_name": str(new_name).strip(),
            "address": header_cell,
        }

    def add_column(
        self,
        workbook_id: str | None,
        sheet_name: str,
        name: str,
        formula_a1: str | None = None,
    ) -> dict[str, Any]:
        """테이블 오른쪽 끝에 새 열을 추가한다. formula_a1이 있으면 데이터 행에 수식을 채운다."""
        if not str(name or "").strip():
            raise ExcelLiveError("name(새 열 머리글)이 필요합니다.")
        table = self._read_table(workbook_id, sheet_name)
        new_col_letter = self._idx_to_col(table["start_col"] + len(table["headers"]))

        header_cell = f"{new_col_letter}{table['start_row']}"
        table["sheet"].range(header_cell).value = str(name).strip()

        filled_cells = 0
        if formula_a1:
            formula = str(formula_a1).strip()
            if not formula.startswith("="):
                raise ExcelLiveError("formula_a1은 '='로 시작해야 합니다.")
            data_count = len(table["rows"])
            if data_count > 0:
                first_data_row = table["start_row"] + 1
                last_data_row = table["start_row"] + data_count
                data_range = f"{new_col_letter}{first_data_row}:{new_col_letter}{last_data_row}"
                rng = table["sheet"].range(data_range)
                rng.formula = formula
                filled_cells = data_count

        return {
            "column": new_col_letter,
            "name": str(name).strip(),
            "formula_filled_cells": filled_cells,
            "address": header_cell,
        }

    def group_by_aggregate(
        self,
        workbook_id: str | None,
        sheet_name: str,
        group_column: str,
        agg: str = "sum",
        value_column: str | None = None,
    ) -> dict[str, Any]:
        """
        그룹별 집계를 계산해 JSON으로만 반환한다 (시트는 수정하지 않음 — 읽기 전용).

        agg=count면 value_column 없이 그룹별 행 개수를 센다.
        """
        table = self._read_table(workbook_id, sheet_name)
        group_idx = self._table_col_index(table, group_column)
        agg_key = str(agg or "sum").strip().lower()

        value_idx: int | None = None
        if agg_key != "count":
            if not value_column:
                raise ExcelLiveError(f"agg={agg_key}에는 value_column(집계 대상 열)이 필요합니다.")
            value_idx = self._table_col_index(table, value_column)

        grouped: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for row in table["rows"]:
            raw_key = row[group_idx] if group_idx < len(row) else None
            key = str(raw_key).strip() if raw_key is not None else "(빈 값)"
            counts[key] = counts.get(key, 0) + 1
            if value_idx is None:
                continue
            cell = row[value_idx] if value_idx < len(row) else None
            if isinstance(cell, bool):
                continue
            try:
                grouped.setdefault(key, []).append(float(cell))
            except (TypeError, ValueError):
                continue

        groups: list[dict[str, Any]] = []
        for key, count in counts.items():
            if agg_key == "count":
                groups.append({"key": key, "value": float(count), "count": count})
                continue
            numbers = grouped.get(key, [])
            if not numbers:
                groups.append({"key": key, "value": None, "count": count})
                continue
            if agg_key == "sum":
                value = sum(numbers)
            elif agg_key == "average":
                value = sum(numbers) / len(numbers)
            elif agg_key == "min":
                value = min(numbers)
            elif agg_key == "max":
                value = max(numbers)
            else:
                raise ExcelLiveError(
                    f"지원하지 않는 집계: {agg} (허용: sum, average, min, max, count)"
                )
            groups.append({"key": key, "value": value, "count": count})

        groups.sort(key=lambda g: (g["value"] is None, -(g["value"] or 0.0)))
        return {
            "group_column": table["headers"][group_idx],
            "value_column": table["headers"][value_idx] if value_idx is not None else None,
            "agg": agg_key,
            "groups": groups,
        }

    # ── 테이블 공통 헬퍼 ─────────────────────────────────────────────────────

    def _read_table(self, workbook_id: str | None, sheet_name: str) -> dict[str, Any]:
        """used range를 머리글 + 데이터 행 테이블로 읽는다."""
        target_id = workbook_id or self._selected_workbook_id
        if not target_id:
            raise WorkbookNotFoundError("workbook_id가 필요합니다.")
        wb = self._find_workbook(target_id)
        sheet = self._find_sheet(wb, sheet_name)

        used = getattr(sheet, "used_range", None)
        if used is None:
            raise ExcelLiveError("시트의 사용 범위를 확인할 수 없습니다.")
        start_row = int(getattr(used, "row", 1) or 1)
        start_col = int(getattr(used, "column", 1) or 1)
        rows_obj = getattr(used, "rows", None)
        cols_obj = getattr(used, "columns", None)
        row_count = int(getattr(rows_obj, "count", 1) or 1)
        col_count = int(getattr(cols_obj, "count", 1) or 1)

        values = self._normalize_values(used.options(ndim=2).value)
        if not values:
            raise ExcelLiveError("시트에 데이터가 없습니다.")

        headers = [
            str(cell).strip() if cell is not None else "" for cell in values[0]
        ]
        end_cell = f"{self._idx_to_col(start_col + col_count - 1)}{start_row + row_count - 1}"
        address = f"{self._idx_to_col(start_col)}{start_row}:{end_cell}"
        return {
            "sheet": sheet,
            "start_row": start_row,
            "start_col": start_col,
            "row_count": row_count,
            "col_count": col_count,
            "headers": headers,
            "rows": [list(r) for r in values[1:]],
            "address": address,
        }

    def _table_col_index(self, table: dict[str, Any], column: str) -> int:
        """열 지정(머리글 이름 또는 열 문자)을 테이블 내 0-기반 인덱스로 해석한다."""
        text = str(column or "").strip()
        if not text:
            raise ExcelLiveError("열 이름 또는 열 문자가 필요합니다.")

        lowered = text.lower()
        for idx, header in enumerate(table["headers"]):
            if header and header.lower() == lowered:
                return idx

        if re.fullmatch(r"[A-Za-z]{1,3}", text):
            absolute = 0
            for ch in text.upper():
                absolute = absolute * 26 + (ord(ch) - ord("A") + 1)
            relative = absolute - table["start_col"]
            if 0 <= relative < len(table["headers"]):
                return relative

        raise ExcelLiveError(f"열을 찾을 수 없습니다: {column} (머리글 이름 또는 열 문자를 지정해 주세요)")

    def _write_table(
        self,
        table: dict[str, Any],
        rows: list[list[Any]],
        headers: list[str] | None = None,
    ) -> None:
        """머리글 + 데이터 행을 원래 used range 크기로 패딩해 한 번에 다시 쓴다."""
        out_headers = headers if headers is not None else table["headers"]
        col_count = table["col_count"]
        row_count = table["row_count"]

        def pad_row(row: list[Any]) -> list[Any]:
            padded = list(row)[:col_count]
            return padded + [None] * (col_count - len(padded))

        padded: list[list[Any]] = [pad_row(list(out_headers))]
        for row in rows:
            padded.append(pad_row(row))
        while len(padded) < row_count:
            padded.append([None] * col_count)

        top_left = f"{self._idx_to_col(table['start_col'])}{table['start_row']}"
        rng = table["sheet"].range(top_left).resize(len(padded), col_count)
        rng.value = padded

    @staticmethod
    def _matches_filter(cell: Any, operator: str, value: Any) -> bool:
        """숫자/문자 겸용 필터 조건 판정."""
        op = str(operator or "").strip()

        if op == "contains":
            if cell is None:
                return False
            return str(value).strip().lower() in str(cell).strip().lower()

        cell_num: float | None = None
        value_num: float | None = None
        if not isinstance(cell, bool):
            try:
                cell_num = float(cell)
            except (TypeError, ValueError):
                cell_num = None
        try:
            value_num = float(value)
        except (TypeError, ValueError):
            value_num = None

        if op in {">", ">=", "<", "<="}:
            if cell_num is None or value_num is None:
                return False
            return ExcelLiveService._matches_condition(cell_num, op, value_num)

        if op in {"==", "!="}:
            if cell_num is not None and value_num is not None:
                equal = cell_num == value_num
            else:
                equal = str(cell).strip().lower() == str(value).strip().lower()
            return equal if op == "==" else not equal

        raise ExcelLiveError(
            f"지원하지 않는 필터 연산자: {operator} (허용: >, >=, <, <=, ==, !=, contains)"
        )

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

    #: 강조 셀 주위에 그리는 연회색 격자.
    _GRIDLINE_RGB = (217, 217, 217)

    @staticmethod
    def _ensure_visual_gridline(cell: Any, rgb: tuple[int, int, int]) -> None:
        """셀 경계선이 비어 있으면 얇은 보더를 적용해 시인성을 높인다.

        플랫폼 분기는 excel_border가 소유한다 — 이전에는 COM 전용이라
        macOS에서 조용히 건너뛰었다.
        """
        excel_border.apply_outline_if_absent(
            getattr(cell, "api", None), ExcelLiveService._GRIDLINE_RGB
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

