# -*- coding: utf-8 -*-
"""위치 지정 게이트 — "정확히 그 자리에, 다른 곳엔 손대지 않고"를 재는 40문.

말투 게이트는 '무엇을'의 강건성을 재고, 이 게이트는 '어디에'를 잰다(2026-09-01
지시: 위치 지정한 부분에 명령 수행이 되는지 상세 검증). 러너·오라클 기계는
run_blind_paraphrase_gate 것을 그대로 쓰고 TASKS만 위치 전용으로 갈아끼운다.

실행(락은 밖의 래퍼가 쥔다 — scripts/run_blind_subset.py 참조):
    cd services/sidecar
    python -u scripts/run_location_gate.py ../../datasets/eval/location_targeting_v1.jsonl
"""
from __future__ import annotations

import asyncio
import sys

import run_blind_paraphrase_gate as bg

# ── 공용 시드: A1:E6 격자 ───────────────────────────────────────────────────
GRID = [
    ["지역", "주문건수", "매출", "금액", "지연건수"],
    ["수도권", 120, 3400, 3400000, 3],
    ["충청권", 80, 2100, 2100000, 1],
    ["호남권", 95, 2600, 2600000, 2],
    ["영남권", 110, 3100, 3100000, 4],
    ["강원권", 60, 1500, 1500000, 0],
]
REGIONS = [r[0] for r in GRID[1:]]


def _seed_grid(wb):
    ws = wb.active
    ws.title = "데이터"
    for row in GRID:
        ws.append(row)


def _seed_two_sheets(wb):
    _seed_grid(wb)
    wb.create_sheet("요약")


def _seed_titled(wb):
    ws = wb.active
    ws.title = "데이터"
    ws["A1"] = "월간 보고서"
    for i, row in enumerate(GRID[:4], start=3):
        for j, v in enumerate(row, start=1):
            ws.cell(row=i, column=j, value=v)


def _grid_intact(ws, *, skip: set[str] = frozenset()) -> str:
    """시드 격자가 그대로인가. skip 좌표는 의도된 변경이라 건너뛴다."""
    for i, row in enumerate(GRID, start=1):
        for j, want in enumerate(row, start=1):
            ref = f"{chr(64 + j)}{i}"
            if ref in skip:
                continue
            got = ws.cell(row=i, column=j).value
            if got != want:
                return f"{ref}={got!r} (원본 {want!r} 훼손)"
    return ""


def _has_fill(cell) -> bool:
    fill = cell.fill
    return bool(fill and fill.patternType)


# ── 과제 정의 ───────────────────────────────────────────────────────────────

def _o_cell_far(wb):
    ws = wb["데이터"]
    if str(ws["F9"].value or "").strip() != "완료":
        return f"F9={ws['F9'].value!r} (완료가 아님)"
    for ref in ("E9", "G9", "F8", "F10"):
        if ws[ref].value not in (None, ""):
            return f"{ref}={ws[ref].value!r} (지정 밖 기록)"
    return _grid_intact(ws)


def _o_two_refs(wb):
    ws = wb["데이터"]
    if str(ws["B9"].value or "").strip() != "점검":
        return f"B9={ws['B9'].value!r} (점검이 아님)"
    if ws["C9"].value not in (None, ""):
        return f"C9={ws['C9'].value!r} ('말고'라고 한 자리에 기록)"
    return _grid_intact(ws)


def _o_range_fill(wb):
    ws = wb["데이터"]
    for ref in ("B2", "C3", "D4"):
        if not _has_fill(ws[ref]):
            return f"{ref} 채움 없음"
    for ref in ("A2", "E2", "B5", "D5"):
        if _has_fill(ws[ref]):
            return f"{ref} 채움 (지정 범위 밖)"
    return _grid_intact(ws)


def _o_row_delete(wb):
    ws = wb["데이터"]
    col_a = [str(c.value) for c in ws["A"] if c.value not in (None, "")]
    if "충청권" in col_a:
        return "충청권 행이 남아 있음"
    for name in ("수도권", "호남권", "영남권", "강원권"):
        if name not in col_a:
            return f"{name} 행이 사라짐 (지정 밖 삭제)"
    if col_a[0] != "지역":
        return f"머리글 훼손: {col_a[0]!r}"
    return ""


def _o_col_format(wb):
    ws = wb["데이터"]
    for i in range(2, 7):
        fmt = ws.cell(row=i, column=4).number_format
        if "," not in fmt:
            return f"D{i} 서식={fmt!r} (콤마 없음)"
    for col in (3, 5):
        for i in range(2, 7):
            fmt = ws.cell(row=i, column=col).number_format
            if "," in fmt:
                return f"{chr(64 + col)}{i} 서식={fmt!r} (지정 밖 열 변경)"
    return _grid_intact(ws)


def _o_col_header_bold(wb):
    ws = wb["데이터"]
    for i in range(2, 7):
        if not ws.cell(row=i, column=3).font.bold:
            return f"C{i} 굵게 아님"
    for col in (2, 4):
        for i in range(2, 7):
            if ws.cell(row=i, column=col).font.bold:
                return f"{chr(64 + col)}{i} 굵게 (지정 밖 열)"
    return _grid_intact(ws)


def _o_sheet_write(wb):
    if "요약" not in wb.sheetnames:
        return "요약 시트가 사라짐"
    got = str(wb["요약"]["B1"].value or "").strip()
    if got != "점검완료":
        return f"요약!B1={got!r} (점검완료가 아님)"
    ws = wb["데이터"]
    if ws["B1"].value != "주문건수":
        return f"데이터!B1={ws['B1'].value!r} (활성 시트에 오기록)"
    return _grid_intact(ws)


def _o_merge(wb):
    ws = wb["데이터"]
    merged = {str(r) for r in ws.merged_cells.ranges}
    if "A1:C1" not in merged:
        return f"A1:C1 병합 없음 (병합: {sorted(merged) or '없음'})"
    extra = merged - {"A1:C1"}
    if extra:
        return f"지정 밖 병합: {sorted(extra)}"
    if str(ws["A1"].value or "") != "월간 보고서":
        return f"A1={ws['A1'].value!r} (제목 훼손)"
    if ws["A3"].value != "지역" or ws["B4"].value != 120:
        return "표 영역 훼손"
    return ""


def _o_clear_range(wb):
    ws = wb["데이터"]
    for ref in ("B2", "B3", "B4"):
        if ws[ref].value not in (None, ""):
            return f"{ref}={ws[ref].value!r} (안 지워짐)"
    if ws["B5"].value != 110 or ws["B6"].value != 60:
        return f"B5/B6={ws['B5'].value!r}/{ws['B6'].value!r} (지정 밖 삭제)"
    if ws["B1"].value != "주문건수":
        return f"머리글 훼손: {ws['B1'].value!r}"
    return _grid_intact(ws, skip={"B2", "B3", "B4"})


def _o_ordinal_fill(wb):
    ws = wb["데이터"]
    for i in range(2, 7):
        if not _has_fill(ws.cell(row=i, column=2)):
            return f"B{i} 채움 없음"
    for col in (1, 3):
        for i in range(2, 7):
            if _has_fill(ws.cell(row=i, column=col)):
                return f"{chr(64 + col)}{i} 채움 (지정 밖 열)"
    return _grid_intact(ws)


LOCATION_TASKS = {
    "loc_cell_far": {
        "desc": "선택과 떨어진 셀 지정 쓰기", "canonical": "F9에 완료라고 써줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_cell_far,
    },
    "loc_cell_two_refs": {
        "desc": "두 좌표 언급 중 지정 좌표에만", "canonical": "C9 말고 B9에 점검이라고 써줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_two_refs,
    },
    "loc_range_fill": {
        "desc": "명시 범위만 채움", "canonical": "B2:D4만 노란색으로 칠해줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_range_fill,
    },
    "loc_row_delete": {
        "desc": "번호로 지정한 행 삭제", "canonical": "3행 삭제해줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_row_delete,
    },
    "loc_col_format": {
        "desc": "문자로 지정한 열 서식", "canonical": "D열 천 단위 콤마 서식으로 해줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_col_format,
    },
    "loc_col_header_bold": {
        "desc": "머리글 이름으로 지정한 열", "canonical": "매출 열 굵게 해줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_col_header_bold,
    },
    "loc_sheet_write": {
        "desc": "시트 한정 쓰기(비활성 시트)", "canonical": "요약 시트 B1에 점검완료라고 써줘",
        "ctx": "A1:E6", "seed": _seed_two_sheets, "oracle": _o_sheet_write,
    },
    "loc_merge_range": {
        "desc": "명시 범위 병합", "canonical": "A1:C1 병합해줘",
        "ctx": None, "seed": _seed_titled, "oracle": _o_merge,
    },
    "loc_clear_range": {
        "desc": "명시 범위만 비움", "canonical": "B2:B4 값 지워줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_clear_range,
    },
    "loc_ordinal_fill": {
        "desc": "서수로 지정한 열 채움", "canonical": "두 번째 열 노란색으로 칠해줘",
        "ctx": "A1:E6", "seed": _seed_grid, "oracle": _o_ordinal_fill,
    },
}


if __name__ == "__main__":
    from office_claw_sidecar.services import decision_trace as _dt

    bg.TASKS = dict(LOCATION_TASKS)
    _dt.source(kind="script", name="location_gate").__enter__()
    sys.argv = [sys.argv[0]] + (sys.argv[1:] or ["../../datasets/eval/location_targeting_v1.jsonl"])
    asyncio.run(bg.main())
