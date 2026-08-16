"""매크로 계획의 참조 커버리지 — 실행 전에 "빈 셀을 참조하는 수식"을 잡는다.

`validate_macro_steps`는 시트 입도까지만 본다("그 시트가 통합문서에 있나"). 그래서
**계획 안에서 셀이 채워지는지**는 아무도 확인하지 않는다.

2026-08-16 실측: 분해가 few-shot에 있던 `"Dashboard 시트 A6:A11에 서울,경기,… 입력"`
단계를 빠뜨린 채 `"Dashboard 시트 B6:B11에 수식 =SUMIF(...,A6,...) 적용"`을 냈다.
기준 셀이 비어 SUMIF 12칸이 전부 0이 됐는데, 실행은 전부 성공이라 `19/19`로 보고됐다.

이 모듈은 계획을 순서대로 훑으며 시트별 "채워진 사각형" 목록을 쌓고, 각 수식 단계가
읽는 셀이 그 안에 드는지 본다. 못 드는 셀이 있으면 경고 문구를 돌려주고, 라우터는
그걸 승인 화면에 띄운다. 실행 자체를 막지는 않는다 — 오탐이 사용자를 막아 세우는 쪽이
더 나쁘기 때문이다.

상태를 갖지 않는 순수 계산이며, `CoverageTracker` 한 객체가 한 계획의 진행 상태를 소유한다.
"""

from __future__ import annotations

import re
from typing import Any

# (r1, c1, r2, c2) — 1-based 닫힌 구간
Rect = tuple[int, int, int, int]

_SHEET_IN_TEXT = re.compile(r"([A-Za-z0-9_가-힣]+)\s*시트")
# 문장에 실린 대상 범위. `A1` 또는 `A1:C10`. 수식 안의 참조와 섞이지 않게 `!`·`$` 뒤는 제외.
_RANGE_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9$!:])([A-Z]{1,3}\d{1,7}(?::[A-Z]{1,3}\d{1,7})?)(?![\d(])"
)
# `Sales_Data!$C$2:$C$61` — 다른 시트를 본다. 이 시트의 기준 셀 후보에서 먼저 걷어낸다.
_SHEET_QUALIFIED_REF = re.compile(
    r"(?:'[^']+'|[A-Za-z0-9_가-힣]+)!\$?[A-Z]{1,3}\$?\d{1,7}(?::\$?[A-Z]{1,3}\$?\d{1,7})?"
)
# 시트 접두사 없는 단일 셀 참조. 범위(A1:B2)와 함수명(LOG10()) 은 뺀다.
_LOCAL_CELL_REF = re.compile(r"(?<![A-Za-z0-9_$:!])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![\d:(])")
_FORMULA_IN_TEXT = re.compile(r"=\s*[A-Za-z(\$]")
_CREATES_SHEET = re.compile(r"시트\s*(?:를\s*)?(?:만들|생성|추가)")

# 결과 표를 통째로 쓰는 액션. 크기를 미리 알 수 없으므로 시트 전체를 '채워짐'으로 둔다.
_WHOLE_SHEET_WRITERS = frozenset(
    {
        "excel_live.pivot_table",
        "excel_live.group_by_aggregate",
        "excel_live.consolidate_sheets",
        "excel_live.consolidate_workbooks_from_folder",
        "excel_live.forecast_linear",
        "excel_live.compare_ranges",
        "excel_live.create_table",
    }
)

# 규칙 파서는 "지역별 매출 집계표 만들어줘"를 None으로 돌려준다(실측 확인). 액션만 보면
# 결과 표 단계를 놓쳐, 그 시트를 '비었다'로 둔 채 뒤 수식에 헛경고를 낸다. 문장으로도 잡는다.
_WHOLE_SHEET_TEXT = re.compile(
    r"(집계표|집계해|피벗|pivot|요약표|통합해|consolidat|예측)", re.IGNORECASE
)


def col_to_idx(letters: str) -> int:
    idx = 0
    for ch in str(letters or "").upper():
        if not ch.isalpha():
            break
        idx = idx * 26 + (ord(ch) - 64)
    return idx


def idx_to_col(index: int) -> str:
    out = ""
    n = max(1, int(index))
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse_rect(ref: str) -> Rect | None:
    """`B6:B11` / `A1` → (r1, c1, r2, c2). A1 표기가 아니면 None."""
    text = str(ref or "").replace("$", "").strip().upper()
    if not text:
        return None
    left, _, right = text.partition(":")
    m1 = re.fullmatch(r"([A-Z]{1,3})(\d{1,7})", left)
    if not m1:
        return None
    m2 = re.fullmatch(r"([A-Z]{1,3})(\d{1,7})", right) if right else m1
    if not m2:
        return None
    r1, c1 = int(m1.group(2)), col_to_idx(m1.group(1))
    r2, c2 = int(m2.group(2)), col_to_idx(m2.group(1))
    return (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))


def _covers(rects: list[Rect], row: int, col: int) -> bool:
    return any(r1 <= row <= r2 and c1 <= col <= c2 for r1, c1, r2, c2 in rects)


def sheet_in_command(command: str) -> str:
    """문장이 지목한 시트 이름. 없으면 빈 문자열."""
    m = _SHEET_IN_TEXT.search(str(command or ""))
    return m.group(1).strip().strip("'\"") if m else ""


def target_range_in_command(command: str) -> str:
    """문장이 지목한 대상 범위. 수식 부분은 보지 않는다."""
    text = str(command or "")
    head = text.split("=", 1)[0] if _FORMULA_IN_TEXT.search(text) else text
    hits = _RANGE_IN_TEXT.findall(head)
    return hits[0] if hits else ""


def criteria_cells(formula: str, target: Rect) -> list[tuple[int, int]]:
    """수식이 기준으로 삼는 **이 시트의** 셀들을 대상 범위만큼 펼친다.

    `B6:B11`에 `=SUMIF(...,A6,...)`를 넣으면 A6은 A6..A11로 흘러내린다. `$`로 고정된
    축은 펼치지 않는다. 이 전개가 없으면 A6 한 칸만 보고 "기준이 있다"고 오판해,
    정작 비어 있는 A7:A11을 놓친다.
    """
    r1, c1, r2, c2 = target
    rows, cols = r2 - r1 + 1, c2 - c1 + 1
    local = _SHEET_QUALIFIED_REF.sub(" ", str(formula or ""))
    out: list[tuple[int, int]] = []
    for col_abs, letters, row_abs, row_text in _LOCAL_CELL_REF.findall(local):
        base_row, base_col = int(row_text), col_to_idx(letters)
        for dr in range(1 if row_abs else rows):
            for dc in range(1 if col_abs else cols):
                cell = (base_row + dr, base_col + dc)
                if cell not in out:
                    out.append(cell)
    return out


class CoverageTracker:
    """한 계획을 순서대로 훑으며 "어느 셀이 채워졌는가"를 쌓는다.

    셀을 낱개로 담으면 A1:Q181 한 번에 3천 개가 들어온다. 사각형 목록으로 들고
    포함 여부만 본다.
    """

    def __init__(self, digest: dict[str, Any] | None = None) -> None:
        self._filled: dict[str, list[Rect]] = {}
        self._whole: set[str] = set()
        # 내용을 **아는** 시트만 판정한다. 다이제스트가 사용 범위를 안 준 시트는
        # 비었는지 찼는지 알 수 없으므로 경고하지 않는다 — 모르는 것을 결함으로
        # 단정하면 멀쩡한 계획이 경고로 뒤덮인다(검증기와 같은 원칙).
        self._known: set[str] = set()
        for sheet in (digest or {}).get("sheets") or []:
            name = str((sheet or {}).get("name") or "").strip().lower()
            rect = parse_rect(str((sheet or {}).get("used_range") or ""))
            if name and rect:
                self._filled.setdefault(name, []).append(rect)
                self._known.add(name)

    def _key(self, command: str, fallback: str = "") -> str:
        return (sheet_in_command(command) or fallback).strip().lower()

    def record(self, command: str, parsed: dict[str, Any] | None, *, fallback_sheet: str = "") -> None:
        """이 단계가 채우는 범위를 쌓는다."""
        key = self._key(command, fallback_sheet)
        if not key:
            return
        action = str((parsed or {}).get("action") or "").strip()

        if _CREATES_SHEET.search(str(command or "")) and not action.endswith("write_range"):
            # 새 시트는 비어 있다 — 이건 **아는** 사실이므로 판정 대상에 넣는다.
            self._filled.setdefault(key, [])
            self._known.add(key)
            return
        if action in _WHOLE_SHEET_WRITERS or _WHOLE_SHEET_TEXT.search(str(command or "")):
            self._whole.add(key)
            return

        ref = target_range_in_command(command) or str((parsed or {}).get("range_ref") or "")
        rect = parse_rect(ref)
        if rect:
            self._filled.setdefault(key, []).append(rect)

    def check(self, command: str, parsed: dict[str, Any] | None, *, fallback_sheet: str = "") -> list[str]:
        """이 단계가 읽는 셀 중 앞 단계가 채우지 않은 것들을 경고 문구로 돌려준다."""
        text = str(command or "")
        if not _FORMULA_IN_TEXT.search(text):
            return []
        key = self._key(command, fallback_sheet)
        if not key or key in self._whole or key not in self._known:
            return []

        formula = str((parsed or {}).get("formula_a1") or "")
        if not formula:
            idx = text.find("=")
            formula = text[idx:] if idx >= 0 else ""
        if not formula:
            return []

        rect = parse_rect(target_range_in_command(text) or str((parsed or {}).get("range_ref") or ""))
        if not rect:
            return []

        known = self._filled.get(key) or []
        missing = [
            f"{idx_to_col(col)}{row}"
            for row, col in criteria_cells(formula, rect)
            if not _covers(known, row, col)
        ]
        if not missing:
            return []
        shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        return [f"이 수식이 참조하는 {shown}을(를) 앞 단계가 채우지 않습니다."]
