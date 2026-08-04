"""
한국어 수량 표현 파서 — "10만 원", "1.5억", "80%", "1,200건"을 숫자로 바꾼다.

사용자는 임계값을 "10만 원 미만"처럼 말하는데, 숫자만 보는 정규식은 여기서 10을 집어
"10 미만"으로 실행해 버린다. 조건 서식·필터가 조용히 잘못 걸리는 흔한 원인이다.

상태 없는 순수 함수 모듈. 조건 파서와 바인더가 함께 쓴다.
"""

from __future__ import annotations

import re

# 배수 접미사. 긴 것부터 확인해야 "십만"이 "만"에 먹히지 않는다.
_SCALES: tuple[tuple[str, float], ...] = (
    ("조", 1_000_000_000_000.0),
    ("억", 100_000_000.0),
    ("만", 10_000.0),
    ("천", 1_000.0),
    ("백", 100.0),
)

_NUMBER_WITH_SCALE = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천|백)?\s*(%|퍼센트|프로)?",
)


def parse_amount(text: str) -> float | None:
    """문자열 앞부분의 수량 표현 하나를 숫자로 바꾼다. 못 읽으면 None."""
    match = _NUMBER_WITH_SCALE.search(str(text or ""))
    if not match or not match.group(1):
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    scale = match.group(2)
    if scale:
        for name, factor in _SCALES:
            if scale == name:
                value *= factor
                break
    return value


def is_percent(text: str) -> bool:
    return bool(re.search(r"\d\s*(?:%|퍼센트|프로)", str(text or "")))


_CONDITION_WORDS: dict[str, str] = {
    "같지 않음": "!=",
    "같지않음": "!=",
    "이상": ">=",
    "초과": ">",
    "넘는": ">",
    "넘으면": ">",
    "이하": "<=",
    "미만": "<",
    "밑": "<",
    "같음": "==",
    "같으면": "==",
    "이면": "==",
    # 구어체 부정형. "100만도 안 되는", "80%가 안 되는 일감"처럼 실제로 가장 많이 쓴다.
    "안 되는": "<",
    "안되는": "<",
    "안 됨": "<",
    "안됨": "<",
    "안 되": "<",
    "못 미치는": "<",
    "못미치는": "<",
    "넘지 않는": "<=",
    "넘지않는": "<=",
    "안 넘는": "<=",
    "안넘는": "<=",
}
# "10만 원 미만" — 숫자와 비교어 사이에 단위·조사가 끼어드는 것을 허용한다.
# 긴 표현부터 시도해야 "같지 않음"이 "같음"에 먹히지 않는다.
_CONDITION_PATTERN = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천|백)?\s*(?:%|퍼센트|프로)?\s*"
    r"(?:원|개|건|점|명|kg|일)?\s*(?:이|가|은|는|을|를|도|에)?\s*"
    r"(" + "|".join(sorted(_CONDITION_WORDS, key=len, reverse=True)) + r")"
)


def parse_condition(text: str) -> tuple[str, float, bool] | None:
    """ "10만 원 미만" → ("<", 100000.0, False), "80% 미만" → ("<", 80.0, True).

    세 번째 값은 퍼센트 표기 여부다. 시트가 비율(0~1)로 저장돼 있으면 호출자가 나눠 쓴다.
    """
    lowered = str(text or "")
    symbol = re.search(r"(>=|<=|>|<|==|!=)\s*(-?\d[\d,]*(?:\.\d+)?)", lowered)
    if symbol:
        try:
            return symbol.group(1), float(symbol.group(2).replace(",", "")), False
        except ValueError:
            return None

    match = _CONDITION_PATTERN.search(lowered)
    if not match:
        return None
    amount = parse_amount(match.group(0))
    if amount is None:
        return None
    return _CONDITION_WORDS[match.group(3)], amount, is_percent(match.group(0))
