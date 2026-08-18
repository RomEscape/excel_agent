"""붙여넣은·선택한 범위의 열별 집계를 바로 아랫줄에 쓴다 — 사람 말투 경로.

2026-08-18 사용자 실측: "컨트롤 c, 컨트롤 v 한 위치에 있는 모든 합을 밑에 있는
시트에 기록할 수 있게 해줘" — 좌표도, SUM이라는 낱말도, 수식도 없다. 사람은
의도를 말하지 수식을 부르지 않는다. 이 문형을 결정적으로 매핑한다:

    대상 범위(문장 범위 → context_range → 살아 있는 선택)를 읽어,
    숫자가 있는 열마다 =FUNC(열구간)을 범위 바로 아랫줄에 넣고,
    글자 열(라벨 열)이 있으면 같은 줄에 "합계" 같은 이름표를 쓴다.

판정은 순수 함수로 두고 값 읽기는 호출부(라우터)가 한다 — 엔진 없이 테스트된다.
"""

from __future__ import annotations

import re
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

# 함수 어휘. 맨 뒤의 SUM 항목은 "합쳐줘"(병합)·"통합"·"합니다"를 피하려고
# 조사·수식어가 붙은 꼴만 받는다.
_FUNC_VOCAB: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"평균", re.IGNORECASE), "AVERAGE", "평균"),
    (re.compile(r"개수|몇\s*개|카운트|count", re.IGNORECASE), "COUNT", "개수"),
    (re.compile(r"최대|최댓값", re.IGNORECASE), "MAX", "최대"),
    (re.compile(r"최소|최솟값", re.IGNORECASE), "MIN", "최소"),
    (
        # "합 좀 밑에다", "각 열 합" 같은 반말·군더더기 꼴을 포함한다
        # (2026-08-18 사람 말투 배터리 1라운드 실측: 두 꼴 다 되묻기로 샜다).
        re.compile(
            r"(?:모든|각|전체)\s*(?:열|칸|항목)?\s*합|합계|총합|총\s*합"
            r"|합(?:을|이|만|과|값|\s+좀|이나)|더한\s*값|다\s*더해",
            re.IGNORECASE,
        ),
        "SUM",
        "합계",
    ),
]
_BELOW = re.compile(r"밑|아래|하단|아랫")
_RANGE = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$")


def match_aggregate_below(message: str) -> tuple[str, str] | None:
    """(엑셀 함수, 한국어 이름표)를 돌려준다. 이 문형이 아니면 None."""
    text = str(message or "")
    if not _BELOW.search(text):
        return None
    if "=" in text:
        # 수식을 직접 쓴 문장은 기존 수식 경로가 맡는다.
        return None
    for pattern, func, label in _FUNC_VOCAB:
        if pattern.search(text):
            return func, label
    return None


_COLUMNWISE = re.compile(r"열\s*별|열별|각\s*열|칼럼\s*별|컬럼\s*별")


def match_aggregate_columns(message: str) -> tuple[str, str] | None:
    """"열 별로 합계를 만들어줘" — 아래·밑 낱말 없이 열별 집계를 말하는 문형.

    대상 줄(붙여넣은 A7:F7 등)이 따로 있으면 방향 낱말이 필요 없다.
    2026-08-18 GUI 실측: 이 문형이 플래너로 새서 pivot_table로 오분류돼
    검증 실패·재계획 실패로 끝났다.
    """
    text = str(message or "")
    if "=" in text or not _COLUMNWISE.search(text):
        return None
    for pattern, func, label in _FUNC_VOCAB:
        if pattern.search(text):
            return func, label
    return None


# "A4에 지역성과 시트 주문건수 합계를 가져와줘" — 셀·원본 시트·열 이름·집계만
# 말하는 크로스시트 문형. "E4에는 …를, F4에는 …를"처럼 한 문장에 여러 절이
# 오면 시트 이름은 앞 절에서 이어받는다.
_CROSS_SHEET = re.compile(
    r"([A-Z]+\d+)\s*에(?:는|다가)?\s*(?:([^\s,]+?)\s*시트(?:의|에서)?\s*)?"
    r"([가-힣A-Za-z0-9_]+?)\s*(?:의)?\s*(합계|총합|평균|개수|합)",
    re.IGNORECASE,
)
_CROSS_SHEET_VERB = re.compile(r"가져|끌어|연결|수식|넣어|채워|기록")
_TOTAL_ROW_LABELS = frozenset({"합계", "총계", "계", "총합", "평균", "최대", "최소", "개수", "total", "sum", "avg", "average"})


def build_cross_sheet_aggregate_plan(
    message: str,
    sheet_reader,
) -> list[dict[str, Any]]:
    """크로스시트 집계 수식 계획. sheet_reader(시트명) → (사용범위, values_2d).

    원본 시트의 머리글에서 열을 찾아 =FUNC('시트'!열구간)을 만든다. 마지막 행이
    합계 줄이면 구간에서 뺀다 — 넣으면 이중 집계다. 2026-08-18 사람 말투 실측:
    이 문형이 의도 정규화로 새서 빈 값을 쓰고 성공으로 보고됐다(가짜 성공).
    """
    text = str(message or "")
    if "시트" not in text or not _CROSS_SHEET_VERB.search(text):
        return []
    steps: list[dict[str, Any]] = []
    current_sheet = ""
    cache: dict[str, tuple[str, list[list[Any]]]] = {}
    for m in _CROSS_SHEET.finditer(text):
        cell = m.group(1).upper()
        sheet = (m.group(2) or "").strip().strip("'\"")
        header_word = m.group(3).strip()
        func_word = m.group(4)
        if sheet:
            current_sheet = sheet
        if not current_sheet:
            continue
        func = {"평균": "AVERAGE", "개수": "COUNT"}.get(func_word, "SUM")
        try:
            if current_sheet not in cache:
                cache[current_sheet] = sheet_reader(current_sheet)
            used_ref, values = cache[current_sheet]
        except Exception:
            return []
        rng = _RANGE.match(str(used_ref or "").strip().upper())
        if not rng or not values:
            continue
        headers = [str(v).strip() if v is not None else "" for v in values[0]]
        if header_word not in headers:
            continue
        col_letter = get_column_letter(
            column_index_from_string(rng.group(1)) + headers.index(header_word)
        )
        data_start = int(rng.group(2)) + 1
        data_end = int(rng.group(4))
        # 꼬리의 집계 줄(합계·평균 등 이름표 또는 수식 줄)은 **전부** 구간에서
        # 뺀다 — 한 줄만 빼면 합계+평균 두 줄일 때 이중 집계가 된다(2026-08-18
        # 대화형 러너 실측: =SUM('지역성과'!B2:B7)).
        idx = len(values) - 1
        while idx >= 1 and data_end > data_start:
            row = values[idx] or []
            label = row[0] if row else None
            has_formula = any(isinstance(v, str) and str(v).startswith("=") for v in row)
            is_agg = isinstance(label, str) and label.strip().lower() in _TOTAL_ROW_LABELS
            if not (has_formula or is_agg):
                break
            data_end -= 1
            idx -= 1
        if data_end < data_start:
            continue
        steps.append(
            {
                "action": "excel_live.set_formula",
                "params": {
                    "range_ref": cell,
                    "formula_a1": f"={func}('{current_sheet}'!{col_letter}{data_start}:{col_letter}{data_end})",
                },
                "reason": f"{current_sheet} 시트 {header_word} {func_word}를 {cell}에",
            }
        )
    return steps


def build_aggregate_below_plan(
    func: str,
    label: str,
    target_range: str,
    values_2d: list[list[Any]] | None,
) -> list[dict[str, Any]]:
    """숫자 열마다 집계 수식을, 라벨 열에 이름표를 — 범위 바로 아랫줄에.

    values_2d가 비거나 숫자 열이 없으면 빈 계획을 돌려준다(호출부가 되묻는다).
    """
    m = _RANGE.match(str(target_range or "").strip().upper())
    if not m:
        return []
    start_letter, start_row, end_letter, end_row = (
        m.group(1),
        int(m.group(2)),
        m.group(3),
        int(m.group(4)),
    )
    rows = values_2d or []
    if not rows:
        return []
    start_idx = column_index_from_string(start_letter)
    end_idx = column_index_from_string(end_letter)

    def _cell(r: int, c: int) -> Any:
        row = rows[r] if r < len(rows) else []
        return row[c] if isinstance(row, list) and c < len(row) else None

    # 첫 행이 전부 글자(또는 빈 칸)면 머리글이다 — 집계 구간에서 뺀다.
    n_cols = end_idx - start_idx + 1
    first = [_cell(0, c) for c in range(n_cols)]
    has_header = len(rows) > 1 and all(
        v is None or isinstance(v, str) for v in first
    ) and any(isinstance(v, str) and v.strip() for v in first)
    data_start = start_row + 1 if has_header else start_row
    below = end_row + 1

    steps: list[dict[str, Any]] = []
    label_letter: str | None = None
    for offset in range(n_cols):
        letter = get_column_letter(start_idx + offset)
        col_values = [
            _cell(r, offset)
            for r in range(data_start - start_row, end_row - start_row + 1)
        ]
        numeric = [
            v for v in col_values if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if numeric:
            steps.append(
                {
                    "action": "excel_live.set_formula",
                    "params": {
                        "range_ref": f"{letter}{below}",
                        "formula_a1": f"={func}({letter}{data_start}:{letter}{end_row})",
                    },
                    "reason": f"{letter}열 {label}을 범위 아랫줄에",
                }
            )
        elif label_letter is None:
            label_letter = letter
    if not steps:
        return []
    if label_letter is not None:
        steps.append(
            {
                "action": "excel_live.write_range",
                "params": {"start_cell": f"{label_letter}{below}", "values_2d": [[label]]},
                "reason": "집계 줄 이름표",
            }
        )
    return steps
