"""
워크북 다이제스트 — 플래너에게 "지금 이 파일이 어떻게 생겼는지"를 알려주는 그라운딩 레이어.

플래너가 시트/헤더를 모른 채 key_column·source_range·output_sheet를 추측하면
시트명이 컬럼명으로 들어가거나 결과가 원본을 덮어쓰는 오작동이 난다.
이 모듈은 실행 전에 시트 목록·사용 범위·머리글·샘플 행을 압축해 제공한다.

- 상태(캐시)와 조회 로직을 이 모듈이 소유한다.
- 라우터/에이전트는 build_workbook_digest / render_workbook_digest만 쓴다.
"""

from __future__ import annotations

import re
import time
from typing import Any

from office_claw_sidecar.services.excel_sheet_layout import render_layout

_MAX_SHEETS = 8
# 실무 시트는 12열을 쉽게 넘는다(데모 파일의 Sales_Data는 17열). 뒤쪽 열을 잘라내면
# 바인더가 "매출이익" 같은 열을 아예 못 찾아 되묻기로 떨어진다.
_MAX_COLS = 26
_SAMPLE_ROWS = 3
_MAX_CELL_TEXT = 24
_CACHE_TTL_SECONDS = 20.0
# 필터 값("완료된 것만")을 실제 셀 값으로 확정하려면 열마다 어떤 값이 들어 있는지 알아야 한다.
# 활성 시트만 조금 더 깊게 읽어 저카디널리티 열의 값 후보를 모은다.
_CATEGORY_SCAN_ROWS = 40
_MAX_CATEGORIES = 8

# (workbook_id) -> (expires_at, digest)
_digest_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _col_to_idx(col: str) -> int:
    idx = 0
    for ch in str(col or "").upper():
        if not ch.isalpha():
            continue
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(1, idx)


def _idx_to_col(idx: int) -> str:
    n = max(1, int(idx))
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _clamp_range(range_ref: str, *, max_rows: int, max_cols: int) -> str:
    """다이제스트용으로 읽을 범위를 좁힌다. 대형 시트 전체를 읽지 않기 위함."""
    text = str(range_ref or "").strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", text)
    if not match:
        return text or "A1"
    start_col, start_row, end_col, end_row = match.groups()
    row_end = min(int(start_row) + max_rows - 1, int(end_row))
    col_end_idx = min(_col_to_idx(start_col) + max_cols - 1, _col_to_idx(end_col))
    return f"{start_col}{start_row}:{_idx_to_col(col_end_idx)}{row_end}"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > _MAX_CELL_TEXT:
        text = text[: _MAX_CELL_TEXT - 1] + "…"
    return text


def _start_column_index(range_ref: str) -> int:
    match = re.match(r"([A-Z]+)\d+", str(range_ref or "").strip().upper())
    return _col_to_idx(match.group(1)) if match else 1


def _is_number(text: str) -> bool:
    try:
        float(str(text).replace(",", ""))
        return True
    except Exception:
        return False


def _summarize_columns(columns: list[dict[str, Any]], body_rows: list[list[Any]]) -> None:
    """열별로 숫자 여부와 값 후보를 채운다(원본 리스트를 그대로 갱신)."""
    for offset, column in enumerate(columns):
        values = [
            _cell_text(row[offset])
            for row in body_rows
            if offset < len(row) and _cell_text(row[offset])
        ]
        if not values:
            continue
        numeric_ratio = sum(1 for v in values if _is_number(v)) / len(values)
        column["numeric"] = numeric_ratio >= 0.8
        if column["numeric"]:
            continue
        distinct: list[str] = []
        for value in values:
            if value not in distinct:
                distinct.append(value)
            if len(distinct) > _MAX_CATEGORIES:
                break
        # 값이 매번 다른 자유 텍스트 열은 후보로 쓸 수 없다.
        if len(distinct) <= _MAX_CATEGORIES:
            column["categories"] = distinct


def build_workbook_digest(
    service: Any,
    *,
    workbook_id: str | None,
    active_sheet_hint: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """시트별 사용 범위·머리글·샘플 행을 담은 다이제스트를 만든다.

    실패해도 명령 처리를 막지 않도록 예외는 모두 삼키고 부분 결과를 돌려준다.
    """
    cache_key = str(workbook_id or "__selected__")
    now = time.monotonic()
    if use_cache:
        cached = _digest_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    digest: dict[str, Any] = {"active_sheet": "", "sheets": []}
    try:
        sheets_info = service.list_sheets(workbook_id)
    except Exception:
        return digest

    names = [str(n) for n in (sheets_info.get("sheets") or [])]
    digest["active_sheet"] = str(active_sheet_hint or sheets_info.get("active_sheet") or "")

    active_name = digest["active_sheet"]
    for name in names[:_MAX_SHEETS]:
        entry: dict[str, Any] = {"name": name, "used_range": "", "columns": [], "sample_rows": []}
        # 활성 시트는 값 후보까지 필요하므로 더 깊게 읽는다.
        scan_rows = _CATEGORY_SCAN_ROWS if name == active_name else _SAMPLE_ROWS
        try:
            used_range = str(service.get_used_range_ref(workbook_id, name) or "")
            entry["used_range"] = used_range
            read_ref = _clamp_range(used_range, max_rows=scan_rows + 1, max_cols=_MAX_COLS)
            # 수식 열은 "=I2*J2"로 읽히면 숫자 열이 아닌 것처럼 보인다. 계산값이 있으면 그쪽을 쓴다.
            reader = getattr(service, "read_computed_range", None) or service.read_range
            payload = reader(workbook_id, name, read_ref)
            values = payload.get("values") or []
        except Exception:
            digest["sheets"].append(entry)
            continue

        if values:
            base_col = _start_column_index(entry["used_range"] or "A1")
            header_row = values[0][:_MAX_COLS]
            entry["columns"] = [
                {"letter": _idx_to_col(base_col + offset), "header": _cell_text(cell)}
                for offset, cell in enumerate(header_row)
            ]
            body = [list(row[:_MAX_COLS]) for row in values[1:]]
            entry["sample_rows"] = [[_cell_text(cell) for cell in row] for row in body[:_SAMPLE_ROWS]]
            _summarize_columns(entry["columns"], body)

        # 값만으로는 "지금 어떻게 보이는가"를 알 수 없다. 수식·서식·블록을 함께 읽어
        # 다음 턴이 자기가 만든 것을 보고 다듬을 수 있게 한다. 엔진이 못 하면 조용히 건너뛴다.
        describe = getattr(service, "describe_sheet_layout", None)
        if callable(describe):
            try:
                entry["layout"] = describe(workbook_id, name)
            except Exception:
                pass
        digest["sheets"].append(entry)

    if use_cache:
        _digest_cache[cache_key] = (now + _CACHE_TTL_SECONDS, digest)
    return digest


def invalidate_workbook_digest(workbook_id: str | None = None) -> None:
    """통합문서를 건드린 직후 캐시를 버린다.

    캐시 TTL이 20초인데 매크로는 2~3초 간격으로 단계를 돌린다. 버리지 않으면 뒤 단계가
    **앞 단계가 방금 쓴 결과를 못 본다** — 2026-08-16 실측에서 열을 추가하고 굵게·수식을
    넣었는데 다음 다이제스트가 옛 사용범위와 빈 서식을 그대로 돌려줬다. 값만 보던 시절에는
    티가 덜 났지만, 서식까지 읽게 된 지금은 "자기가 만든 것을 보고 다듬는" 흐름이 통째로 막힌다.
    """
    if workbook_id is None:
        _digest_cache.clear()
        return
    _digest_cache.pop(str(workbook_id), None)
    # 선택된 통합문서로 담긴 항목도 같은 파일일 수 있다. 애매하면 버리는 쪽이 안전하다.
    _digest_cache.pop("__selected__", None)


def render_workbook_digest(digest: dict[str, Any], *, max_chars: int = 1600) -> str:
    """프롬프트에 넣을 수 있는 짧은 텍스트로 변환한다."""
    sheets = digest.get("sheets") or []
    if not sheets:
        return ""
    active = str(digest.get("active_sheet") or "")
    lines: list[str] = ["현재 통합문서 상태(실제 파일에서 읽음):"]
    for sheet in sheets:
        name = str(sheet.get("name") or "")
        marker = " (활성)" if name and name == active else ""
        lines.append(f"- 시트 {name}{marker} 사용범위={sheet.get('used_range') or '비어있음'}")
        columns = sheet.get("columns") or []
        if columns:
            rendered = " | ".join(
                f"{col.get('letter')}={col.get('header') or '(빈칸)'}" for col in columns
            )
            lines.append(f"  열: {rendered}")
        sample = sheet.get("sample_rows") or []
        if sample:
            lines.append(f"  예시행: {' | '.join(sample[0])}")
        # 활성 시트만 서식까지 보여 준다 — 전 시트를 다 적으면 프롬프트가 터진다.
        if name == active:
            lines.extend(render_layout(sheet.get("layout") or {}))
        if name == active:
            for col in columns:
                categories = col.get("categories") or []
                if len(categories) >= 2:
                    lines.append(f"  '{col.get('header')}' 값 후보: {', '.join(categories)}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text + "\n"


def invalidate_digest_cache(workbook_id: str | None = None) -> None:
    """편집 후 낡은 다이제스트가 재사용되지 않도록 캐시를 비운다."""
    if workbook_id is None:
        _digest_cache.clear()
        return
    _digest_cache.pop(str(workbook_id), None)
    _digest_cache.pop("__selected__", None)
