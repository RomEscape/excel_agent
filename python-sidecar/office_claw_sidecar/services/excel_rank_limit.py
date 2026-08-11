"""수량 한정어("상위 3개", "매출 높은 10개")를 실제 기준값으로 바꾼다.

다이제스트는 머리글과 예시 3행만 준다. 상위 N이 무엇인지는 열 전체를 봐야 알 수
있으므로 모델에게 물어보면 지어낸 숫자가 돌아온다. 여기서는 모델에게 "무엇을 몇 개"
까지만 맡기고(`detect`), 그 N번째 값이 얼마인지는 파일을 읽어서 정한다(`resolve`).

계산은 순수 함수로 두고 파일 읽기는 호출자가 주입한다 — COM 없이 시험할 수 있어야
한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from office_claw_sidecar.services.excel_header_lexicon import resolve_header
from office_claw_sidecar.services.excel_param_binder import sheet_entry

# "상위 3개" / "top 5" — 순위 말과 개수가 붙어 있는 형태.
_EXPLICIT_RANK = re.compile(r"(상위|하위|top|bottom)\s*(\d{1,4})", re.IGNORECASE)
# "매출 높은 10개" / "가장 큰 3건" — 비교급과 개수가 붙어 있는 형태.
_COMPARATIVE_RANK = re.compile(
    r"(높은|낮은|큰|작은|많은|적은)\s*(?:순(?:으로|서로)?\s*)?(\d{1,4})\s*(?:개|건|행|위|명|가지|줄)",
    re.IGNORECASE,
)
_DESCENDING_WORDS = ("상위", "top", "높은", "큰", "많은")

# 개수 앞에 붙는 기준 열 이름. "금액이 높은 상위 3개"의 "금액".
_METRIC_BEFORE_RANK = re.compile(
    r"([가-힣A-Za-z_][가-힣A-Za-z0-9_ ]{0,15}?)\s*(?:이|가|을|를|은|는)?\s*"
    r"(?:제일|가장|최고|최대)?\s*(?:높은|낮은|큰|작은|많은|적은|상위|하위)"
)


@dataclass(frozen=True)
class RankLimit:
    """"금액이 높은 상위 3개"에서 뽑아낸 것.

    `metric_term`은 사용자가 부른 이름 그대로다. 시트 머리글로 옮기는 것은
    `resolve_step`이 한다 — 사용자는 "매출"이라 부르고 머리글은 Revenue일 수 있다.
    """

    count: int
    descending: bool
    metric_term: str = ""


def detect(message: str) -> RankLimit | None:
    """문장이 몇 개로 대상을 좁히고 있는지. 없으면 None."""
    text = str(message or "")
    match = _EXPLICIT_RANK.search(text) or _COMPARATIVE_RANK.search(text)
    if match is None:
        return None
    try:
        count = int(match.group(2))
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    word = match.group(1).lower()
    descending = any(token in word for token in _DESCENDING_WORDS)
    return RankLimit(count=count, descending=descending, metric_term=_metric_term(text))


def _metric_term(text: str) -> str:
    found = _METRIC_BEFORE_RANK.search(text)
    if found is None:
        return ""
    # "금액이 높은"의 "금액". 앞말이 통째로 딸려 오면 마지막 어절만 쓴다.
    return (found.group(1) or "").strip().split()[-1] if (found.group(1) or "").strip() else ""


def threshold_for(values: Sequence[Any], limit: RankLimit) -> float | None:
    """N번째로 크거나 작은 값. 숫자가 N개보다 적으면 None.

    None은 "이 열로는 상위 N을 정할 수 없다"는 뜻이고, 호출자는 지어내는 대신
    되묻거나 포기해야 한다.
    """
    numbers = sorted(_numbers(values), reverse=limit.descending)
    if len(numbers) < limit.count:
        return None
    return numbers[limit.count - 1]


def _numbers(values: Sequence[Any]) -> list[float]:
    """엑셀이 돌려주는 [[520], [180]] 형태와 평평한 [520, 180]을 모두 받는다."""
    out: list[float] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            out.extend(_numbers(value))
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append(float(value))
            continue
        text = str(value or "").strip().replace(",", "")
        if not text:
            continue
        try:
            out.append(float(text))
        except ValueError:
            continue
    return out


def _column_letters(entry: dict[str, Any], header: str) -> str:
    for column in entry.get("columns") or []:
        if str(column.get("header") or "") == header:
            return str(column.get("letter") or "")
    return ""


def _last_row(entry: dict[str, Any]) -> int:
    match = re.search(r"(\d+)\s*$", str(entry.get("used_range") or ""))
    return int(match.group(1)) if match else 0


def _numeric_headers(entry: dict[str, Any]) -> list[str]:
    return [
        str(col.get("header") or "")
        for col in entry.get("columns") or []
        if col.get("numeric") and str(col.get("header") or "")
    ]


def resolve_step(
    message: str,
    digest: dict[str, Any] | None,
    *,
    sheet_name: str | None,
    read_column: Callable[[str], Sequence[Any]],
    fill_color: str = "#FFFF00",
) -> dict[str, Any] | None:
    """"상위 N 강조"를 기준값이 박힌 조건부 서식 한 단계로 바꾼다.

    `read_column`은 "C2:C9" 같은 범위를 받아 값 목록을 돌려준다. 기준 열을
    확정할 수 없거나 값이 모자라면 None을 돌려준다 — 그때는 전체를 칠하느니
    아무것도 하지 않는 편이 낫다.
    """
    limit = detect(message)
    if limit is None:
        return None
    entry = sheet_entry(digest or {}, sheet_name)
    if not entry:
        return None

    numeric = _numeric_headers(entry)
    header = resolve_header(limit.metric_term, numeric) if limit.metric_term else None
    if not header:
        # 기준을 말하지 않았어도 숫자 열이 하나뿐이면 모호하지 않다.
        if len(numeric) != 1:
            return None
        header = numeric[0]

    letter = _column_letters(entry, header)
    last_row = _last_row(entry)
    if not letter or last_row < 2:
        return None

    threshold = threshold_for(read_column(f"{letter}2:{letter}{last_row}"), limit)
    if threshold is None:
        return None

    return {
        "action": "excel_live.highlight_by_condition",
        "params": {
            "target_range": f"{letter}2:{letter}{last_row}",
            "operator": ">=" if limit.descending else "<=",
            "threshold": threshold,
            "fill_color": fill_color,
        },
        "reason": f"원문의 '{limit.count}개' 한정을 {header} 열 실제 값으로 환산",
    }
