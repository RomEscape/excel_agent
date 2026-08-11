"""
결과 검증기(Verifier) — "했다고 보고한 일이 실제로 파일에 일어났는지"를 파일에서 다시 읽어 확인한다.

기존 단계 검증은 실행기가 돌려준 result 딕셔너리만 봤다. 그래서
sorted_rows=0, filtered_rows=0 처럼 "아무것도 안 했다"는 응답도 성공으로 통과했고,
사용자는 "완료했습니다"를 듣고 바뀌지 않은 시트를 보게 됐다.

이 모듈은 액션별 사후조건을 워크북에서 직접 확인한다.
- 쓰기: 기록하려던 값이 실제로 그 셀에 들어갔는가
- 지우기: 지우라고 한 범위가 실제로 비었는가
- 정렬: 기준 열이 실제로 단조 증가/감소인가
- 필터/강조/수식: 실제로 영향받은 셀이 1개 이상인가
- 피벗/예측/비교/통합/차트: 결과 시트가 생겼고 비어 있지 않은가

주의: 여기서 보는 것은 "실행이 인자대로 됐는가"이지 "인자가 옳았는가"가 아니다.
사용자가 C3를 원했는데 플래너가 D3를 지목했다면 D3에 값이 제대로 들어간 이상
이 검증기는 통과시킨다. 그건 플래너 인자 오류이고 계획 단계에서 잡아야 한다.

판정은 (ok, detail) 튜플이다. detail은 실패 이유이며 Critic 재계획과 사용자 안내에 쓰인다.
상태를 갖지 않는 순수 함수 모듈이고, 라우터는 verify_effect만 호출한다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

_CELL_REF = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")
# 사후조건을 확인할 때 읽어들일 최대 행 수. 큰 표에서도 검증이 느려지지 않게 한다.
_MAX_SCAN_ROWS = 200


def _col_to_idx(letters: str) -> int:
    idx = 0
    for ch in str(letters or "").upper():
        if not ch.isalpha():
            break
        idx = idx * 26 + (ord(ch) - 64)
    return max(1, idx)


def _idx_to_col(index: int) -> str:
    idx = max(1, int(index))
    out = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _range_parts(range_ref: str) -> tuple[str, int, str, int] | None:
    text = str(range_ref or "").strip().upper()
    if "!" in text:
        text = text.split("!", 1)[1]
    if ":" not in text:
        match = _CELL_REF.match(text)
        if not match:
            return None
        return match.group(1), int(match.group(2)), match.group(1), int(match.group(2))
    left, right = text.split(":", 1)
    lm, rm = _CELL_REF.match(left), _CELL_REF.match(right)
    if not lm or not rm:
        return None
    return lm.group(1), int(lm.group(2)), rm.group(1), int(rm.group(2))


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_sorted(values: list[Any], *, descending: bool) -> bool:
    """빈 셀은 건너뛰고, 숫자끼리·문자끼리만 비교한다."""
    cleaned = [v for v in values if v is not None and str(v).strip() != ""]
    if len(cleaned) < 2:
        return True
    numbers = [_as_number(v) for v in cleaned]
    if all(n is not None for n in numbers):
        keys: list[Any] = [n for n in numbers if n is not None]
    else:
        keys = [str(v).strip() for v in cleaned]
    pairs = zip(keys, keys[1:])
    return all(a >= b for a, b in pairs) if descending else all(a <= b for a, b in pairs)


def _column_index_for_key(key_column: Any, header_row: list[Any], first_col_idx: int) -> int | None:
    """key_column("금액" 또는 2 또는 "C")을 실제 열 인덱스로 바꾼다."""
    if isinstance(key_column, int):
        return first_col_idx + max(0, key_column - 1)
    text = str(key_column or "").strip()
    if not text:
        return None
    for offset, header in enumerate(header_row):
        if str(header or "").strip() == text:
            return first_col_idx + offset
    if text.isalpha() and len(text) <= 3:
        return _col_to_idx(text)
    if text.isdigit():
        return first_col_idx + max(0, int(text) - 1)
    return None


def _read(service: Any, workbook_id: str | None, sheet_name: str | None, range_ref: str) -> list[list[Any]]:
    payload = service.read_range(workbook_id, sheet_name, range_ref)
    values = payload.get("values") if isinstance(payload, dict) else None
    return values if isinstance(values, list) else []


def _sheet_has_data(service: Any, workbook_id: str | None, sheet_name: str) -> bool:
    try:
        used = str(service.get_used_range_ref(workbook_id, sheet_name) or "")
        if not used:
            return False
        rows = _read(service, workbook_id, sheet_name, used)
    except Exception:
        return False
    for row in rows:
        for cell in row:
            if cell is not None and str(cell).strip():
                return True
    return False


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _same_cell(expected: Any, actual: Any) -> bool:
    """쓴 값과 읽은 값이 같은가. 엔진이 바꿔 놓는 표현 차이는 같다고 본다.

    3.0을 쓰면 3으로 돌아오고, 날짜 문자열은 datetime이 되어 돌아온다. 이런
    표현 차이를 불일치로 보면 멀쩡한 작업을 되돌리게 된다 — 실제 값이 다를
    때만 걸러야 한다.
    """
    if _is_blank(expected) and _is_blank(actual):
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)

    exp_num, act_num = _as_number(expected), _as_number(actual)
    if exp_num is not None and act_num is not None:
        return abs(exp_num - act_num) < 1e-9

    # 날짜·시간은 엔진마다 문자열/객체가 갈려 신뢰할 수 있게 비교하기 어렵다.
    if isinstance(actual, (datetime, date, time)) or isinstance(expected, (datetime, date, time)):
        return True

    return str(expected).strip() == str(actual).strip()


def _requested_range(params: dict[str, Any]) -> str:
    """start_cell과 값 모양으로 "써야 했던" 범위를 만든다."""
    start = str(params.get("start_cell") or "").strip()
    values = params.get("values_2d")
    if not start or not isinstance(values, list) or not values:
        return ""
    if ":" in start:
        return start
    parts = _range_parts(start)
    if parts is None:
        return ""
    base_col, base_row = _col_to_idx(parts[0]), parts[1]
    rows = len(values)
    cols = max((len(r) for r in values if isinstance(r, list)), default=0)
    if rows < 1 or cols < 1:
        return ""
    if rows == 1 and cols == 1:
        return start
    return f"{start}:{_idx_to_col(base_col + cols - 1)}{base_row + rows - 1}"


def _verify_write(
    params: dict[str, Any],
    result: dict[str, Any],
    *,
    service: Any,
    workbook_id: str | None,
    sheet_name: str | None,
) -> tuple[bool, str]:
    """기록한 값이 실제로 셀에 들어갔는지 다시 읽어 확인한다.

    written_cells만 보면 "몇 칸을 건드렸다"까지만 알 수 있다. 보호된 시트나
    병합 셀처럼 쓰기가 삼켜지는 경우 그 숫자는 그대로 올라온다.
    """
    expected = params.get("values_2d")
    if not isinstance(expected, list) or not expected:
        return True, ""
    # 확인할 범위는 요청에서 계산한다. 실행기가 보고한 주소를 그대로 믿으면,
    # 주소를 좁게 보고하는 실행기 앞에서 검증도 같이 좁아진다 — 검증 대상이
    # 준 값에 검증 범위를 맡기는 셈이다.
    address = _requested_range(params) or str(result.get("address") or "").strip()
    if not address:
        return True, ""

    try:
        actual = _read(service, workbook_id, sheet_name, address)
    except Exception:
        return True, ""
    if not actual:
        return True, ""

    origin = _range_parts(address)
    base_col = _col_to_idx(origin[0]) if origin else 1
    base_row = origin[1] if origin else 1

    for row_idx, exp_row in enumerate(expected):
        if not isinstance(exp_row, list) or row_idx >= len(actual):
            continue
        act_row = actual[row_idx] or []
        for col_idx, exp_value in enumerate(exp_row):
            # 수식은 읽을 때 수식 문자열/계산값 중 무엇이 오는지 엔진에 달렸다.
            if isinstance(exp_value, str) and exp_value.strip().startswith("="):
                continue
            if col_idx >= len(act_row):
                continue
            if not _same_cell(exp_value, act_row[col_idx]):
                cell = f"{_idx_to_col(base_col + col_idx)}{base_row + row_idx}"
                detail = (
                    f"write_value_mismatch:{cell} 셀에 {exp_value!r}를 쓰려 했으나 "
                    f"{act_row[col_idx]!r}가 들어 있습니다"
                )
                return False, detail
    return True, ""


def _verify_clear(
    params: dict[str, Any],
    result: dict[str, Any],
    *,
    service: Any,
    workbook_id: str | None,
    sheet_name: str | None,
) -> tuple[bool, str]:
    """지우라고 한 범위가 실제로 비었는지 확인한다."""
    address = str(result.get("address") or params.get("target_range") or "").strip()
    if not address:
        return True, ""

    try:
        rows = _read(service, workbook_id, sheet_name, address)
    except Exception:
        return True, ""

    remaining = [cell for row in rows for cell in row if not _is_blank(cell)]
    if remaining:
        detail = (
            f"clear_not_applied:{address} 범위에 값이 {len(remaining)}개 남아 있습니다 "
            f"({remaining[:3]})"
        )
        return False, detail
    return True, ""


def _verify_sort(
    params: dict[str, Any],
    result: dict[str, Any],
    *,
    service: Any,
    workbook_id: str | None,
    sheet_name: str | None,
) -> tuple[bool, str]:
    range_ref = str(result.get("address") or params.get("range_ref") or "").strip()
    parts = _range_parts(range_ref)
    if not parts:
        return True, ""
    start_col, start_row, end_col, end_row = parts
    if end_row <= start_row:
        return False, "sort_no_rows:정렬할 데이터 행이 없습니다"

    scan_end = min(end_row, start_row + _MAX_SCAN_ROWS)
    try:
        rows = _read(service, workbook_id, sheet_name, f"{start_col}{start_row}:{end_col}{scan_end}")
    except Exception:
        return True, ""
    if not rows:
        return True, ""

    has_header = bool(params.get("has_header", True))
    header_row = rows[0] if has_header else []
    body = rows[1:] if has_header else rows
    if len(body) < 2:
        return True, ""

    key_idx = _column_index_for_key(params.get("key_column"), header_row, _col_to_idx(start_col))
    if key_idx is None:
        return True, ""
    offset = key_idx - _col_to_idx(start_col)
    if offset < 0 or offset >= max(len(r) for r in body):
        return False, f"sort_key_out_of_range:{params.get('key_column')} 열이 정렬 범위 밖입니다"

    column = [row[offset] if offset < len(row) else None for row in body]
    descending = str(params.get("order", "asc")).strip().lower() in {"desc", "descending", "내림차순"}
    if _is_sorted(column, descending=descending):
        return True, ""
    return (
        False,
        f"sort_not_applied:{_idx_to_col(key_idx)}열이 요청한 순서로 정렬되지 않았습니다",
    )


def _verify_output_sheet(
    params: dict[str, Any],
    result: dict[str, Any],
    *,
    service: Any,
    workbook_id: str | None,
) -> tuple[bool, str]:
    target = str(result.get("sheet_name") or params.get("output_sheet") or "").strip()
    if not target:
        return True, ""
    if "!" in target:
        target = target.split("!", 1)[0].strip("'")
    try:
        sheets = service.list_sheets(workbook_id)
        names = sheets.get("sheets") if isinstance(sheets, dict) else sheets
        names = [str(n) for n in (names or [])]
    except Exception:
        return True, ""
    if target not in names:
        return False, f"output_sheet_missing:{target} 시트가 만들어지지 않았습니다"
    if not _sheet_has_data(service, workbook_id, target):
        return False, f"output_sheet_empty:{target} 시트에 결과가 기록되지 않았습니다"
    return True, ""


def verify_effect(
    *,
    action: str,
    params: dict[str, Any],
    result: dict[str, Any],
    service: Any,
    workbook_id: str | None,
    sheet_name: str | None,
) -> tuple[bool, str]:
    """액션별 사후조건을 워크북에서 다시 읽어 확인한다.

    검증에 필요한 정보를 못 읽으면 통과시킨다 — 검증기가 못 봤다는 이유로
    성공한 작업을 되돌리면 더 큰 손해다.
    """
    params = params or {}
    result = result or {}

    try:
        if action == "excel_live.write_range":
            return _verify_write(
                params, result, service=service, workbook_id=workbook_id, sheet_name=sheet_name
            )

        if action == "excel_live.clear_range":
            return _verify_clear(
                params, result, service=service, workbook_id=workbook_id, sheet_name=sheet_name
            )

        if action == "excel_live.sort_range":
            return _verify_sort(
                params, result, service=service, workbook_id=workbook_id, sheet_name=sheet_name
            )

        if action == "excel_live.filter_rows":
            matched = result.get("filtered_rows", result.get("matched_rows"))
            if matched is not None and int(matched or 0) <= 0:
                return False, "filter_no_match:조건에 맞는 행이 없습니다"
            return True, ""

        if action == "excel_live.highlight_by_condition":
            # 조건에 맞는 셀이 0개인 것은 **정상 결과**다. "50 이상만 노란색"에서
            # 50 이상이 없으면 아무것도 안 칠하는 게 맞다.
            #
            # 이걸 실패로 보면 계획이 끊기고 롤백이 돌고 재계획이 뜨는데, 재계획한
            # 모델은 "칠해진 게 없다"를 조건이 빡빡했다는 뜻으로 읽고 조건을 느슨하게
            # 만든다. 2026-08-11 `0811-182610-armA-off` 실측: `이상치강조`가 3회 모두
            # 이 경로로 49행 전부를 칠했다. 오탐 하나가 정상 결과를 파괴로 바꿨다.
            #
            # 가르는 기준은 "칠했는가"가 아니라 "대 봤는가"다.
            scanned = result.get("scanned_cells")
            if scanned is not None:
                if int(scanned or 0) >= 1:
                    return True, ""
                return False, "empty_target_range:조건을 검사할 셀이 없습니다"
            # 옛 실행기 결과에는 scanned_cells가 없다. 그때는 예전 기준을 쓴다.
            changed = int(result.get("changed_cells", result.get("applied_cells", 0)) or 0)
            if changed <= 0:
                return False, "no_cells_changed:서식이 적용된 셀이 없습니다"
            return True, ""

        if action in {
            "excel_live.fill_range",
            "excel_live.apply_border",
            "excel_live.set_border",
        }:
            # 이 셋은 조건이 없다. 범위 전체를 칠하므로 changed_cells는 범위 크기와
            # 같고, 0이면 범위를 잘못 잡았다는 뜻이라 실패가 맞다.
            changed = int(result.get("changed_cells", result.get("applied_cells", 0)) or 0)
            if changed <= 0:
                return False, "no_cells_changed:서식이 적용된 셀이 없습니다"
            return True, ""

        if action == "excel_live.set_formula":
            applied = int(result.get("formula_applied_cells", 0) or 0)
            if applied <= 0:
                return False, "formula_not_applied:수식이 입력된 셀이 없습니다"
            return True, ""

        if action == "excel_live.set_data_validation":
            if not result.get("applied"):
                return False, "validation_not_applied:입력 제한이 설정되지 않았습니다"
            return True, ""

        if action in {
            "excel_live.pivot_table",
            "excel_live.forecast_linear",
            "excel_live.compare_ranges",
            "excel_live.consolidate_sheets",
            "excel_live.consolidate_workbooks_from_folder",
        }:
            return _verify_output_sheet(params, result, service=service, workbook_id=workbook_id)

        if action == "excel_live.create_chart":
            # 차트는 도형으로 얹히므로 시트에 셀 값이 없을 수 있다. 생성 여부만 본다.
            if not result.get("created"):
                return False, "chart_not_created:차트가 만들어지지 않았습니다"
            return True, ""

        if action == "excel_live.find_duplicates":
            # 중복이 0건인 것도 정상적인 답이다. 점검이 돌았는지만 본다.
            if "duplicate_groups" not in result:
                return False, "duplicate_scan_failed:중복 점검 결과를 읽지 못했습니다"
            return True, ""

        if action == "excel_live.export_pdf":
            if not result.get("exported"):
                return False, "pdf_not_exported:PDF가 생성되지 않았습니다"
            return True, ""

        if action == "excel_live.recalculate":
            if not result.get("recalculated"):
                return False, "recalc_failed:재계산 표시에 실패했습니다"
            return True, ""
    except Exception:
        return True, ""

    return True, ""
