"""계획 위생 검사 — **실행 전에** 계획 자체가 이상한지 본다.

왜 사후조건으로는 부족한가 (2026-08-19 결과 워크북 감사에서 배운 것):
    `excel_result_verifier`는 "계획이 말한 값이 셀에 들어갔는가"를 본다. 그래서 계획이
    "E35에 'B35 빼기 B36 한 값'이라는 **글자**를 써라"였을 때, 그 글자가 들어간 것을 확인하고
    통과시켰다. **사후조건은 말이 틀린 경우를 원리적으로 못 잡는다.**

여기서 보는 건 워크북이 아니라 **계획과 원문의 관계**다. 의미를 이해할 필요가 없다:
    - 셀에 쓰려는 값이 원문의 조각이고 그 안에 셀 주소·연산어가 있으면 → 그건 데이터가 아니라 지시문이다
    - 값이 조사 한 글자면 → 값이 아니다
    - 원문이 "<셀>에 … <시트> …" 순서인데 계획이 그 시트에 쓰면 → 그 시트는 원본이다

말투·어순·오타와 무관하게 성립하는 검사라, 파서를 새로 뚫는 문형이 나와도 같은 보호가 남는다.
판정된 계획은 **실행 대신 해석 카드/되묻기**로 내려간다(확신 3분기에 합류, 새 출구를 만들지 않는다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 값 안에 이런 낱말이 셀 주소와 함께 있으면 데이터가 아니라 계산 지시문이다.
_OPERATION_WORD = re.compile(
    r"(나누기|빼기|더하기|곱하기|뺀|나눈|더한|곱한|차이|합계|총합계|총합|총계|평균|소계|개수|비율|퍼센트|수식)"
)
_CELL_REF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}(?![A-Za-z0-9])")
_SHEET_WORD = re.compile(r"(?:시트|탭|sheet)", re.IGNORECASE)
_AGGREGATE_WORD = re.compile(
    r"(합계|총합계|총합|총계|평균|개수|건수|소계|합|더한|더해"
    r"|total|sum|average|avg|count)",
    re.IGNORECASE,
)
# 조사·군말만 남은 값.
_PARTICLE_ONLY = re.compile(r"(?:을|를|은|는|이|가|의|로|으로|도|만|랑|과|와|에|에다|다|요|좀|것|거)")
# 자리·동작을 가리키는 조각. 값으로 들어가면 지시문이 데이터로 박힌 것이다.
_DIRECTIVE_FRAGMENT = re.compile(
    r"^(?:(?:이|그|저|요)\s*(?:표|범위|영역|칸|셀|줄|행)\s*(?:아래|밑|옆|위|뒤)"
    r"|여기에?|여기다|요기에?|이거|이걸|이것"
    r"|(?:합계|평균|총합|총계|소계)\s*(?:줄|행|한\s*줄)"
    r"|(?:굵게|테두리|콤마|천\s*단위|틀\s*고정|병합))"
)

# 사람은 사정을 먼저 말하고 명령을 뒤에 한다("…필요하다고 해서요, A1에 …").
# 이 꼬리로 끝나는 조각은 데이터가 아니다(2026-08-20 게이트4 clear_table:
# A1에 '새로 데이터를 받아서 다시 넣어야 해서요'가 그대로 박혔다).
_PREAMBLE_TAIL = re.compile(
    r"(?:해서요|하셔서요|해서|하셔서|때문에|필요하다고|좋겠다고|하라고|라고\s*해서|"
    r"싶어서요|싶어서|해야\s*해서요|해야\s*해서|드려요)\s*[,·]?\s*$"
)

_WRITE_ACTIONS = frozenset({"excel_live.write_range"})
#: 쓸 칸이 수식 범위 안에 들어가면 순환 참조다.
_FORMULA_ACTIONS = frozenset({"excel_live.set_formula"})


@dataclass
class SanityIssue:
    """계획에서 발견한 위생 문제 하나."""

    code: str
    action: str
    detail: str
    #  block  = 실행하면 안 된다(되묻기)
    #  confirm = 사람이 보고 정하게 한다(해석 카드)
    severity: str = "block"

    def describe(self) -> str:
        return f"{self.code}: {self.detail}"


def _cells_of(values: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(values, list):
        return out
    for row in values:
        cells = row if isinstance(row, list) else [row]
        for cell in cells:
            if cell is None:
                continue
            text = str(cell).strip()
            if text:
                out.append(text)
    return out


def _looks_like_command_value(value: str, *, single_cell: bool = True) -> str:
    """이 값이 데이터가 아니라 지시문으로 보이면 이유를 돌려준다.

    `single_cell`이 거짓이면 조사-한-글자 검사를 건너뛴다 — 여러 칸에 나열해 쓰는 계획에서는
    '가'·'다' 같은 한 글자가 조사가 아니라 값이다(2026-08-20 `A2:C2에 가,나,다 입력` 오탐).
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text.startswith("="):
        return ""
    if single_cell and _PARTICLE_ONLY.fullmatch(text):
        return "조사·군말 한 조각이 값으로 들어갔습니다"
    if _CELL_REF.search(text) and _OPERATION_WORD.search(text):
        return "셀 주소와 계산 낱말이 함께 든 문장은 값이 아니라 수식 요청입니다"
    if _SHEET_WORD.search(text) and _AGGREGATE_WORD.search(text):
        return "시트 이름과 집계 낱말이 함께 든 문장은 값이 아니라 수식 요청입니다"
    if _DIRECTIVE_FRAGMENT.match(text):
        return "자리·서식을 가리키는 말이 값으로 들어갔습니다"
    if _PREAMBLE_TAIL.search(text):
        return "사정을 설명하는 머리말이 값으로 들어갔습니다"
    return ""


def _destination_before_sheet(message: str) -> str:
    """"<셀>에 … <이름> 시트 …" 순서면 그 시트 이름을 돌려준다(= 그 시트는 원본이다)."""
    text = str(message or "")
    dest = re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*(?:셀|칸)?\s*에(?:다가?|는)?(?![A-Za-z0-9])", text)
    if dest is None:
        return ""
    mention = re.search(r"([^\s,]+)\s*(?:시트|탭|sheet)", text[dest.end() :], re.IGNORECASE)
    if mention is None:
        return ""
    return str(mention.group(1)).strip().strip("'\"")


def _cell_index(ref: str) -> tuple[int, int] | None:
    """"B7" → (열 7? 아니라 (행, 열)). 못 읽으면 None."""
    m = re.fullmatch(r"([A-Za-z]{1,3})(\d{1,7})", str(ref or "").strip())
    if not m:
        return None
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def _circular_formula_issues(action: str, params: dict[str, Any]) -> list[SanityIssue]:
    """쓸 칸이 수식이 참조하는 범위 안에 있으면 순환이다.

    `요약!A2 = =SUM(A2:A2)` — 엑셀은 0을 보여 주고, 사후조건은 "수식이 들어갔다"고 통과시킨다
    (2026-08-20 게이트4 cross_sheet_sum).
    """
    target = _cell_index(str(params.get("range_ref") or params.get("target_range") or ""))
    formula = str(params.get("formula_a1") or params.get("formula") or "")
    if target is None or not formula:
        return []
    for m in re.finditer(
        r"(?<![A-Za-z0-9_!])([A-Za-z]{1,3}\d{1,7})\s*:\s*([A-Za-z]{1,3}\d{1,7})", formula
    ):
        # 시트를 넘어 참조하면(`지역성과!B2:B6`) 순환이 아니다.
        if formula[: m.start()].rstrip().endswith("!"):
            continue
        start, end = _cell_index(m.group(1)), _cell_index(m.group(2))
        if start is None or end is None:
            continue
        row_lo, row_hi = sorted((start[0], end[0]))
        col_lo, col_hi = sorted((start[1], end[1]))
        if row_lo <= target[0] <= row_hi and col_lo <= target[1] <= col_hi:
            return [
                SanityIssue(
                    code="formula_refers_to_itself",
                    action=action,
                    detail=(
                        f"수식 `{formula[:40]}`이 쓸 칸 자신을 참조합니다(순환)"
                    ),
                )
            ]
    return []


def check_plan_sanity(
    steps: list[dict[str, Any]],
    *,
    message: str,
    active_sheet: str = "",
) -> list[SanityIssue]:
    """계획이 원문과 앞뒤가 맞는지 본다. 워크북은 읽지 않는다(순수 함수)."""
    issues: list[SanityIssue] = []
    source_sheet = _destination_before_sheet(message)

    for step in steps or []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        params = dict(step.get("params") or {})

        # S1~S4 — 쓰려는 값이 실은 지시문인가.
        if action in _WRITE_ACTIONS:
            written = _cells_of(params.get("values_2d"))
            for value in written:
                why = _looks_like_command_value(value, single_cell=len(written) == 1)
                if not why:
                    continue
                # 원문에 그대로 있는 조각일 때만 — 사용자가 진짜로 그 글자를 넣으려는 경우와 가른다.
                compact_value = re.sub(r"\s+", "", value)
                compact_message = re.sub(r"\s+", "", str(message or ""))
                if compact_value and compact_value in compact_message:
                    issues.append(
                        SanityIssue(
                            code="value_is_a_directive",
                            action=action,
                            detail=f"{why} — '{value[:40]}'",
                        )
                    )
                    break

        # S7 — 쓸 칸이 수식 범위 안에 있는가(순환 참조).
        if action in _FORMULA_ACTIONS:
            issues.extend(_circular_formula_issues(action, params))

        # S5 — 대상 시트가 사실은 원본인가.
        target_sheet = str(params.get("sheet_name") or "").strip()
        if (
            source_sheet
            and target_sheet
            and active_sheet
            and target_sheet.replace(" ", "").casefold() != str(active_sheet).replace(" ", "").casefold()
            and source_sheet.replace(" ", "").casefold().endswith(target_sheet.replace(" ", "").casefold())
        ):
            issues.append(
                SanityIssue(
                    code="writes_to_the_source_sheet",
                    action=action,
                    detail=(
                        f"원문은 '{active_sheet}'의 칸을 먼저 지목했는데 계획은 원본 시트 '{target_sheet}'에 씁니다"
                    ),
                )
            )
    return issues


def worst_severity(issues: list[SanityIssue]) -> str:
    return "block" if any(i.severity == "block" for i in issues) else ("confirm" if issues else "")
