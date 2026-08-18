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
        re.compile(r"(모든|각|전체)\s*합|합계|총합|총\s*합|합(?:을|이|만|과|값)|더한\s*값|다\s*더해", re.IGNORECASE),
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
