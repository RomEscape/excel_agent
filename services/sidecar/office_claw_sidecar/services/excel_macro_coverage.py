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
# 값을 실제로 바꾸는 문장. 서식(배경색·굵게)은 값을 지우지 않으므로 뺀다.
_WRITES_VALUES = re.compile(r"(입력|기입|써|쓰|넣|적어|채워|수식|적용|write|set)", re.IGNORECASE)
# 병합은 좌상단만 남기고 나머지 값을 **없앤다.** 서식 중 유일하게 파괴적이다.
_MERGES = re.compile(r"병합", re.IGNORECASE)

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


def rect_to_ref(r1: int, c1: int, r2: int, c2: int) -> str:
    """(행, 열) 사각형을 A1 표기로. 한 칸이면 `A1`, 아니면 `A1:C3`."""
    if r1 == r2 and c1 == c2:
        return f"{idx_to_col(c1)}{r1}"
    return f"{idx_to_col(c1)}{r1}:{idx_to_col(c2)}{r2}"


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
        # 계획을 실행하기 **전부터** 데이터가 있던 자리. 여기를 덮어쓰면 원본이 사라진다.
        # `_filled`는 계획이 채운 것까지 섞이므로 따로 들고 있어야 한다.
        self._preexisting: dict[str, Rect] = {}
        for sheet in (digest or {}).get("sheets") or []:
            name = str((sheet or {}).get("name") or "").strip().lower()
            rect = parse_rect(str((sheet or {}).get("used_range") or ""))
            if name and rect:
                self._filled.setdefault(name, []).append(rect)
                self._known.add(name)
                self._preexisting[name] = rect

    def _key(self, command: str, fallback: str = "") -> str:
        explicit = sheet_in_command(command)
        if explicit:
            return explicit.strip().lower()
        # "제품_리포트 A3:A7에 …"처럼 '시트'라는 낱말 없이 시트명으로 시작하는 문장.
        # 2026-08-17 실측: 이 형태가 활성 시트로 오귀속돼, 계획 안에서 새로 만든
        # 시트에 쓰는 단계가 "기존 데이터를 덮어씁니다" 오탐 경고를 5건 받았다.
        head = str(command or "").strip().split()
        if head:
            first = head[0].strip().strip("'\"").lower()
            if first and (
                first in self._known or first in self._preexisting or first in self._filled
            ):
                return first
        return str(fallback or "").strip().lower()

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

    def check_overwrite(
        self, command: str, parsed: dict[str, Any] | None, *, fallback_sheet: str = ""
    ) -> list[str]:
        """원래 있던 데이터를 덮어쓰거나 병합으로 없애는 단계를 잡는다.

        2026-08-16 실측: 물류 통합문서(사용범위 A1:M201)에 분해가 낸 1단계가
        `배송_데이터 시트 A1:M201 병합해줘`였다. 201행 전체가 한 칸으로 합쳐져 원본이
        통째로 사라졌고, 그 뒤 단계는 `MergedCell ... read-only`로 죽었다.
        few-shot 예시가 J·K열이 비어 있는 워크북 기준이라, 이미 데이터가 있는 J~M에도
        그대로 베껴 쓴 것이다.

        읽기 커버리지(check)는 이걸 못 잡는다 — 그쪽은 "참조하는 칸이 채워졌나"만 본다.
        """
        text = str(command or "")
        key = self._key(text, fallback_sheet)
        original = self._preexisting.get(key)
        if not original:
            return []

        merges = bool(_MERGES.search(text))
        if not merges and not _WRITES_VALUES.search(text):
            return []  # 배경색·굵게 같은 순수 서식은 값을 지우지 않는다.

        ref = target_range_in_command(text) or str((parsed or {}).get("range_ref") or "")
        rect = parse_rect(ref)
        if not rect:
            return []
        r1, c1, r2, c2 = rect
        o1, oc1, o2, oc2 = original
        # 겹치는 칸이 있는가
        rows = min(r2, o2) - max(r1, o1) + 1
        cols = min(c2, oc2) - max(c1, oc1) + 1
        if rows <= 0 or cols <= 0:
            return []
        overlap = rows * cols
        # 한 칸만 겹치는 건 제목을 다시 쓰는 경우가 많아 굳이 막지 않는다.
        if overlap <= 1 and not merges:
            return []
        where = rect_to_ref(max(r1, o1), max(c1, oc1), min(r2, o2), min(c2, oc2))
        if merges:
            return [f"{where}를 병합하면 그 안의 기존 데이터 {overlap}칸이 사라집니다."]
        return [f"{where}의 기존 데이터 {overlap}칸을 덮어씁니다."]

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
