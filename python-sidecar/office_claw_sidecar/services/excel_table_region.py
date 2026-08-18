"""문맥 범위를 그 범위가 속한 표 전체로 넓힌다 — Excel의 CurrentRegion에 가깝다.

왜 필요한가 (2026-08-19 GUI 충실 러너 실측):
    프론트는 직전 명령의 **결과 주소**를 context_range로 보낸다. 머리글을 칠한 뒤
    "합계를 표 아래에 한 줄로"라고 하면 문맥이 머리글 한 줄(A9:F9)이라 집계 훅이
    합칠 숫자를 못 보고 빈 계획을 내 플래너로 샜다. 사람이 "표 아래"라고 했으면
    그 줄이 속한 표 전체가 대상이다.

설계:
    - 순수 함수. 사용 범위 값(2차원)과 그 좌표만 받는다 — 엔진 무관, Excel 없이 테스트.
    - 제목 줄("섹터별 투자 비중" 한 칸)은 표가 아니다. 위아래로 넓힐 때 그 줄에
      **표 너비의 절반 이상**(최소 2칸) 값이 있어야 이어진 표로 본다. 좌우도 같다.
"""

from __future__ import annotations

from typing import Any

Rect = tuple[int, int, int, int]  # (r1, c1, r2, c2), 1-based, 양끝 포함


def _filled(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _row_fill(values: list[list[Any]], used: Rect, row: int, c1: int, c2: int) -> int:
    ur1, uc1, ur2, _uc2 = used
    if row < ur1 or row > ur2:
        return 0
    line = values[row - ur1] if row - ur1 < len(values) else []
    count = 0
    for col in range(c1, c2 + 1):
        idx = col - uc1
        if 0 <= idx < len(line) and _filled(line[idx]):
            count += 1
    return count


def _col_fill(values: list[list[Any]], used: Rect, col: int, r1: int, r2: int) -> int:
    ur1, uc1, _ur2, uc2 = used
    if col < uc1 or col > uc2:
        return 0
    idx = col - uc1
    count = 0
    for row in range(r1, r2 + 1):
        line = values[row - ur1] if 0 <= row - ur1 < len(values) else []
        if 0 <= idx < len(line) and _filled(line[idx]):
            count += 1
    return count


def expand_to_table_region(rect: Rect, used: Rect, values: list[list[Any]]) -> Rect:
    """`rect`가 속한 이어진 표로 넓힌 사각형을 돌려준다. 넓힐 게 없으면 그대로."""
    r1, c1, r2, c2 = rect
    ur1, uc1, ur2, uc2 = used
    changed = True
    guard = 0
    while changed and guard < 64:
        guard += 1
        changed = False
        width = c2 - c1 + 1
        need_w = max(2, (width + 1) // 2) if width >= 2 else 1
        while r1 - 1 >= ur1 and _row_fill(values, used, r1 - 1, c1, c2) >= need_w:
            r1 -= 1
            changed = True
        while r2 + 1 <= ur2 and _row_fill(values, used, r2 + 1, c1, c2) >= need_w:
            r2 += 1
            changed = True
        height = r2 - r1 + 1
        need_h = max(2, (height + 1) // 2) if height >= 2 else 1
        while c1 - 1 >= uc1 and _col_fill(values, used, c1 - 1, r1, r2) >= need_h:
            c1 -= 1
            changed = True
        while c2 + 1 <= uc2 and _col_fill(values, used, c2 + 1, r1, r2) >= need_h:
            c2 += 1
            changed = True
    return (r1, c1, r2, c2)
