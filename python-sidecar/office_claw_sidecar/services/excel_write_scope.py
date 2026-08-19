"""쓰기의 **블라스트 반경** — 사람이 지목하지 않은 자리의 값을 덮는지 실행 전에 본다.

왜 필요한가 (2026-08-19 결과 워크북 감사):
    "B10에다 성적부 시트 결석 다 더한 값 가져와줘" 가 대시보드가 아니라 **성적부!B10**에 수식을 썼고,
    그 자리에 있던 학생 이름이 사라졌다. 파서·계획·검증 어느 층도 못 막았다 —
    사후조건은 "계획대로 썼는가"만 보므로 계획이 틀리면 통과시킨다.

이 모듈은 문장의 의미를 몰라도 된다. **"사람이 가리킨 자리"와 "실제로 건드릴 자리"를 비교**할 뿐이라
말투·어순·오타와 무관하게 같은 보호를 준다. 파싱 커버리지가 아무리 넓어져도 이 층은 계속 유효하다.

판정은 셋 중 하나:
    - 겹침 없음        → 조용히 실행해도 안전(기존 경로 그대로)
    - 빈 칸만 건드림   → 안전
    - **값 있는 칸을 지목 밖에서 덮음** → 위험. 실행 대신 확인을 받는다.

시트까지 묶어 비교하는 게 핵심이다. 위 사례는 지목이 `대시보드!B10`, 실제 쓰기가 `성적부!B10`이라
주소만 보면 같지만 시트가 달라 위험으로 잡힌다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 값을 바꾸는 액션만 본다. 서식·차트는 되돌리기 쉬워 이 가드의 대상이 아니다.
#
# find_replace는 **일부러 뺐다.** 대상이 본래 표 전체(__USED_RANGE__)라 자리표시자를 풀고 나면
# 비어 있지 않은 칸 수십 개가 전부 "지목 밖"이 되어 매번 경고가 뜬다. 치환은 원래 일괄 작업이고,
# 제대로 바뀌었는지는 사후조건(찾을 글자가 남았는가)이 본다.
DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "excel_live.write_range",
        "excel_live.set_formula",
        "excel_live.clear_range",
    }
)

# 실행기가 실행 직전에 푸는 자리표시자. 가드도 같은 값을 봐야 한다 — 안 풀면 clear_range처럼
# 자리표시자를 쓰는 액션을 통째로 놓친다(2026-08-19 적대적 검증에서 발견).
PLACEHOLDERS: frozenset[str] = frozenset({"__ACTIVE_SELECTION__", "__ACTIVE_CELL__", "__USED_RANGE__"})

_CELL = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3})(\d{1,7})(?![A-Za-z0-9])")
_RANGE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{1,3})(\d{1,7})\s*:\s*([A-Za-z]{1,3})(\d{1,7})(?![A-Za-z0-9])"
)
_COLUMN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3})\s*(?:열|컬럼|column)", re.IGNORECASE)
# "표 전체", "시트 전체"처럼 범위를 통째로 가리키는 말 — 이때는 반경을 따지지 않는다.
_WHOLE_SCOPE = re.compile(r"(전체|전부\s*(?:다|싹)?\s*(?:지우|비우|삭제)|모두\s*(?:지우|비우|삭제)|다\s*지워|싹\s*지워)")

MAX_SCOPE_CELLS = 20000
# 지목 사각형 상한 — 값 격자 문장이 셀 닮은 토큰을 수십 개 만든다.
MAX_STATED_RECTS = 64


def col_to_idx(letters: str) -> int:
    n = 0
    for ch in str(letters or "").upper():
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
    return n


def idx_to_col(index: int) -> str:
    out = ""
    n = int(index)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out or "A"


@dataclass(frozen=True)
class Rect:
    """시트 한 장 위의 직사각 영역(1-기준, 끝 포함)."""

    sheet: str
    r1: int
    c1: int
    r2: int
    c2: int

    def contains(self, sheet: str, row: int, col: int) -> bool:
        return (
            _same_sheet(self.sheet, sheet)
            and self.r1 <= row <= self.r2
            and self.c1 <= col <= self.c2
        )

    @property
    def cells(self) -> int:
        return max(0, self.r2 - self.r1 + 1) * max(0, self.c2 - self.c1 + 1)

    def ref(self) -> str:
        return f"{idx_to_col(self.c1)}{self.r1}:{idx_to_col(self.c2)}{self.r2}"


@dataclass
class RiskyCell:
    sheet: str
    address: str
    value: Any

    def describe(self) -> str:
        text = str(self.value)
        if len(text) > 24:
            text = text[:24] + "…"
        return f"{self.sheet}!{self.address}='{text}'"


@dataclass
class ScopeVerdict:
    """지목 밖에서 값을 덮는가."""

    risky: list[RiskyCell] = field(default_factory=list)
    checked: bool = True
    why: str = ""

    @property
    def is_risky(self) -> bool:
        return bool(self.risky)

    def summary(self, limit: int = 4) -> str:
        if not self.risky:
            return ""
        head = ", ".join(c.describe() for c in self.risky[:limit])
        more = f" 외 {len(self.risky) - limit}칸" if len(self.risky) > limit else ""
        return f"지목하지 않은 자리의 값 {len(self.risky)}칸을 덮습니다: {head}{more}"


def _same_sheet(a: str | None, b: str | None) -> bool:
    return str(a or "").strip().casefold() == str(b or "").strip().casefold()


def _norm_sheet(name: str | None, default: str) -> str:
    text = str(name or "").strip().strip("'\"")
    return text or default


def parse_ref(ref: str, default_sheet: str) -> Rect | None:
    """'A1:C9' · 'Sheet1!A1' · 'A1' → Rect. 열 전체('A:A')는 다루지 않는다(범위가 사실상 무한)."""
    text = str(ref or "").strip()
    if not text:
        return None
    sheet = default_sheet
    if "!" in text:
        head, _, tail = text.rpartition("!")
        sheet = _norm_sheet(head, default_sheet)
        text = tail
    m = _RANGE.search(text)
    if m:
        r1, r2 = int(m.group(2)), int(m.group(4))
        c1, c2 = col_to_idx(m.group(1)), col_to_idx(m.group(3))
        return Rect(sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))
    m = _CELL.fullmatch(text) or _CELL.search(text)
    if m:
        r, c = int(m.group(2)), col_to_idx(m.group(1))
        return Rect(sheet, r, c, r, c)
    return None


def stated_scope(
    *,
    message: str,
    context_range: str | None,
    active_sheet: str,
    extra_refs: list[str] | None = None,
) -> list[Rect]:
    """사람이 **가리킨** 자리. 문장에 적은 범위·셀·열 + 붙여넣기 선택 + 계획이 밝힌 대상.

    시트는 활성 시트로 본다 — 문장이 다른 시트를 불렀더라도 그것이 대상인지 원본인지는
    여기서 판단하지 않는다(그 판단이 틀렸던 것이 바로 막으려는 사고다).
    """
    text = str(message or "")
    rects: list[Rect] = []
    for m in _RANGE.finditer(text):
        rects.append(
            Rect(
                active_sheet,
                min(int(m.group(2)), int(m.group(4))),
                min(col_to_idx(m.group(1)), col_to_idx(m.group(3))),
                max(int(m.group(2)), int(m.group(4))),
                max(col_to_idx(m.group(1)), col_to_idx(m.group(3))),
            )
        )
    covered = {(m.start(), m.end()) for m in _RANGE.finditer(text)}
    for m in _CELL.finditer(text):
        if any(s <= m.start() < e for s, e in covered):
            continue
        r, c = int(m.group(2)), col_to_idx(m.group(1))
        rects.append(Rect(active_sheet, r, c, r, c))
    for m in _COLUMN.finditer(text):
        c = col_to_idx(m.group(1))
        if c:
            rects.append(Rect(active_sheet, 1, c, 1_048_576, c))
    for ref in list(extra_refs or []) + ([context_range] if context_range else []):
        rect = parse_ref(str(ref or ""), active_sheet)
        if rect is not None:
            rects.append(rect)
    return rects


def _step_targets(
    action: str,
    params: dict[str, Any],
    default_sheet: str,
    resolve_placeholder=None,
) -> list[Rect]:
    """계획 한 단계가 실제로 건드릴 자리."""
    sheet = _norm_sheet(params.get("sheet_name"), default_sheet)

    def _resolve(raw: str) -> str:
        text = str(raw or "").strip()
        if text in PLACEHOLDERS and resolve_placeholder is not None:
            try:
                return str(resolve_placeholder(sheet, text) or "")
            except Exception:
                return ""
        return text

    out: list[Rect] = []
    if action == "excel_live.write_range":
        start = _resolve(params.get("start_cell"))
        values = params.get("values_2d")
        base = parse_ref(start, sheet)
        if base is None:
            return []
        rows = len(values) if isinstance(values, list) and values else 1
        cols = max((len(r) for r in values if isinstance(r, list)), default=1) if isinstance(values, list) else 1
        out.append(Rect(base.sheet, base.r1, base.c1, base.r1 + max(1, rows) - 1, base.c1 + max(1, cols) - 1))
        return out
    for key in ("range_ref", "target_range", "output_range"):
        raw = params.get(key)
        if raw is None:
            continue
        rect = parse_ref(_resolve(raw), sheet)
        if rect is not None:
            out.append(rect)
            break
    return out


def write_footprint(
    steps: list[dict[str, Any]],
    default_sheet: str,
    resolve_placeholder=None,
) -> list[Rect]:
    """계획 전체가 값을 바꿀 자리. 값을 안 바꾸는 액션(서식·차트)은 세지 않는다."""
    out: list[Rect] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        if action not in DESTRUCTIVE_ACTIONS:
            continue
        out.extend(
            _step_targets(action, dict(step.get("params") or {}), default_sheet, resolve_placeholder)
        )
    return out


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def assess(
    *,
    steps: list[dict[str, Any]],
    message: str,
    context_range: str | None,
    active_sheet: str,
    read_rect,
    extra_refs: list[str] | None = None,
    resolve_placeholder=None,
) -> ScopeVerdict:
    """지목 밖에서 값을 덮는지 판정한다.

    read_rect(sheet, ref) → values_2d. 못 읽으면 판정을 포기하고 **통과**시킨다 —
    못 봤다는 이유로 멀쩡한 작업을 막으면 더 큰 손해다(기존 검증기와 같은 원칙).

    resolve_placeholder(sheet, "__ACTIVE_SELECTION__") → 실제 범위. 실행기가 실행 직전에 푸는 값이라
    가드도 같은 것을 봐야 한다 — 안 풀면 clear_range 대부분을 놓친다.
    """
    footprint = write_footprint(steps, active_sheet, resolve_placeholder)
    if not footprint:
        return ScopeVerdict(checked=False, why="값을 바꾸는 단계 없음")
    if _WHOLE_SCOPE.search(str(message or "")):
        return ScopeVerdict(checked=False, why="전체를 지목한 문장")

    stated = stated_scope(
        message=message, context_range=context_range, active_sheet=active_sheet, extra_refs=extra_refs
    )
    # 값 격자 문장은 셀 닮은 토큰을 수십 개 만든다. 지목이 지나치게 많으면 판정 비용만 커지고
    # 의미도 흐려지므로 앞쪽 것만 쓴다(지목이 많다 = 이미 관대하다).
    if len(stated) > MAX_STATED_RECTS:
        stated = stated[:MAX_STATED_RECTS]
    if not stated:
        # 아무것도 가리키지 않은 문장 — 보통은 활성 셀 쓰기라 이 가드의 대상이 아니다.
        #
        # 다만 **지우기**는 다르다. 사람이 자리를 하나도 안 짚었는데 값을 지운다면 그게 가장 의심스러운
        # 경우다(2026-08-19 블라인드 게이트: "차트 전부 지워 주세요, **데이터는 그대로 두시고요**"가
        # B2의 데이터까지 지웠고 카드 없이 실행됐다). 지우기만 남기고 나머지는 통과시킨다.
        clearing = [
            s
            for s in steps or []
            if isinstance(s, dict) and str(s.get("action") or "") == "excel_live.clear_range"
        ]
        if not clearing:
            return ScopeVerdict(checked=False, why="문장이 자리를 가리키지 않음")
        footprint = write_footprint(clearing, active_sheet, resolve_placeholder)
        if not footprint:
            return ScopeVerdict(checked=False, why="문장이 자리를 가리키지 않음")

    risky: list[RiskyCell] = []
    for rect in footprint:
        if rect.cells > MAX_SCOPE_CELLS:
            return ScopeVerdict(checked=False, why="대상이 너무 넓어 판정 생략")
        outside = [
            (r, c)
            for r in range(rect.r1, rect.r2 + 1)
            for c in range(rect.c1, rect.c2 + 1)
            if not any(s.contains(rect.sheet, r, c) for s in stated)
        ]
        if not outside:
            continue
        # 발자국 전체가 아니라 **지목 밖 칸의 바운딩 박스만** 읽는다 — 넓은 쓰기에서 왕복 비용을 줄인다.
        br1 = min(r for r, _ in outside)
        br2 = max(r for r, _ in outside)
        bc1 = min(c for _, c in outside)
        bc2 = max(c for _, c in outside)
        box = Rect(rect.sheet, br1, bc1, br2, bc2)
        try:
            values = read_rect(rect.sheet, box.ref())
        except Exception:
            return ScopeVerdict(checked=False, why="대상 범위를 읽지 못함")
        if not isinstance(values, list):
            return ScopeVerdict(checked=False, why="대상 범위를 읽지 못함")
        for r, c in outside:
            row = values[r - br1] if 0 <= r - br1 < len(values) else None
            cell = row[c - bc1] if isinstance(row, list) and 0 <= c - bc1 < len(row) else None
            if not _is_blank(cell):
                risky.append(RiskyCell(rect.sheet, f"{idx_to_col(c)}{r}", cell))
    return ScopeVerdict(risky=risky)
