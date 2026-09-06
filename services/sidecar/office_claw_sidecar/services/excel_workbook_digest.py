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
# 라이브(xlwings) 엔진은 **사용자가 언제든 Excel 에서 타자할 수 있다.** 20초 캐시를 그대로
# 쓰면 방금 친 열 이름·행이 플래너에게 안 보인다(2026-09-06 실측: 타자 직후 재생성해도
# 같은 객체가 나왔고 used_range 가 A1:C3 에 머물렀다). 한 명령이 안에서 여러 번 읽는
# 비용만 아끼면 되므로 짧게 잡는다. 파일 엔진은 우리만 파일을 바꾸므로 그대로 20초.
_LIVE_CACHE_TTL_SECONDS = 3.0
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


# 다이제스트는 줄 단위 포맷이다. 셀 값에 개행이 있으면 "- 시트 …", "  열: …" 같은
# 구조 줄을 셀 하나가 위조할 수 있다 — 2026-08-16 실측에서 A1 한 칸이 프롬프트에
# 가짜 시트 줄을 만들어 냈다. 24자 절단은 표시 제한이지 방어가 아니다(24자면 한국어
# 지시문 한 문장이 들어간다). 여기서 구조 문자를 없애는 건 **프롬프트 문구를 안
# 바꾸므로** SFT 학습 형식과 무관하다(CLAUDE.md §3.5).
_STRUCTURE_CHARS = re.compile(r"[\r\n\t\x00-\x1f\x7f]+")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = _STRUCTURE_CHARS.sub(" ", str(value)).strip()
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


#: 한 시트에서 머리글을 붙일 표 블록 상한 — 대시보드도 보통 6개 안쪽이다.
_MAX_BLOCKS = 12
_BLOCK_REF = re.compile(r"^([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?$")


def _blocks_with_headers(
    service: Any, workbook_id: str | None, sheet_name: str, layout: dict[str, Any]
) -> list[dict[str, Any]]:
    """표 블록마다 머리글 줄을 읽어 붙인다.

    돌려주는 것: [{"ref": "A25:F30", "header_row": 25, "first_data_row": 26,
                   "last_row": 30, "columns": [{"letter","header"}, …]}]
    한 칸짜리 블록(제목 셀)과 읽기 실패는 건너뛴다 — 못 읽었다고 다이제스트를 버리지 않는다.
    """
    out: list[dict[str, Any]] = []
    refs = [str(r) for r in (layout.get("blocks") or []) if str(r).strip()]
    if not refs:
        return out
    reader = getattr(service, "read_computed_range", None) or getattr(service, "read_range", None)
    if reader is None:
        return out
    for ref in refs[:_MAX_BLOCKS]:
        m = _BLOCK_REF.match(ref.upper())
        if not m or not m.group(3):
            continue  # 한 칸 블록은 표가 아니다
        start_col, start_row, end_col, end_row = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if end_row <= start_row:
            continue  # 머리글만 있고 데이터가 없다
        try:
            payload = reader(workbook_id, sheet_name, f"{start_col}{start_row}:{end_col}{start_row}")
            header_values = (payload.get("values") or [[]])[0]
        except Exception:
            continue
        base = _start_column_index(f"{start_col}{start_row}")
        columns = [
            {"letter": _idx_to_col(base + offset), "header": _cell_text(cell)}
            for offset, cell in enumerate(header_values[:_MAX_COLS])
        ]
        if not any(str(c["header"]).strip() for c in columns):
            continue
        out.append(
            {
                "ref": ref.upper(),
                "header_row": start_row,
                "first_data_row": start_row + 1,
                "last_row": end_row,
                "columns": columns,
            }
        )
    return out


def _resolve_digest_workbook_id(service: Any, workbook_id: str | None) -> str | None:
    """다이제스트가 읽을 통합문서를 하나로 확정한다. 못 정하면 None(예전 동작).

    라이브 엔진의 `read_range` 는 지목이 없으면 폴백 없이 거절하므로, 여기서 활성·선택
    통합문서의 경로를 미리 구해 둬야 머리글과 예시행이 채워진다(2026-09-06 감사).
    조회는 전부 편의라 실패하면 조용히 원래 값을 돌려준다.
    """
    if workbook_id:
        return workbook_id
    getter = getattr(service, "get_selected_workbook_id", None)
    if callable(getter):
        try:
            selected = str(getter() or "").strip()
            if selected:
                return selected
        except Exception:
            pass
    # 라이브 엔진: 선택이 없으면 활성 통합문서의 경로. (파일 엔진에는 이 메서드가 없을 수 있다.)
    path_getter = getattr(service, "get_workbook_path", None)
    if callable(path_getter):
        try:
            path = str(path_getter(None) or "").strip()
            if path:
                return path
        except Exception:
            pass
    try:
        rows = service.list_workbooks() or []
        if len(rows) == 1:
            # 여러 개면 어느 것이 사용자의 관심사인지 알 수 없다 — 예전처럼 엔진에 맡긴다.
            return str(rows[0].get("workbook_id") or "").strip() or None
    except Exception:
        pass
    return None


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
    # 대상 통합문서를 **먼저 확정한다**(2026-09-06 감사).
    #
    # 프론트는 workbook_id 를 항상 null 로 보내고(WorkspacePage 가 그렇게 부른다) 사용자가
    # 파일을 클릭하지 않았으면 선택도 비어 있다. 그런데 `list_sheets`·`get_used_range_ref` 는
    # 활성 통합문서로 폴백하는 반면 `read_range` 는 폴백 없이 WorkbookNotFoundError 를 던진다.
    # 그래서 시트 목록과 사용 범위는 채워지고 **머리글·예시행만 통째로 비었다** — 플래너는
    # 사용자가 방금 타자한 열 이름을 못 본 채 파라미터를 추측했다. 여기서 한 번 확정해
    # 아래 모든 호출이 같은 통합문서를 보게 한다.
    workbook_id = _resolve_digest_workbook_id(service, workbook_id)

    cache_key = str(workbook_id or "__selected__")
    now = time.monotonic()
    # 라이브 엔진은 사용자가 그 사이에 타자했을 수 있어 캐시를 짧게 잡는다.
    ttl = _LIVE_CACHE_TTL_SECONDS if str(getattr(service, "engine", "")) == "xlwings" else _CACHE_TTL_SECONDS
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
        # 대시보드는 한 시트에 표를 여럿 쌓는다. `columns`는 1행만 담으므로, 아래쪽 표의
        # 머리글("지연일수")을 부르면 못 찾아 조건부 서식이 0칸에 걸렸다(2026-08-20 ex23).
        # 블록마다 첫 줄을 읽어 머리글과 데이터 행 범위를 붙인다.
        entry["blocks"] = _blocks_with_headers(service, workbook_id, name, entry.get("layout") or {})
        digest["sheets"].append(entry)

    if use_cache:
        _digest_cache[cache_key] = (now + ttl, digest)
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


def first_free_column(used_range: str) -> str:
    """사용 범위 **오른쪽 첫 빈 열**. 파생 값을 여기부터 쓰면 원본을 안 건드린다.

    2026-08-16 실측: few-shot 예시가 "J1에 매출 입력"처럼 열을 하드코딩해 두었더니,
    J~M에 이미 클레임·거리·연료·평점이 있는 물류 통합문서에서도 그대로 J에 썼다.
    1단계가 `A1:M201 병합해줘`였고 201행이 통째로 사라졌다.
    규칙으로 "빈 열을 쓰라"고만 하면 모델이 어느 열이 비었는지 모른다 — 알려 줘야 한다.
    """
    text = str(used_range or "").replace("$", "").strip().upper()
    if not text:
        return ""
    right = text.rpartition(":")[2] or text
    match = re.match(r"([A-Z]{1,3})\d", right)
    if not match:
        return ""
    idx = 0
    for ch in match.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    out, n = "", idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


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
        used = str(sheet.get("used_range") or "")
        free = first_free_column(used)
        free_note = f" 빈열={free}부터" if free else ""
        lines.append(f"- 시트 {name}{marker} 사용범위={used or '비어있음'}{free_note}")
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
