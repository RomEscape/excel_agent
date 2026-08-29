"""시트의 **보이는 상태**를 요약한다 — 수식·서식·병합·블록.

다이제스트는 지금까지 값만 보여 줬다. 그래서 모델은 자기가 방금 칠한 제목 바가 무슨
색인지, 어느 열이 수식인지, 테두리가 어디 걸렸는지 알 수 없었다. 대화 기억에 의존하는
수밖에 없는데 그건 대화가 길어지면 무너진다(2026-08-16 실측: 남색으로 칠한 A1:C1과
21칸 경계선이 다이제스트에 한 글자도 안 나왔다).

여러 턴에 걸쳐 **다듬는** 작업을 하려면 매 턴 파일에서 다시 읽어야 한다. 이 모듈이 그
읽은 결과를 프롬프트에 넣을 만한 크기로 압축한다.

핵심은 **칸을 사각형으로 접는 것**이다. 굵게 4칸을 낱개로 적으면 금방 프롬프트가 터지고
사람도 못 읽는다. `A7:C7` 한 줄이면 충분하다.

openpyxl 워크시트를 받는 순수 함수 모듈이며, 파일을 여는 일은 호출자(파일 서비스)가 한다.
"""

from __future__ import annotations

from typing import Any

# 프롬프트가 터지지 않게 하는 상한. 넘으면 앞쪽만 적고 "…"로 끊는다.
_MAX_RECTS = 6
_MAX_SCAN_ROWS = 200
_MAX_SCAN_COLS = 40


def idx_to_col(index: int) -> str:
    out = ""
    n = max(1, int(index))
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _ref(r1: int, c1: int, r2: int, c2: int) -> str:
    if r1 == r2 and c1 == c2:
        return f"{idx_to_col(c1)}{r1}"
    return f"{idx_to_col(c1)}{r1}:{idx_to_col(c2)}{r2}"


def compress_cells(cells: set[tuple[int, int]]) -> list[str]:
    """(행, 열) 집합을 사각형 A1 표기 목록으로 접는다.

    행마다 이어진 열 구간을 만들고, 위아래로 같은 구간이 이어지면 하나로 합친다.
    표 블록에 걸린 서식은 대개 이걸로 한두 개 사각형이 된다.
    """
    if not cells:
        return []
    runs: list[tuple[int, int, int]] = []  # (row, c1, c2)
    for row in sorted({r for r, _ in cells}):
        cols = sorted(c for r, c in cells if r == row)
        start = prev = cols[0]
        for col in cols[1:]:
            if col == prev + 1:
                prev = col
                continue
            runs.append((row, start, prev))
            start = prev = col
        runs.append((row, start, prev))

    rects: list[list[int]] = []  # [r1, c1, r2, c2]
    for row, c1, c2 in runs:
        for rect in rects:
            if rect[1] == c1 and rect[3] == c2 and rect[2] == row - 1:
                rect[2] = row
                break
        else:
            rects.append([row, c1, row, c2])
    return [_ref(r1, c1, r2, c2) for r1, c1, r2, c2 in rects]


def _rgb(color: Any) -> str:
    """openpyxl 색 객체에서 RRGGBB만 뽑는다. 테마색·자동색은 빈 문자열."""
    value = getattr(color, "rgb", None)
    if not isinstance(value, str) or len(value) < 6:
        return ""
    hexed = value[-6:].upper()
    return "" if hexed in {"000000"} and getattr(color, "type", "") == "theme" else hexed


def describe_worksheet(ws: Any, *, max_rows: int = _MAX_SCAN_ROWS) -> dict[str, Any]:
    """워크시트 하나의 수식·서식·블록을 요약한다."""
    out: dict[str, Any] = {
        "formulas": [], "bold": [], "filled": [], "bordered": [],
        "number_formats": [], "merged": [], "conditional_formats": [],
        "charts": len(getattr(ws, "_charts", [])),
        "freeze_panes": str(getattr(ws, "freeze_panes", "") or ""),
        "blocks": [],
    }

    formulas: set[tuple[int, int]] = set()
    bold: set[tuple[int, int]] = set()
    bordered: set[tuple[int, int]] = set()
    filled: dict[str, set[tuple[int, int]]] = {}
    numfmt: dict[str, set[tuple[int, int]]] = {}
    occupied: set[tuple[int, int]] = set()

    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, max_rows),
                            max_col=min(ws.max_column or 1, _MAX_SCAN_COLS)):
        for cell in row:
            pos = (cell.row, cell.column)
            value = cell.value
            if value is not None and str(value).strip() != "":
                occupied.add(pos)
                if isinstance(value, str) and value.startswith("="):
                    formulas.add(pos)
            font = cell.font
            if font is not None and font.bold:
                bold.add(pos)
            fill = cell.fill
            if fill is not None and fill.patternType:
                code = _rgb(getattr(fill, "fgColor", None))
                if code and code != "FFFFFF":
                    filled.setdefault(code, set()).add(pos)
            border = cell.border
            if border is not None and any(
                getattr(getattr(border, side, None), "style", None) for side in ("left", "right", "top", "bottom")
            ):
                bordered.add(pos)
            fmt = str(cell.number_format or "")
            if fmt and fmt not in {"General", "@"}:
                numfmt.setdefault(fmt, set()).add(pos)

    out["formulas"] = compress_cells(formulas)[:_MAX_RECTS]
    out["bold"] = compress_cells(bold)[:_MAX_RECTS]
    out["bordered"] = compress_cells(bordered)[:_MAX_RECTS]
    out["filled"] = [
        {"range": ref, "color": f"#{code}"}
        for code, cells in sorted(filled.items())
        for ref in compress_cells(cells)[:2]
    ][:_MAX_RECTS]
    out["number_formats"] = [
        {"range": ref, "format": fmt}
        for fmt, cells in sorted(numfmt.items())
        for ref in compress_cells(cells)[:2]
    ][:_MAX_RECTS]
    out["merged"] = [str(r) for r in list(ws.merged_cells.ranges)[:_MAX_RECTS]]
    try:
        out["conditional_formats"] = [str(rng.sqref) for rng in list(ws.conditional_formatting)[:_MAX_RECTS]]
    except Exception:
        out["conditional_formats"] = []
    out["blocks"] = _find_blocks(occupied)[:_MAX_RECTS]
    return out


def _find_blocks(occupied: set[tuple[int, int]]) -> list[str]:
    """빈 행으로 갈라지는 표 블록들. 대시보드처럼 표가 여럿인 시트를 위해서다.

    다이제스트는 "1행=머리글, 그 아래가 표"라는 평평한 가정을 한다. 그래서 KPI 위에
    지역별 표가 따로 있는 시트는 아래쪽 표가 통째로 안 보인다.
    """
    if not occupied:
        return []
    rows = sorted({r for r, _ in occupied})
    groups: list[list[int]] = [[rows[0]]]
    for row in rows[1:]:
        if row == groups[-1][-1] + 1:
            groups[-1].append(row)
        else:
            groups.append([row])
    blocks: list[str] = []
    for group in groups:
        cells = [(r, c) for r, c in occupied if r in group]
        r1, r2 = min(group), max(group)
        c1 = min(c for _, c in cells)
        c2 = max(c for _, c in cells)
        blocks.append(_ref(r1, c1, r2, c2))
    return blocks


def render_layout(layout: dict[str, Any], *, indent: str = "  ") -> list[str]:
    """다이제스트 텍스트에 이어 붙일 줄들. 비어 있으면 아무 줄도 내지 않는다."""
    if not layout:
        return []
    lines: list[str] = []

    def add(label: str, refs: list[str]) -> None:
        if refs:
            lines.append(f"{indent}{label}: {', '.join(refs)}")

    add("표 블록", layout.get("blocks") or [])
    add("수식", layout.get("formulas") or [])
    add("병합", layout.get("merged") or [])
    add("굵게", layout.get("bold") or [])
    add("테두리", layout.get("bordered") or [])
    fills = layout.get("filled") or []
    if fills:
        add("배경색", [f"{f['range']}={f['color']}" for f in fills])
    fmts = layout.get("number_formats") or []
    if fmts:
        add("숫자서식", [f"{f['range']}={f['format']}" for f in fmts])
    add("조건부서식", layout.get("conditional_formats") or [])
    if layout.get("charts"):
        lines.append(f"{indent}차트: {layout['charts']}개")
    if layout.get("freeze_panes"):
        lines.append(f"{indent}틀고정: {layout['freeze_panes']}")
    return lines
