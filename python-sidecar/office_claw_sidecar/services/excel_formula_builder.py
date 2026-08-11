"""
이름으로 말한 계산식을 엑셀 수식으로 바꾸는 레이어.

"이익률 열에 매출이익 나누기 매출 수식을 넣어줘"처럼 사람은 셀 주소 대신 열 이름과
한국어 연산어로 계산을 말한다. 기존 수식 규칙은 "B열 수량 × C열 단가"처럼 열 문자를
전제로 해서, 이런 문장을 만나면 전부 되묻기로 떨어졌다.

이 모듈은 문장에서 "결과 열 + 피연산자 열들 + 연산 순서"만 뽑아 두고(`parse_named_formula`),
실제 열 문자 확정은 통합문서 머리글을 아는 바인더가 맡는다(`build_formula`).
그래서 이 모듈은 시트를 몰라도 되고, 순수 함수로 테스트할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .excel_header_lexicon import find_header_mentions

# 연산어. infix는 두 피연산자 사이, postfix는 뒤에서 앞의 두 개를 묶는다
# ("재주문점 두 배에서 현재고를 뺀" → 뺀은 뒤에 온다).
_INFIX_OPS: tuple[tuple[str, str], ...] = (
    ("나누기", "/"),
    ("나눈", "/"),
    ("÷", "/"),
    ("곱하기", "*"),
    ("곱한", "*"),
    ("×", "*"),
    ("더하기", "+"),
    ("빼기", "-"),
)
_POSTFIX_OPS: tuple[tuple[str, str], ...] = (
    ("뺀", "-"),
    ("빼서", "-"),
    ("차감", "-"),
    ("더한", "+"),
    ("합한", "+"),
    ("곱한", "*"),
    ("나눈", "/"),
)
_OP_SYMBOLS: tuple[tuple[str, str], ...] = (("/", "/"), ("*", "*"), ("+", "+"))

# "재주문점 두 배" 같은 배율 표현.
_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("두 배", 2.0),
    ("두배", 2.0),
    ("2배", 2.0),
    ("세 배", 3.0),
    ("3배", 3.0),
    ("절반", 0.5),
)

_TARGET_PATTERN = re.compile(r"([가-힣A-Za-z0-9_ ]{2,14}?)\s*(?:열|칼럼|컬럼|필드)\s*(?:에|에다|을|를|은|는)")


@dataclass
class NamedFormula:
    """열 이름으로 표현된 계산식. 열 문자는 아직 모른다."""

    target: str
    operands: list[str] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    scales: dict[int, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "operands": list(self.operands),
            "operators": list(self.operators),
            "scales": {str(k): v for k, v in self.scales.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NamedFormula:
        scales = {}
        for key, value in (data.get("scales") or {}).items():
            try:
                scales[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return cls(
            target=str(data.get("target") or ""),
            operands=[str(x) for x in (data.get("operands") or [])],
            operators=[str(x) for x in (data.get("operators") or [])],
            scales=scales,
        )


def _scale_after(text: str, position: int) -> float | None:
    window = text[position : position + 8]
    for surface, factor in _MULTIPLIERS:
        if surface in window:
            return factor
    return None


def _operator_between(text: str, start: int, end: int) -> str | None:
    segment = text[start:end]
    for surface, symbol in _INFIX_OPS:
        if surface in segment:
            return symbol
    for surface, symbol in _OP_SYMBOLS:
        if surface in segment:
            return symbol
    return None


def _operator_after(text: str, position: int) -> str | None:
    segment = text[position : position + 14]
    for surface, symbol in _POSTFIX_OPS:
        if surface in segment:
            return symbol
    return None


def parse_named_formula(message: str, headers: list[str]) -> NamedFormula | None:
    """ "이익률 열에 매출이익 나누기 매출" → target=이익률, operands=[매출이익, 매출], ops=[/].

    결과 열과 피연산자를 모두 찾지 못하거나 연산어가 없으면 None을 돌려준다.
    추측해서 엉뚱한 수식을 심는 것보다 되묻는 편이 낫다.
    """
    text = str(message or "")
    if not text or not headers:
        return None

    target_match = _TARGET_PATTERN.search(text)
    if not target_match:
        return None
    target_term = target_match.group(1).strip()
    if not target_term:
        return None

    expression = text[target_match.end() :]
    mentions = [m for m in find_header_mentions(expression, headers) if m["header"] != target_term]
    # 결과 열과 같은 열이 피연산자로 잡히면 순환 참조가 된다.
    target_hits = find_header_mentions(target_term, headers)
    target_header = target_hits[0]["header"] if target_hits else target_term
    mentions = [m for m in mentions if m["header"] != target_header]
    if len(mentions) < 2:
        return None

    formula = NamedFormula(target=target_header)
    formula.operands.append(mentions[0]["header"])
    scale = _scale_after(expression, mentions[0]["end"])
    if scale is not None:
        formula.scales[0] = scale

    for index in range(1, len(mentions)):
        previous, current = mentions[index - 1], mentions[index]
        operator = _operator_between(expression, previous["end"], current["start"])
        if operator is None:
            operator = _operator_after(expression, current["end"])
        if operator is None:
            return None
        formula.operators.append(operator)
        formula.operands.append(current["header"])
        scale = _scale_after(expression, current["end"])
        if scale is not None:
            formula.scales[index] = scale

    return formula


def build_formula(formula: NamedFormula, letters: list[str], row: int) -> str:
    """확정된 열 문자로 A1 수식을 만든다. 나눗셈은 0으로 나누기를 막아 준다."""
    if not letters or len(letters) != len(formula.operands):
        return ""

    def term(index: int) -> str:
        ref = f"{letters[index]}{row}"
        scale = formula.scales.get(index)
        return f"({ref}*{scale:g})" if scale is not None else ref

    expression = term(0)
    for index, operator in enumerate(formula.operators, start=1):
        expression = f"{expression}{operator}{term(index)}"

    if "/" in formula.operators:
        # 분모가 비어 있는 행에서 #DIV/0! 이 뜨면 사용자는 수식이 잘못됐다고 본다.
        denominators = [
            term(index)
            for index, operator in enumerate(formula.operators, start=1)
            if operator == "/"
        ]
        guard = "*".join(denominators)
        return f"=IFERROR({expression},\"\")" if len(guard) > 40 else f"=IF({guard}=0,\"\",{expression})"
    return f"={expression}"
