"""엑셀 수식 계산기.

openpyxl은 수식을 계산하지 않는다. Excel이 한 번도 저장하지 않은 파일(우리가 만들었거나
스크립트로 생성한 파일)에는 계산 캐시조차 없어서 `data_only=True`로 열면 수식 셀이 전부
None이 된다. 그러면 "매출이 10만 원 미만인 행" 같은 조건이 한 건도 걸리지 않는다.
실무 파일에서 매출·이익·이익률처럼 조건의 대상이 되는 열이 대부분 수식이라 치명적이다.

여기서는 실제 업무 파일에 자주 나오는 범위만 계산한다. 데모 워크북 703개 수식을 조사한
결과 418개가 함수 없는 사칙연산이고, 나머지는 IFERROR·IF·SUMIF(S)·COUNTIF(S)·MAX·
TODAY·DATE 계열에 몰려 있었다. 지원하지 않는 함수를 만나면 조용히 0을 돌려주지 않고
`FormulaError`를 내서 호출자가 "계산하지 못했다"를 구분할 수 있게 한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any

__all__ = ["FormulaError", "WorkbookEvaluator", "excel_serial", "from_excel_serial"]

# Excel 날짜 기준일. 1900 윤년 버그 때문에 1899-12-30을 0일로 둔다.
_EPOCH = datetime(1899, 12, 30)

_MAX_DEPTH = 64


class FormulaError(Exception):
    """수식을 계산할 수 없을 때. `code`는 Excel 오류 표기(#VALUE! 등)."""

    def __init__(self, code: str = "#VALUE!", detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def excel_serial(value: date | datetime) -> float:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime(value.year, value.month, value.day)
    return (moment - _EPOCH).total_seconds() / 86400.0


def from_excel_serial(serial: float) -> datetime:
    return _EPOCH + timedelta(days=float(serial))


# --------------------------------------------------------------------------
# 값 변환
# --------------------------------------------------------------------------


def _to_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (datetime, date)):
        return excel_serial(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text.endswith("%"):
            try:
                return float(text[:-1]) / 100.0
            except ValueError as exc:
                raise FormulaError("#VALUE!", f"숫자로 볼 수 없음: {value!r}") from exc
        try:
            return float(text)
        except ValueError:
            parsed = _parse_date_text(text)
            if parsed is not None:
                return excel_serial(parsed)
            raise FormulaError("#VALUE!", f"숫자로 볼 수 없음: {value!r}")
    raise FormulaError("#VALUE!", f"숫자로 볼 수 없음: {value!r}")


def _parse_date_text(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _to_text(value: Any) -> str:
    """Excel의 문자열 결합 규칙. 날짜는 일련번호로 바뀐다(">="&DATE(...) 패턴)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date)):
        value = excel_serial(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper == "TRUE":
            return True
        if upper in ("FALSE", ""):
            return False
        raise FormulaError("#VALUE!", f"참/거짓으로 볼 수 없음: {value!r}")
    return _to_number(value) != 0


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _compare(left: Any, right: Any) -> int:
    """Excel 비교 규칙(간소화). 숫자끼리면 숫자로, 아니면 대소문자 무시 문자열로."""
    left_num = _maybe_number(left)
    right_num = _maybe_number(right)
    if left_num is not None and right_num is not None:
        return (left_num > right_num) - (left_num < right_num)
    lt = _to_text(left).casefold()
    rt = _to_text(right).casefold()
    return (lt > rt) - (lt < rt)


def _maybe_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (datetime, date)):
        return excel_serial(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# 토크나이저
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<error>\#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A))
  | (?P<sheetref>(?:'(?:[^']|'')+'|[A-Za-z_\u00c0-\uffff][A-Za-z0-9_.\u00c0-\uffff]*)!\$?[A-Za-z]{1,3}\$?\d+)
  | (?P<ref>\$?[A-Za-z]{1,3}\$?\d+)
  | (?P<sheetcol>(?:'(?:[^']|'')+'|[A-Za-z_\u00c0-\uffff][A-Za-z0-9_.\u00c0-\uffff]*)!\$?[A-Za-z]{1,3}(?=\s*:))
  | (?P<colref>\$?[A-Za-z]{1,3}(?=\s*:\s*\$?[A-Za-z]{1,3}(?![A-Za-z0-9])))
  | (?P<colend>\$[A-Za-z]{1,3}(?![A-Za-z0-9]))
  | (?P<func>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<op><=|>=|<>|[-+*/^&<>=%(),:;])
    """,
    re.VERBOSE,
)


class _Token:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        return f"<{self.kind}:{self.text}>"


def _tokenize(formula: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    length = len(formula)
    while pos < length:
        match = _TOKEN_RE.match(formula, pos)
        if match is None:
            raise FormulaError("#NAME?", f"해석할 수 없는 문자: {formula[pos]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        tokens.append(_Token(kind, match.group()))
    return tokens


# --------------------------------------------------------------------------
# 참조
# --------------------------------------------------------------------------

_REF_PART = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def _column_index(letters: str) -> int:
    total = 0
    for ch in letters.upper():
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total


def _split_sheet(text: str) -> tuple[str | None, str]:
    if "!" not in text:
        return None, text
    sheet, _, ref = text.rpartition("!")
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, ref


class _Range:
    """함수 인자로 넘어가는 셀 범위. 스칼라 자리에서는 첫 셀처럼 취급한다."""

    __slots__ = ("col1", "col2", "row1", "row2", "sheet")

    def __init__(self, sheet: str | None, row1: int, col1: int, row2: int, col2: int) -> None:
        self.sheet = sheet
        self.row1, self.col1 = min(row1, row2), min(col1, col2)
        self.row2, self.col2 = max(row1, row2), max(col1, col2)


# --------------------------------------------------------------------------
# 평가기
# --------------------------------------------------------------------------

RawLookup = Callable[[str, int, int], Any]


class WorkbookEvaluator:
    """워크북 하나에 대한 수식 계산기.

    `raw_lookup(sheet, row, col)`은 셀의 원본 값(수식 문자열 또는 리터럴)을 준다.
    `cached_lookup`이 값을 주면 그걸 우선 쓴다 — Excel이 남긴 계산 캐시가 있으면
    굳이 다시 계산하지 않는다.
    """

    def __init__(
        self,
        raw_lookup: RawLookup,
        *,
        cached_lookup: RawLookup | None = None,
        default_sheet: str = "",
        today: date | None = None,
        sheet_bounds: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        self._raw = raw_lookup
        self._cached = cached_lookup
        self._default_sheet = default_sheet
        self._today = today or date.today()
        self._sheet_bounds = sheet_bounds
        self._memo: dict[tuple[str, int, int], Any] = {}
        self._stack: set[tuple[str, int, int]] = set()

    # -- 공개 API ---------------------------------------------------------

    def value(self, sheet: str, row: int, col: int) -> Any:
        """셀의 계산된 값. 수식이면 계산하고, 아니면 원본 값을 그대로 준다."""
        key = (sheet, row, col)
        if key in self._memo:
            return self._memo[key]
        if key in self._stack:
            raise FormulaError("#REF!", "순환 참조")
        if self._cached is not None:
            cached = self._cached(sheet, row, col)
            if cached is not None:
                self._memo[key] = cached
                return cached
        raw = self._raw(sheet, row, col)
        if not (isinstance(raw, str) and raw.startswith("=")):
            self._memo[key] = raw
            return raw
        self._stack.add(key)
        try:
            result = self.evaluate(raw, sheet=sheet)
        finally:
            self._stack.discard(key)
        self._memo[key] = result
        return result

    def evaluate(self, formula: str, *, sheet: str | None = None) -> Any:
        """수식 문자열 하나를 계산한다."""
        if len(self._stack) > _MAX_DEPTH:
            raise FormulaError("#REF!", "참조 깊이 초과")
        text = formula[1:] if formula.startswith("=") else formula
        tokens = _tokenize(text)
        parser = _Parser(tokens, self, sheet or self._default_sheet)
        result = parser.parse()
        return self._scalar(result)

    # -- 내부 -------------------------------------------------------------

    def _scalar(self, value: Any) -> Any:
        if isinstance(value, _Range):
            return self.value(value.sheet or self._default_sheet, value.row1, value.col1)
        return value

    def iter_range(self, rng: _Range) -> Iterable[Any]:
        sheet = rng.sheet or self._default_sheet
        for r in range(rng.row1, rng.row2 + 1):
            for c in range(rng.col1, rng.col2 + 1):
                yield self.value(sheet, r, c)

    @property
    def today(self) -> date:
        return self._today

    def last_row(self, sheet: str) -> int:
        """`B:B` 같은 열 전체 참조를 실제 데이터 끝까지로 좁힌다."""
        if self._sheet_bounds is None:
            raise FormulaError("#REF!", "열 전체 참조를 쓰려면 시트 범위를 알아야 합니다")
        return self._sheet_bounds(sheet)[0]


# --------------------------------------------------------------------------
# 파서 (재귀 하강, 평가 동시 수행)
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Token], evaluator: WorkbookEvaluator, sheet: str) -> None:
        self._tokens = tokens
        self._pos = 0
        self._ev = evaluator
        self._sheet = sheet

    # -- 토큰 도우미 ------------------------------------------------------

    def _peek(self) -> _Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> _Token:
        token = self._peek()
        if token is None:
            raise FormulaError("#VALUE!", "수식이 도중에 끝났습니다")
        self._pos += 1
        return token

    def _accept_op(self, *ops: str) -> str | None:
        token = self._peek()
        if token is not None and token.kind == "op" and token.text in ops:
            self._pos += 1
            return token.text
        return None

    def _expect_op(self, op: str) -> None:
        if self._accept_op(op) is None:
            raise FormulaError("#VALUE!", f"{op!r}가 필요합니다")

    # -- 문법 -------------------------------------------------------------

    def parse(self) -> Any:
        value = self._comparison()
        if self._peek() is not None:
            raise FormulaError("#VALUE!", "수식 뒤에 남은 내용이 있습니다")
        return value

    def _comparison(self) -> Any:
        left = self._concat()
        while True:
            op = self._accept_op("=", "<>", "<", "<=", ">", ">=")
            if op is None:
                return left
            right = self._concat()
            cmp = _compare(self._ev._scalar(left), self._ev._scalar(right))
            left = {
                "=": cmp == 0,
                "<>": cmp != 0,
                "<": cmp < 0,
                "<=": cmp <= 0,
                ">": cmp > 0,
                ">=": cmp >= 0,
            }[op]

    def _concat(self) -> Any:
        left = self._additive()
        while self._accept_op("&") is not None:
            right = self._additive()
            left = _to_text(self._ev._scalar(left)) + _to_text(self._ev._scalar(right))
        return left

    def _additive(self) -> Any:
        left = self._multiplicative()
        while True:
            op = self._accept_op("+", "-")
            if op is None:
                return left
            right = self._multiplicative()
            lnum = _to_number(self._ev._scalar(left))
            rnum = _to_number(self._ev._scalar(right))
            left = lnum + rnum if op == "+" else lnum - rnum

    def _multiplicative(self) -> Any:
        left = self._unary()
        while True:
            op = self._accept_op("*", "/")
            if op is None:
                return left
            right = self._unary()
            lnum = _to_number(self._ev._scalar(left))
            rnum = _to_number(self._ev._scalar(right))
            if op == "*":
                left = lnum * rnum
            else:
                if rnum == 0:
                    raise FormulaError("#DIV/0!", "0으로 나눔")
                left = lnum / rnum
        return left

    def _unary(self) -> Any:
        op = self._accept_op("-", "+")
        if op is not None:
            value = _to_number(self._ev._scalar(self._unary()))
            return -value if op == "-" else value
        return self._power()

    def _power(self) -> Any:
        base = self._postfix()
        if self._accept_op("^") is not None:
            exponent = self._unary()
            return _to_number(self._ev._scalar(base)) ** _to_number(self._ev._scalar(exponent))
        return base

    def _postfix(self) -> Any:
        value = self._primary()
        while self._accept_op("%") is not None:
            value = _to_number(self._ev._scalar(value)) / 100.0
        return value

    def _primary(self) -> Any:
        token = self._next()
        if token.kind == "number":
            return float(token.text)
        if token.kind == "string":
            return token.text[1:-1].replace('""', '"')
        if token.kind == "error":
            raise FormulaError(token.text, "수식에 오류 값이 있습니다")
        if token.kind == "op" and token.text == "(":
            value = self._comparison()
            self._expect_op(")")
            return value
        if token.kind in ("ref", "sheetref"):
            return self._reference(token.text)
        if token.kind in ("colref", "sheetcol"):
            return self._column_range(token.text)
        if token.kind == "func":
            upper = token.text.upper()
            if upper in ("TRUE", "FALSE") and not self._is_call():
                return upper == "TRUE"
            if self._is_call():
                return self._call(upper)
            raise FormulaError("#NAME?", f"알 수 없는 이름: {token.text}")
        raise FormulaError("#VALUE!", f"예상치 못한 토큰: {token.text!r}")

    def _is_call(self) -> bool:
        token = self._peek()
        return token is not None and token.kind == "op" and token.text == "("

    def _reference(self, text: str) -> Any:
        sheet, ref = _split_sheet(text)
        match = _REF_PART.match(ref)
        if match is None:
            raise FormulaError("#REF!", f"참조를 해석할 수 없음: {text}")
        col = _column_index(match.group(1))
        row = int(match.group(2))
        if self._accept_op(":") is not None:
            end = self._next()
            if end.kind not in ("ref", "sheetref"):
                raise FormulaError("#REF!", "범위 끝을 해석할 수 없습니다")
            end_sheet, end_ref = _split_sheet(end.text)
            end_match = _REF_PART.match(end_ref)
            if end_match is None:
                raise FormulaError("#REF!", f"참조를 해석할 수 없음: {end.text}")
            return _Range(
                sheet or end_sheet or self._sheet,
                row,
                col,
                int(end_match.group(2)),
                _column_index(end_match.group(1)),
            )
        return self._ev.value(sheet or self._sheet, row, col)

    def _column_range(self, text: str) -> _Range:
        """`B:B`, `Sales_Data!$L:$L` — 열 전체 참조."""
        sheet, start_letters = _split_sheet(text)
        self._expect_op(":")
        end = self._next()
        if end.kind not in ("colref", "colend", "sheetcol", "func"):
            raise FormulaError("#REF!", "열 범위 끝을 해석할 수 없습니다")
        _, end_letters = _split_sheet(end.text)
        target_sheet = sheet or self._sheet
        return _Range(
            target_sheet,
            1,
            _column_index(start_letters.lstrip("$")),
            self._ev.last_row(target_sheet),
            _column_index(end_letters.lstrip("$")),
        )

    def _call(self, name: str) -> Any:
        self._expect_op("(")
        args: list[Any] = []
        if self._accept_op(")") is None:
            while True:
                args.append(self._argument(name, len(args)))
                if self._accept_op(",", ";") is not None:
                    continue
                self._expect_op(")")
                break
        return _apply_function(name, args, self._ev)

    def _argument(self, func_name: str, index: int) -> Any:
        """IFERROR/IF의 분기 인자는 오류가 나도 다른 분기를 살려야 한다."""
        start = self._pos
        try:
            return self._comparison()
        except FormulaError:
            if func_name in ("IFERROR", "IFNA", "IF"):
                self._skip_argument(start)
                return _Deferred()
            raise

    def _skip_argument(self, start: int) -> None:
        """오류가 난 인자를 토큰 단위로 건너뛴다."""
        self._pos = start
        depth = 0
        while self._pos < len(self._tokens):
            token = self._tokens[self._pos]
            if token.kind == "op":
                if token.text == "(":
                    depth += 1
                elif token.text == ")":
                    if depth == 0:
                        return
                    depth -= 1
                elif token.text in (",", ";") and depth == 0:
                    return
            self._pos += 1


class _Deferred:
    """계산 중 오류가 났던 인자. IFERROR가 잡아 주면 결과에 쓰이지 않는다."""

    __slots__ = ()


# --------------------------------------------------------------------------
# 함수
# --------------------------------------------------------------------------


def _flatten(args: Iterable[Any], evaluator: WorkbookEvaluator) -> list[Any]:
    out: list[Any] = []
    for arg in args:
        if isinstance(arg, _Range):
            out.extend(evaluator.iter_range(arg))
        elif isinstance(arg, _Deferred):
            raise FormulaError("#VALUE!", "계산하지 못한 인자")
        else:
            out.append(arg)
    return out


def _numbers(args: Iterable[Any], evaluator: WorkbookEvaluator) -> list[float]:
    out: list[float] = []
    for value in _flatten(args, evaluator):
        if _is_blank(value) or isinstance(value, bool):
            continue
        number = _maybe_number(value)
        if number is not None:
            out.append(number)
    return out


_CRITERIA_RE = re.compile(r"^(<=|>=|<>|<|>|=)?(.*)$", re.DOTALL)


def _matches_criteria(value: Any, criteria: Any) -> bool:
    if isinstance(criteria, (int, float)) and not isinstance(criteria, bool):
        return _maybe_number(value) == float(criteria)
    text = _to_text(criteria)
    match = _CRITERIA_RE.match(text)
    op = (match.group(1) if match else "") or "="
    operand_text = (match.group(2) if match else text).strip()
    operand: Any = operand_text
    operand_num = _maybe_number(operand_text)
    if operand_num is not None:
        operand = operand_num
    if op == "=":
        if operand_text == "":
            return _is_blank(value)
        return _compare(value, operand) == 0
    if op == "<>":
        if operand_text == "":
            return not _is_blank(value)
        return _compare(value, operand) != 0
    if _is_blank(value):
        return False
    cmp = _compare(value, operand)
    return {
        "<": cmp < 0,
        "<=": cmp <= 0,
        ">": cmp > 0,
        ">=": cmp >= 0,
    }[op]


def _range_cells(arg: Any, evaluator: WorkbookEvaluator) -> list[Any]:
    if isinstance(arg, _Range):
        return list(evaluator.iter_range(arg))
    return [arg]


def _conditional_pairs(args: list[Any], evaluator: WorkbookEvaluator, start: int) -> list[tuple[list[Any], Any]]:
    pairs: list[tuple[list[Any], Any]] = []
    idx = start
    while idx + 1 < len(args):
        pairs.append((_range_cells(args[idx], evaluator), evaluator._scalar(args[idx + 1])))
        idx += 2
    return pairs


def _selected_indices(pairs: list[tuple[list[Any], Any]]) -> list[int]:
    if not pairs:
        return []
    length = min(len(cells) for cells, _ in pairs)
    return [
        i for i in range(length) if all(_matches_criteria(cells[i], criteria) for cells, criteria in pairs)
    ]


def _apply_function(name: str, args: list[Any], ev: WorkbookEvaluator) -> Any:
    scalar = ev._scalar

    if name == "IF":
        condition = args[0] if args else False
        if isinstance(condition, _Deferred):
            raise FormulaError("#VALUE!", "IF 조건을 계산하지 못했습니다")
        chosen = args[1] if _to_bool(scalar(condition)) else (args[2] if len(args) > 2 else False)
        if isinstance(chosen, _Deferred):
            raise FormulaError("#VALUE!", "IF 분기를 계산하지 못했습니다")
        return scalar(chosen)

    if name in ("IFERROR", "IFNA"):
        primary = args[0] if args else _Deferred()
        if not isinstance(primary, _Deferred):
            return scalar(primary)
        fallback = args[1] if len(args) > 1 else 0
        if isinstance(fallback, _Deferred):
            raise FormulaError("#VALUE!", "IFERROR 대체값을 계산하지 못했습니다")
        return scalar(fallback)

    values = [scalar(a) if not isinstance(a, _Range) else a for a in args]

    if name == "SUM":
        return sum(_numbers(values, ev))
    if name in ("AVERAGE", "AVG"):
        nums = _numbers(values, ev)
        if not nums:
            raise FormulaError("#DIV/0!", "평균 대상이 없습니다")
        return sum(nums) / len(nums)
    if name == "MIN":
        nums = _numbers(values, ev)
        return min(nums) if nums else 0.0
    if name == "MAX":
        nums = _numbers(values, ev)
        return max(nums) if nums else 0.0
    if name == "COUNT":
        return float(len(_numbers(values, ev)))
    if name == "COUNTA":
        return float(sum(1 for v in _flatten(values, ev) if not _is_blank(v)))
    if name == "COUNTBLANK":
        return float(sum(1 for v in _flatten(values, ev) if _is_blank(v)))

    if name in ("SUMIF", "AVERAGEIF"):
        cells = _range_cells(values[0], ev)
        criteria = scalar(values[1]) if len(values) > 1 else ""
        target = _range_cells(values[2], ev) if len(values) > 2 else cells
        picked = [
            _maybe_number(target[i]) or 0.0
            for i in range(min(len(cells), len(target)))
            if _matches_criteria(cells[i], criteria)
        ]
        if name == "SUMIF":
            return float(sum(picked))
        if not picked:
            raise FormulaError("#DIV/0!", "평균 대상이 없습니다")
        return float(sum(picked) / len(picked))

    if name == "COUNTIF":
        cells = _range_cells(values[0], ev)
        criteria = scalar(values[1]) if len(values) > 1 else ""
        return float(sum(1 for cell in cells if _matches_criteria(cell, criteria)))

    if name == "SUMIFS":
        target = _range_cells(values[0], ev)
        indices = _selected_indices(_conditional_pairs(values, ev, 1))
        return float(sum(_maybe_number(target[i]) or 0.0 for i in indices if i < len(target)))

    if name == "COUNTIFS":
        return float(len(_selected_indices(_conditional_pairs(values, ev, 0))))

    if name == "AVERAGEIFS":
        target = _range_cells(values[0], ev)
        indices = _selected_indices(_conditional_pairs(values, ev, 1))
        picked = [_maybe_number(target[i]) or 0.0 for i in indices if i < len(target)]
        if not picked:
            raise FormulaError("#DIV/0!", "평균 대상이 없습니다")
        return float(sum(picked) / len(picked))

    if name == "AND":
        return all(_to_bool(v) for v in _flatten(values, ev))
    if name == "OR":
        return any(_to_bool(v) for v in _flatten(values, ev))
    if name == "NOT":
        return not _to_bool(values[0])

    if name == "ABS":
        return abs(_to_number(values[0]))
    if name == "INT":
        return float(int(_to_number(values[0]) // 1))
    if name == "SQRT":
        return _to_number(values[0]) ** 0.5
    if name == "ROUND":
        digits = int(_to_number(values[1])) if len(values) > 1 else 0
        return round(_to_number(values[0]), digits)
    if name in ("ROUNDUP", "ROUNDDOWN", "CEILING", "FLOOR"):
        number = _to_number(values[0])
        digits = int(_to_number(values[1])) if len(values) > 1 else 0
        factor = 10.0**digits
        scaled = number * factor
        if name in ("ROUNDUP", "CEILING"):
            bumped = -((-scaled) // 1) if scaled > 0 else scaled // 1
        else:
            bumped = scaled // 1 if scaled > 0 else -((-scaled) // 1)
        return bumped / factor

    if name == "TODAY":
        return excel_serial(ev.today)
    if name == "NOW":
        return excel_serial(datetime.now())
    if name == "DATE":
        year, month, day = (int(_to_number(v)) for v in values[:3])
        month_index = month - 1
        year += month_index // 12
        month = month_index % 12 + 1
        return excel_serial(datetime(year, month, 1) + timedelta(days=day - 1))
    if name == "EDATE":
        base = from_excel_serial(_to_number(values[0]))
        months = int(_to_number(values[1]))
        month_index = base.month - 1 + months
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        day = min(base.day, _days_in_month(year, month))
        return excel_serial(datetime(year, month, day))
    if name == "YEAR":
        return float(from_excel_serial(_to_number(values[0])).year)
    if name == "MONTH":
        return float(from_excel_serial(_to_number(values[0])).month)
    if name == "DAY":
        return float(from_excel_serial(_to_number(values[0])).day)

    if name == "VALUE":
        return _to_number(values[0])
    if name == "TEXT":
        return _to_text(values[0])
    if name == "LEN":
        return float(len(_to_text(values[0])))
    if name == "LEFT":
        count = int(_to_number(values[1])) if len(values) > 1 else 1
        return _to_text(values[0])[:count]
    if name == "RIGHT":
        count = int(_to_number(values[1])) if len(values) > 1 else 1
        text = _to_text(values[0])
        return text[-count:] if count else ""
    if name == "MID":
        start = int(_to_number(values[1]))
        count = int(_to_number(values[2]))
        return _to_text(values[0])[start - 1 : start - 1 + count]
    if name == "TRIM":
        return _to_text(values[0]).strip()
    if name in ("UPPER", "LOWER"):
        text = _to_text(values[0])
        return text.upper() if name == "UPPER" else text.lower()
    if name == "CONCATENATE":
        return "".join(_to_text(v) for v in _flatten(values, ev))

    # ── 찾기 함수 ──────────────────────────────────────────────────────
    #
    # 실무 대시보드에 거의 항상 들어가는데 통째로 빠져 있었다(2026-08-17 실측:
    # VLOOKUP·HLOOKUP·XLOOKUP·INDEX·MATCH 전부 #NAME?). 수식은 파일에 들어가지만
    # 이 평가기가 값을 못 내니, 다이제스트에는 수식 문자열이 보이고
    # `verify_formula_result`의 사후 검증도 그 자리에서 눈을 감았다.
    if name == "MATCH":
        target = scalar(args[0]) if args else None
        rng = args[1] if len(args) > 1 else None
        if not isinstance(rng, _Range):
            raise FormulaError("#N/A", "MATCH의 두 번째 인자는 범위여야 합니다")
        mode = int(_to_number(scalar(args[2]))) if len(args) > 2 else 1
        cells = list(ev.iter_range(rng))
        if mode == 0:
            for i, cell in enumerate(cells, start=1):
                if _compare(cell, target) == 0:
                    return float(i)
            raise FormulaError("#N/A", "MATCH: 찾는 값이 없습니다")
        # 근사 일치. 정렬돼 있다고 보고 조건을 만족하는 마지막/첫 위치를 찾는다.
        best = None
        for i, cell in enumerate(cells, start=1):
            if _is_blank(cell):
                continue
            if (mode > 0 and _compare(cell, target) <= 0) or (mode < 0 and _compare(cell, target) >= 0):
                best = i
        if best is None:
            raise FormulaError("#N/A", "MATCH: 조건을 만족하는 값이 없습니다")
        return float(best)

    if name == "INDEX":
        rng = args[0] if args else None
        if not isinstance(rng, _Range):
            raise FormulaError("#REF!", "INDEX의 첫 인자는 범위여야 합니다")
        height = rng.row2 - rng.row1 + 1
        width = rng.col2 - rng.col1 + 1
        row_n = int(_to_number(scalar(args[1]))) if len(args) > 1 else 0
        col_n = int(_to_number(scalar(args[2]))) if len(args) > 2 else 0
        # 한 줄짜리 범위는 인자 하나로 그 줄의 n번째를 가리킨다.
        if len(args) == 2:
            if height == 1:
                row_n, col_n = 1, row_n
            else:
                col_n = 1
        if not (1 <= row_n <= height) or not (1 <= col_n <= width):
            raise FormulaError("#REF!", "INDEX: 범위를 벗어났습니다")
        return ev.value(rng.sheet or ev._default_sheet, rng.row1 + row_n - 1, rng.col1 + col_n - 1)

    if name in ("VLOOKUP", "HLOOKUP"):
        target = scalar(args[0]) if args else None
        rng = args[1] if len(args) > 1 else None
        if not isinstance(rng, _Range):
            raise FormulaError("#N/A", f"{name}의 두 번째 인자는 범위여야 합니다")
        index = int(_to_number(scalar(args[2]))) if len(args) > 2 else 1
        approximate = _to_bool(scalar(args[3])) if len(args) > 3 else True
        sheet = rng.sheet or ev._default_sheet
        vertical = name == "VLOOKUP"
        span = (rng.row2 - rng.row1 + 1) if vertical else (rng.col2 - rng.col1 + 1)
        depth = (rng.col2 - rng.col1 + 1) if vertical else (rng.row2 - rng.row1 + 1)
        if not (1 <= index <= depth):
            raise FormulaError("#REF!", f"{name}: 인덱스가 범위를 벗어났습니다")
        best = None
        for offset in range(span):
            key = (
                ev.value(sheet, rng.row1 + offset, rng.col1)
                if vertical
                else ev.value(sheet, rng.row1, rng.col1 + offset)
            )
            cmp = _compare(key, target)
            if cmp == 0:
                best = offset
                break
            if approximate and not _is_blank(key) and cmp < 0:
                best = offset
        if best is None:
            raise FormulaError("#N/A", f"{name}: 찾는 값이 없습니다")
        return (
            ev.value(sheet, rng.row1 + best, rng.col1 + index - 1)
            if vertical
            else ev.value(sheet, rng.row1 + index - 1, rng.col1 + best)
        )

    if name == "XLOOKUP":
        target = scalar(args[0]) if args else None
        lookup = args[1] if len(args) > 1 else None
        result = args[2] if len(args) > 2 else None
        if not isinstance(lookup, _Range) or not isinstance(result, _Range):
            raise FormulaError("#N/A", "XLOOKUP의 찾을 범위와 결과 범위는 범위여야 합니다")
        keys = list(ev.iter_range(lookup))
        for i, key in enumerate(keys):
            if _compare(key, target) == 0:
                cells = list(ev.iter_range(result))
                if i < len(cells):
                    return cells[i]
                raise FormulaError("#REF!", "XLOOKUP: 결과 범위가 짧습니다")
        # 4번째 인자는 "못 찾았을 때" 값이다.
        if len(args) > 3:
            return scalar(args[3])
        raise FormulaError("#N/A", "XLOOKUP: 찾는 값이 없습니다")

    # ── 실무에서 흔한 나머지 ────────────────────────────────────────────
    if name == "MEDIAN":
        nums = sorted(_numbers(values, ev))
        if not nums:
            raise FormulaError("#NUM!", "MEDIAN: 숫자가 없습니다")
        mid = len(nums) // 2
        return nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2

    if name in ("STDEV", "STDEV.S", "STDEVP", "STDEV.P"):
        nums = _numbers(values, ev)
        sample = name in ("STDEV", "STDEV.S")
        if len(nums) < (2 if sample else 1):
            raise FormulaError("#DIV/0!", "STDEV: 표본이 부족합니다")
        mean = sum(nums) / len(nums)
        var = sum((x - mean) ** 2 for x in nums) / (len(nums) - 1 if sample else len(nums))
        return var ** 0.5

    if name == "SUMPRODUCT":
        columns = [_numbers([arg], ev) for arg in args]
        if not columns:
            return 0.0
        length = min(len(col) for col in columns)
        return float(sum(
            __import__("math").prod(col[i] for col in columns) for i in range(length)
        ))

    if name == "SUBTOTAL":
        code = int(_to_number(scalar(args[0]))) % 100 if args else 9
        rest = args[1:]
        mapping = {1: "AVERAGE", 2: "COUNT", 3: "COUNTA", 4: "MAX", 5: "MIN", 9: "SUM"}
        inner = mapping.get(code)
        if not inner:
            raise FormulaError("#VALUE!", f"SUBTOTAL 코드 {code}는 지원하지 않습니다")
        return _apply_function(inner, rest, ev)

    if name == "IFS":
        for i in range(0, len(args) - 1, 2):
            condition = args[i]
            if isinstance(condition, _Deferred):
                continue
            if _to_bool(scalar(condition)):
                return scalar(args[i + 1])
        raise FormulaError("#N/A", "IFS: 참인 조건이 없습니다")

    if name == "TEXTJOIN":
        sep = _to_text(scalar(args[0])) if args else ""
        skip_blank = _to_bool(scalar(args[1])) if len(args) > 1 else True
        parts = [_to_text(v) for v in _flatten(args[2:], ev)]
        if skip_blank:
            parts = [t for t in parts if t != ""]
        return sep.join(parts)

    if name == "SUBSTITUTE":
        text = _to_text(values[0]) if values else ""
        old = _to_text(values[1]) if len(values) > 1 else ""
        new = _to_text(values[2]) if len(values) > 2 else ""
        return text.replace(old, new) if old else text

    if name in ("RANK", "RANK.EQ"):
        target = _to_number(values[0]) if values else 0.0
        nums = _numbers([args[1]] if len(args) > 1 else [], ev)
        descending = True
        if len(args) > 2:
            descending = not _to_bool(scalar(args[2]))
        ordered = sorted(nums, reverse=descending)
        for i, x in enumerate(ordered, start=1):
            if x == target:
                return float(i)
        raise FormulaError("#N/A", "RANK: 값이 범위에 없습니다")

    if name == "EOMONTH":
        base = from_excel_serial(_to_number(values[0])) if values else None
        if base is None:
            raise FormulaError("#VALUE!", "EOMONTH: 날짜를 읽지 못했습니다")
        months = int(_to_number(values[1])) if len(values) > 1 else 0
        total = base.year * 12 + (base.month - 1) + months
        year, month = divmod(total, 12)
        month += 1
        return excel_serial(datetime(year, month, _days_in_month(year, month)))

    if name == "DATEDIF":
        start = from_excel_serial(_to_number(values[0])) if values else None
        end = from_excel_serial(_to_number(values[1])) if len(values) > 1 else None
        if start is None or end is None:
            raise FormulaError("#VALUE!", "DATEDIF: 날짜를 읽지 못했습니다")
        unit = _to_text(values[2]).upper() if len(values) > 2 else "D"
        if unit == "D":
            return float((end - start).days)
        if unit == "M":
            return float((end.year - start.year) * 12 + (end.month - start.month)
                         - (1 if end.day < start.day else 0))
        if unit == "Y":
            return float(end.year - start.year
                         - (1 if (end.month, end.day) < (start.month, start.day) else 0))
        raise FormulaError("#NUM!", f"DATEDIF 단위 {unit}는 지원하지 않습니다")

    raise FormulaError("#NAME?", f"지원하지 않는 함수: {name}")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day
