"""
파라미터 바인더 — 플래너가 낸 상징적 파라미터를 실제 워크북 좌표로 확정하는 레이어.

플래너(LLM·룰 어느 쪽이든)는 "금액 열 기준", "월을 행으로" 같은 말을 그대로 흘리거나
key_column=1 처럼 근거 없는 인덱스를 채워 넣는다. 그 값이 실행기까지 그냥 내려가면
엉뚱한 열이 정렬되고도 성공으로 보고된다.

이 모듈은 실행 직전에 다이제스트(실제 시트/머리글/값 후보)와 원문을 대조해
- 존재하지 않는 열 이름 → 원문이 가리키는 실제 머리글로 교정
- 숫자 인덱스 → 머리글 이름으로 교정
- output_sheet="피벗1!A1" 같은 오염된 값 → 시트명/시작셀로 분리
- 필터 값 누락 → 해당 열의 실제 값 후보에서 확정
를 수행하고, 끝내 못 정하는 슬롯은 unresolved로 보고해 되묻게 한다.

상태를 갖지 않는 순수 함수 모듈이며, 라우터는 bind_plan_steps / resolve_sheet_from_message만 쓴다.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .excel_formula_builder import build_formula, parse_named_formula
from .excel_header_lexicon import find_header_mentions, resolve_header
from .excel_live_executor import PlanStep
from .korean_number import parse_condition

# 액션별로 "열을 가리키는" 파라미터. 값은 단일 슬롯 이름.
_COLUMN_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    "excel_live.filter_rows": ("column",),
    "excel_live.pivot_table": ("row_field", "column_field", "value_field"),
    "excel_live.sort_rows": ("column",),
    "excel_live.drop_column": ("column",),
    "excel_live.rename_column": ("column",),
    "excel_live.group_by_aggregate": ("group_column", "value_column"),
    "excel_live.calculate_column_stat": ("column",),
}
# 열 목록을 받는 파라미터.
_COLUMN_LIST_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.dedupe_rows": ("key_columns",),
    "excel_live.find_duplicates": ("key_columns",),
}
# 기준 열을 못 정해도 전체 열로 점검하면 되는 액션. 되묻지 않는다.
_OPTIONAL_COLUMN_LIST_ACTIONS = {"excel_live.find_duplicates"}
# 없으면 실행기·검증기가 임의 기본값(1번 열)으로 채워버리는 필수 열 슬롯.
_REQUIRED_COLUMN_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    "excel_live.filter_rows": ("column",),
}
# 기준 열을 잘못 잡으면 데이터가 조용히 뒤섞이는 슬롯.
# 원문이 기준을 말하지 않았다면 플래너가 채운 값이 그럴듯해도 믿지 않는다.
_REQUIRE_EXPLICIT_COLUMN: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    "excel_live.dedupe_rows": ("key_columns",),
}
# 결과를 새 시트에 쓰는 액션. output_sheet 오염을 정리한다.
_OUTPUT_SHEET_ACTIONS = {
    "excel_live.pivot_table",
    "excel_live.forecast_linear",
    "excel_live.compare_ranges",
    "excel_live.consolidate_sheets",
    "excel_live.consolidate_workbooks_from_folder",
    "excel_live.create_chart",
}

_SHEET_MENTION_PATTERN = re.compile(r"([A-Za-z0-9가-힣_]{1,20})\s*(?:시트|sheet)", re.IGNORECASE)
_REFERENCE_CONTEXT = re.compile(r"(참조표|참조|참고표|조회표는|기준표|룩업|lookup|reference)\s*(?:는|은|을|를|:)?\s*$")
_COLUMN_LETTER_MENTION = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\s*열")
_CELL_REF_PATTERN = re.compile(r"^[A-Z]{1,3}\d{1,7}$")
_COLUMN_LETTER_ONLY = re.compile(r"^[A-Za-z]{1,3}$")
_JOSA = ("으로", "로서", "에서", "들을", "를", "을", "은", "는", "이", "가", "의", "로", "별")


def _strip_josa(text: str) -> str:
    value = str(text or "").strip()
    for josa in _JOSA:
        if len(value) > len(josa) and value.endswith(josa):
            return value[: -len(josa)]
    return value


def sheet_entry(digest: dict[str, Any], sheet_name: str | None) -> dict[str, Any]:
    sheets = digest.get("sheets") or []
    target = str(sheet_name or digest.get("active_sheet") or "").strip()
    for sheet in sheets:
        if str(sheet.get("name") or "") == target:
            return sheet
    return sheets[0] if sheets else {}


def sheet_names(digest: dict[str, Any]) -> list[str]:
    return [str(sheet.get("name") or "") for sheet in (digest.get("sheets") or [])]


def _headers(entry: dict[str, Any]) -> list[str]:
    return [str(col.get("header") or "") for col in (entry.get("columns") or []) if col.get("header")]


def _column_meta(entry: dict[str, Any], header: str) -> dict[str, Any]:
    for col in entry.get("columns") or []:
        if str(col.get("header") or "").strip() == str(header or "").strip():
            return col
    return {}


def resolve_sheet_from_message(
    message: str,
    digest: dict[str, Any],
    *,
    default: str | None,
) -> str | None:
    """원문이 "매출 시트"처럼 명시한 시트가 실제로 있으면 그 시트를 작업 대상으로 삼는다.

    여러 시트가 언급되면 첫 번째(=보통 원본)를 고른다. 결과를 쓰는 시트는 output_sheet가 따로 받는다.
    "참조표는 조회표 시트 A:B"처럼 참조 대상으로 불린 시트는 작업 대상이 아니므로 건너뛴다 —
    그렇지 않으면 VLOOKUP 결과가 원본이 아닌 참조표 시트에 써진다.
    """
    text = str(message or "")
    names = {name for name in sheet_names(digest) if name}
    if not names:
        return default
    for match in _SHEET_MENTION_PATTERN.finditer(text):
        candidate = _strip_josa(match.group(1))
        if candidate not in names:
            continue
        if _REFERENCE_CONTEXT.search(text[max(0, match.start() - 12) : match.start()]):
            continue
        return candidate
    return _sheet_called_by_its_korean_name(text, names) or default


# 영문 시트명을 한국어로 부를 때 쓰는 말. 시트 이름은 거의 이 낱말들의 조합이다.
_SHEET_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "project": ("프로젝트", "과제"),
    "plan": ("계획", "일정"),
    "sales": ("매출", "판매", "영업"),
    "data": ("데이터", "자료"),
    "inventory": ("재고",),
    "stock": ("재고",),
    "lookup": ("조회", "참조", "코드"),
    "summary": ("요약", "집계"),
    "report": ("보고", "리포트"),
    "dashboard": ("대시보드", "현황"),
    "customer": ("고객", "거래처"),
    "order": ("주문", "발주"),
    "product": ("제품", "품목", "상품"),
    "budget": ("예산",),
    "cost": ("원가", "비용"),
    "chart": ("차트", "그래프"),
}
_SHEET_NAME_TOKENS = re.compile(r"[A-Za-z가-힣0-9]+")


def _sheet_called_by_its_korean_name(text: str, names: set[str]) -> str | None:
    """ "프로젝트 계획에서 ..." — '시트'라는 낱말 없이 한국어로 시트를 부른 경우.

    이 말을 놓치면 활성 시트(대개 Sales_Data)에 그대로 작업이 떨어진다. Project_Plan을
    걸러야 할 필터가 매출 데이터를 지우는 식이라, 실행은 성공하고 결과만 틀린다.

    오인식이 더 위험하므로 시트명을 이루는 모든 낱말이 원문에 있을 때만 인정한다.
    낱말이 하나뿐인 이름(Inventory)은 "<이름> 시트" 규칙이 이미 처리한다.
    """
    lowered = str(text or "").lower()
    for name in sorted(names, key=len, reverse=True):
        tokens = [t.lower() for t in _SHEET_NAME_TOKENS.findall(name) if len(t) > 1]
        if len(tokens) < 2:
            continue
        if all(
            token in lowered or any(alias in text for alias in _SHEET_NAME_ALIASES.get(token, ()))
            for token in tokens
        ):
            return name
    return None


def header_mentions(message: str, entry: dict[str, Any], digest: dict[str, Any]) -> list[dict[str, Any]]:
    """원문의 머리글 언급을 위치까지 포함해 돌려준다(시트명으로 쓰인 언급은 제외).

    "매출이익 나누기 매출"처럼 한국어 개념어로만 부른 경우도 사전을 통해 실제 머리글에 잇는다.
    """
    text = str(message or "")
    names = set(sheet_names(digest))
    rows: list[dict[str, Any]] = []
    for hit in find_header_mentions(text, _headers(entry)):
        tail = text[hit["end"] : hit["end"] + 6]
        if hit["header"] in names and re.match(r"\s*(?:시트|sheet)", tail, re.IGNORECASE):
            continue
        rows.append(hit)
    return rows


def mentioned_headers(message: str, entry: dict[str, Any], digest: dict[str, Any]) -> list[str]:
    """원문에 등장한 실제 머리글을 등장 순서대로 돌려준다."""
    return [hit["header"] for hit in header_mentions(message, entry, digest)]


def _resolve_column_value(
    value: Any,
    *,
    entry: dict[str, Any],
    candidates: list[str],
    used: set[str],
) -> tuple[Any, str]:
    """열 슬롯 하나를 실제 머리글로 확정한다. (확정값, 사유)를 돌려준다."""
    headers = _headers(entry)
    if isinstance(value, str):
        text = value.strip()
        if text and text in headers:
            return text, "kept"
        if text and _COLUMN_LETTER_ONLY.match(text):
            return text.upper(), "kept_letter"
        if text:
            # 플래너는 영문 시트에도 "매출"이라고 쓴다. 개념 사전으로 실제 머리글에 잇는다.
            mapped = resolve_header(text, headers)
            if mapped:
                return mapped, "lexicon"
    for candidate in candidates:
        if candidate not in used:
            return candidate, "from_message"
    return value, "unresolved"


def _pick_role_column(message: str, headers: list[str], markers: tuple[str, ...]) -> str | None:
    """ "월을 행으로", "채널을 행으로" 처럼 역할 단서 바로 앞에 오는 머리글을 고른다."""
    text = str(message or "")
    best: tuple[int, str] | None = None
    for hit in find_header_mentions(text, headers):
        tail = text[hit["end"] : hit["end"] + 8]
        for marker in markers:
            if re.match(rf"\s*(?:을|를|은|는|이|가)?\s*{re.escape(marker)}", tail):
                if best is None or hit["start"] < best[0]:
                    best = (hit["start"], hit["header"])
                break
    return best[1] if best else None


# "매출 데이터를 날짜순으로" — 앞의 '매출'은 대상을 부르는 말이지 정렬 기준이 아니다.
_DATASET_LABELS = ("데이터", "자료", "표", "목록", "리스트", "시트", "파일", "내역")
_SORT_ORDER_MARKER = re.compile(r"오름차순|내림차순|정렬|순으로|순서대로|큰\s*순|작은\s*순|최신순|과거순|순위")

# "A1:L37" 처럼 원문이 직접 말한 범위. 이게 없으면 플래너가 지어낸 범위를 믿으면 안 된다.
_EXPLICIT_RANGE_MENTION = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}")

# 표 전체를 다루는 작업들 — 범위를 잘못 좁히면 일부 행만 집계·정렬돼 조용히 틀린 결과가 된다.
_WHOLE_TABLE_RANGE_SLOTS = {
    "excel_live.pivot_table": "source_range",
    "excel_live.sort_range": "target_range",
}


def _pick_sort_key(message: str, headers: list[str]) -> str | None:
    """정렬 기준 열을 고른다. 정렬어 바로 앞에 붙은 열이 기준이다.

    등장 순서로 첫 열을 집으면 "매출 데이터를 날짜 오름차순으로 정렬"에서 매출로 정렬해
    데이터가 통째로 뒤섞인다. 되돌릴 수는 있어도 사용자가 원한 결과는 아니다.
    """
    text = str(message or "")
    mentions = find_header_mentions(text, headers)
    if not mentions:
        return None

    def is_dataset_label(hit: dict[str, Any]) -> bool:
        tail = text[hit["end"] : hit["end"] + 8].lstrip()
        return any(tail.startswith(word) for word in _DATASET_LABELS)

    ranked = [hit for hit in mentions if not is_dataset_label(hit)] or mentions
    marker = _SORT_ORDER_MARKER.search(text)
    if marker:
        before = [hit for hit in ranked if hit["start"] < marker.start()]
        if before:
            return before[-1]["header"]
    return ranked[0]["header"]


# 집계 기준을 가리키는 조사. "지역별", "제품 분류마다", "고객당" 모두 같은 뜻이다.
_GROUP_MARKER = re.compile(r"\s*(?:별|마다|당)")
# 무엇을 더할지 가리키는 말. "매출 합계"뿐 아니라 "매출이 얼마나 나오는지"도 같은 요청이다.
_MEASURE_MARKER = re.compile(
    r"\s*(?:을|를|은|는|이|가|의)?\s*(?:합계|합|총액|총합|평균|개수|건수|카운트|얼마|실적|규모)"
)


def _bind_pivot(params: dict[str, Any], *, message: str, entry: dict[str, Any]) -> list[str]:
    headers = _headers(entry)
    notes: list[str] = []
    text = str(message or "")
    source_sheet = str(entry.get("name") or "").strip()
    if source_sheet and not str(params.get("source_sheet") or "").strip():
        # 결과 시트를 먼저 만드는 계획이면 활성 시트가 그쪽으로 옮겨간다.
        # 집계 원본은 지금 확정해 둬야 빈 시트를 읽고 실패하지 않는다.
        params["source_sheet"] = source_sheet
        notes.append(f"source_sheet={source_sheet}")
    mentions = find_header_mentions(text, headers)
    row_field = _pick_role_column(message, headers, ("행",))
    col_field = _pick_role_column(message, headers, ("열",))
    if not row_field:
        # "지역별 매출 합계" — 'X별'은 거의 언제나 집계 기준(행)이다.
        # "제품 분류마다"처럼 '별' 대신 '마다/당'을 쓰는 사람도 그만큼 많다.
        for hit in mentions:
            if _GROUP_MARKER.match(text[hit["end"] : hit["end"] + 3]):
                row_field = hit["header"]
                break
    if not row_field:
        # 집계 표지가 없어도 언급된 비숫자 열이 하나면 그게 기준이다.
        # 여기서 못 정하면 플래머가 지어낸 Order_ID로 묶여 180행짜리 "요약"이 나온다.
        text_only = [
            hit["header"]
            for hit in mentions
            if hit["header"] != col_field and not _column_meta(entry, hit["header"]).get("numeric")
        ]
        if len(set(text_only)) == 1:
            row_field = text_only[0]
    value_field = None
    for hit in mentions:
        header = hit["header"]
        if header == row_field or header == col_field:
            continue
        if not _column_meta(entry, header).get("numeric"):
            continue
        if _MEASURE_MARKER.match(text[hit["end"] : hit["end"] + 8]):
            value_field = header
            break
    if value_field is None:
        # "지역별 매출을 집계" 처럼 집계어가 붙지 않아도, 언급된 숫자 열이 하나면 그게 값이다.
        numeric_mentions = [
            hit["header"]
            for hit in mentions
            if hit["header"] not in {row_field, col_field}
            and _column_meta(entry, hit["header"]).get("numeric")
        ]
        if len(numeric_mentions) == 1:
            value_field = numeric_mentions[0]
    if row_field:
        params["row_field"] = row_field
        notes.append(f"row_field={row_field}")
    if col_field and col_field != row_field:
        params["column_field"] = col_field
        notes.append(f"column_field={col_field}")
    if value_field:
        params["value_field"] = value_field
        notes.append(f"value_field={value_field}")
    if re.search(r"평균", message):
        params["agg"] = "avg"
    elif re.search(r"개수|건수|카운트", message):
        params["agg"] = "count"
    elif re.search(r"합계|합", message):
        params["agg"] = "sum"
    return notes


def _bind_filter_value(params: dict[str, Any], *, message: str, entry: dict[str, Any]) -> list[str]:
    """ "완료된 건만" 처럼 값만 말한 필터를 실제 셀 값으로 확정한다.

    플래너가 값을 채웠더라도 시트에 없는 값("완료된")이면 필터 결과가 0행이 된다.
    실제 셀 값 목록에 있는 값이 원문에 있으면 그쪽이 항상 옳다.
    """
    notes: list[str] = []
    text = str(message or "")
    numeric = re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만)", text)
    if numeric:
        op_map = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<"}
        params["operator"] = op_map.get(numeric.group(2), ">=")
        params["value"] = float(numeric.group(1))
        notes.append(f"value={params['value']}")
        return notes

    current_value = params.get("value")
    if current_value not in (None, "") and not isinstance(current_value, str):
        return notes

    known_values = {
        str(category)
        for col in entry.get("columns") or []
        for category in col.get("categories") or []
        if category
    }
    if current_value and str(current_value) in known_values:
        notes.extend(_bind_filter_column_from_value(params, entry=entry))
        return notes

    for col in entry.get("columns") or []:
        for category in col.get("categories") or []:
            if category and category in text:
                params["column"] = str(col.get("header") or params.get("column") or "")
                params["operator"] = params.get("operator") or "=="
                params["value"] = category
                notes.append(f"column={params['column']},value={category}")
                return notes
    return notes


def _bind_filter_column_from_value(params: dict[str, Any], *, entry: dict[str, Any]) -> list[str]:
    """값이 어느 한 열에만 있으면 그 열을 기준 열로 삼는다.

    "완료된 것만 남겨줘"는 기준 열을 말하지 않았지만 '완료'가 상태 열에만 있다면
    되물을 것이 없다. 두 열 이상에 있으면 정하지 않고 남겨서 되묻게 한다.
    """
    if str(params.get("column") or "").strip():
        return []
    value = str(params.get("value") or "")
    if not value:
        return []
    owners = [
        str(col.get("header") or "")
        for col in entry.get("columns") or []
        if value in {str(category) for category in col.get("categories") or []}
    ]
    owners = [name for name in owners if name]
    if len(owners) != 1:
        return []
    params["column"] = owners[0]
    return [f"column={owners[0]}"]


_EXCLUSION_VERB = re.compile(r"(빼|제외|없애|제거|지워|지우|삭제|말고|아닌|제하고|빠뜨)", re.IGNORECASE)


def _bind_filter_mode(params: dict[str, Any], *, message: str) -> list[str]:
    """ "남길 것"과 "뺄 것"을 가른다.

    플래너는 "취소된 주문은 빼줘"를 operator="==", value="취소"로 옮겨 놓는다. 그대로 실행하면
    취소 건만 남기고 나머지를 전부 지우는, 요청과 정반대의 편집이 된다.

    판정은 값 바로 뒤에 "만"이 붙었는지로 한다. "완료된 건만 남겨줘"는 포함,
    "취소된 주문은 빼고 나머지만 남겨줘"는 값 뒤가 "된 주문은"이라 제외로 읽는다.
    """
    text = str(message or "")
    value = str(params.get("value") or "").strip()
    if not text or not value or not _EXCLUSION_VERB.search(text):
        return []
    position = text.find(value)
    if position >= 0 and "만" in text[position + len(value) : position + len(value) + 8]:
        return []
    if str(params.get("mode") or "") == "remove":
        return []
    params["mode"] = "remove"
    return ["mode=remove"]


_WRITE_VALUE_PATTERN = re.compile(
    r"([A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?)"
    r"\s*(?:셀|범위)?\s*에\s*(.+?)\s*(?:입력|기입|써|쓰|넣|적어|set|write)",
    re.IGNORECASE,
)

_CELL_REF_PATTERN = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")


def _column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _range_shape(ref: str) -> tuple[str, int, int] | None:
    """ "E2:G2" → ("E2", 1, 3). 단일 셀이면 (셀, 1, 1).

    시작 셀은 항상 좌상단으로 정규화한다. "G2:E2"처럼 거꾸로 적어도 E2가 기준이 돼야
    값이 범위 밖으로 밀려나지 않는다.
    """
    parts = ref.upper().split(":")
    head = _CELL_REF_PATTERN.match(parts[0])
    if not head:
        return None
    if len(parts) == 1:
        return parts[0], 1, 1
    tail = _CELL_REF_PATTERN.match(parts[1])
    if not tail:
        return None

    head_col, tail_col = _column_index(head.group(1)), _column_index(tail.group(1))
    head_row, tail_row = int(head.group(2)), int(tail.group(2))
    left_letters = head.group(1) if head_col <= tail_col else tail.group(1)
    return (
        f"{left_letters}{min(head_row, tail_row)}",
        abs(tail_row - head_row) + 1,
        abs(tail_col - head_col) + 1,
    )


def _shape_write_values(raw: str, row_count: int, col_count: int) -> list[list[Any]]:
    """원문의 값 부분을 대상 범위 모양에 맞춘 2차원 배열로 만든다.

    단일 셀이면 콤마를 건드리지 않는다 — "C3에 1,000 입력"의 천 단위 구분자를
    구분자로 오해해 쪼개면 값이 1과 0으로 망가진다. 범위가 명시됐을 때만
    콤마를 값 구분자로 본다.
    """
    if row_count == 1 and col_count == 1:
        literal = _coerce_literal(raw)
        return [[literal]] if literal != "" else []

    parts = [_coerce_literal(part) for part in raw.split(",")]
    parts = [part for part in parts if part != ""]
    if not parts:
        return []
    if col_count == 1:
        return [[part] for part in parts]
    if row_count == 1:
        return [parts]
    return [parts[index : index + col_count] for index in range(0, len(parts), col_count)]


def _coerce_literal(text: str) -> Any:
    value = str(text or "").strip().strip("'\"")
    if not value:
        return value
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _bind_write_values(params: dict[str, Any], *, message: str) -> list[str]:
    """ "C3에 120 입력해줘"처럼 값이 원문에만 있는 쓰기 명령을 채운다.

    플래너는 이 경우 values_2d를 빠뜨리기 쉬운데, 그러면 검증기에서 막혀
    가장 단순한 명령이 되묻기로 떨어진다.
    """
    existing = params.get("values_2d")
    match = _WRITE_VALUE_PATTERN.search(str(message or ""))
    if not match:
        return []
    shape = _range_shape(match.group(1))
    if shape is None:
        return []
    top_left, row_count, col_count = shape
    existing_cells = 0
    if isinstance(existing, list) and existing:
        existing_cells = sum(len(r) if isinstance(r, list) else 1 for r in existing)
        # 값이 이미 있어도 원문이 지목한 범위를 다 못 채우면 그대로 둘 수 없다.
        # "A1:A3에 총매출,총이익,평균주문금액 입력"에 값 하나만 실려 오면
        # 나머지 두 칸은 아무 말 없이 빈 채로 남는다.
        if existing_cells >= row_count * col_count:
            return []
    values = _shape_write_values(match.group(2), row_count, col_count)
    if not values:
        return []
    if existing_cells and sum(len(row) for row in values) <= existing_cells:
        # 원문에서 다시 뽑아도 나아지지 않으면 플래너가 준 값을 존중한다.
        return []
    params["values_2d"] = values
    # 원문에 범위가 명시됐으면 그 좌상단이 항상 옳다. 플래너가 범위의 끝 셀을
    # 시작으로 잡아 오는 경우가 있는데, 두면 값이 범위 밖으로 밀려 쓰인다.
    start_cell = str(params.get("start_cell") or "").strip().upper()
    is_placeholder = not start_cell or start_cell in {"__ACTIVE_CELL__", "__ACTIVE_SELECTION__"}
    if is_placeholder or row_count > 1 or col_count > 1:
        params["start_cell"] = top_left
    return [f"values_2d={values}"]


_OUTPUT_SHEET_TARGET_PATTERN = re.compile(
    r"([A-Za-z0-9가-힣_]{1,20})\s*(?:시트|sheet)\s*(?:으로|로|에)", re.IGNORECASE
)


def _bind_consolidate(params: dict[str, Any], *, message: str, digest: dict[str, Any]) -> list[str]:
    """ "1분기,2분기,3분기 시트를 통합1 시트로" 에서 원본/결과 시트를 갈라낸다."""
    known = [name for name in sheet_names(digest) if name]
    if not known:
        return []
    text = str(message or "")
    changes: list[str] = []

    output = str(params.get("output_sheet") or "").strip()
    if not output or output in known:
        for match in _OUTPUT_SHEET_TARGET_PATTERN.finditer(text):
            candidate = match.group(1)
            if candidate not in known:
                output = candidate
                params["output_sheet"] = candidate
                changes.append(f"output_sheet={candidate}")
                break

    sources = params.get("source_sheets")
    valid_sources = [s for s in sources if s in known] if isinstance(sources, list) else []
    if not valid_sources:
        found = sorted(
            ((text.find(name), name) for name in known if name != output and name in text),
            key=lambda row: row[0],
        )
        valid_sources = [name for pos, name in found if pos >= 0]
    if valid_sources and valid_sources != sources:
        params["source_sheets"] = valid_sources
        changes.append(f"source_sheets={valid_sources}")
    return changes


def _normalize_output_sheet(
    params: dict[str, Any], *, message: str = "", source_sheet: str = "", action: str = ""
) -> list[str]:
    """결과를 쓸 시트를 확정한다.

    - "피벗1!A1"처럼 시트명과 셀이 붙어 온 값을 분리한다.
    - 원문이 "Region_Chart 시트에"라고 결과 시트를 지목했으면 그 이름을 쓴다.
    - 결과 시트가 원본 시트와 같으면 원본을 덮어쓰게 되므로 다른 이름으로 옮긴다.
    """
    changes: list[str] = []
    raw = params.get("output_sheet")
    if isinstance(raw, str) and "!" in raw:
        sheet_part, _, cell_part = raw.partition("!")
        sheet_part = sheet_part.strip().strip("'")
        cell_part = cell_part.strip().upper()
        if sheet_part:
            params["output_sheet"] = sheet_part
            if _CELL_REF_PATTERN.match(cell_part) and "output_start" in params:
                params["output_start"] = cell_part
            changes.append(f"output_sheet={sheet_part}")

    named = _result_sheet_from_message(message)
    if action == "excel_live.create_chart" and not named:
        # 차트는 표를 덮어쓰지 않는 오버레이라 소스 시트에 그대로 붙이면 된다.
        # 집계 계열과 달리 별도 시트로 뺄 이유가 없는데, 플래너가 "Rep_Chart" 같은
        # 이름을 지어내면 사용자가 보던 시트에는 아무것도 안 생기고 낯선 시트만 하나 는다.
        if params.pop("output_sheet", None) is not None:
            changes.append("output_sheet=소스 시트")
        return changes
    if named and str(params.get("output_sheet") or "").strip() != named:
        params["output_sheet"] = named
        changes.append(f"output_sheet={named}")

    current = str(params.get("output_sheet") or "").strip()
    if current and source_sheet and current == source_sheet:
        # 집계 결과를 원본 시트에 쓰면 원본 데이터가 지워진다.
        params["output_sheet"] = f"{source_sheet}_집계"
        changes.append(f"output_sheet={params['output_sheet']}")

    changes.extend(_normalize_output_start(params, message=message))
    return changes


def _normalize_output_start(params: dict[str, Any], *, message: str = "") -> list[str]:
    """결과 시트의 시작 셀은 원문이 지목하지 않는 한 A1이다.

    플래너가 근거 없이 `output_start="B1"`을 채우면 결과표가 한 칸 밀려 A열이 빈다.
    실행은 성공으로 보고되지만 이후 차트·수식이 A열을 참조해 조용히 어긋난다.
    """
    if "output_start" not in params:
        return []
    current = str(params.get("output_start") or "").strip().upper()
    if not current or current == "A1":
        return []
    if _CELL_REF_PATTERN.match(current) and current in _mentioned_cell_refs(message):
        return []
    params["output_start"] = "A1"
    return [f"output_start=A1(was {current})"]


_CELL_REF_MENTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{1,7})(?![A-Za-z0-9])")


def _mentioned_cell_refs(message: str) -> set[str]:
    return {match.group(1).upper() for match in _CELL_REF_MENTION.finditer(str(message or ""))}


_RESULT_SHEET_MENTION = re.compile(
    r"([A-Za-z0-9가-힣_]{1,24})\s*(?:시트|sheet)\s*(?:에|로|에다|으로)", re.IGNORECASE
)


def _result_sheet_from_message(message: str) -> str:
    """ "Region_Chart 시트에 정리해줘" — 결과를 담을 시트 이름을 원문에서 찾는다."""
    match = _RESULT_SHEET_MENTION.search(str(message or ""))
    if not match:
        return ""
    name = _strip_josa(match.group(1))
    return name if name and name not in {"새", "다른", "이", "그"} else ""


_COLOR_WORDS: tuple[tuple[str, str], ...] = (
    ("빨", "#FF0000"),
    ("적색", "#FF0000"),
    ("red", "#FF0000"),
    ("주황", "#FFA500"),
    ("노랑", "#FFFF00"),
    ("노란", "#FFFF00"),
    ("yellow", "#FFFF00"),
    ("초록", "#00B050"),
    ("녹색", "#00B050"),
    ("green", "#00B050"),
    ("파랑", "#0070C0"),
    ("파란", "#0070C0"),
    ("blue", "#0070C0"),
    ("회색", "#D9D9D9"),
    ("연회색", "#F2F2F2"),
    ("gray", "#D9D9D9"),
    ("grey", "#D9D9D9"),
)
_COLUMN_ONLY_MENTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*열")
_CONDITION_FORMAT_ACTIONS = {"excel_live.highlight_by_condition", "excel_live.fill_range"}
_CONDITION_OPERATORS: dict[str, str] = {
    "이상": ">=",
    "초과": ">",
    "넘는": ">",
    "이하": "<=",
    "미만": "<",
    "이면": "==",
}
_CONDITION_WORD = re.compile("(" + "|".join(_CONDITION_OPERATORS) + ")")


def _split_range_prefix(range_ref: str) -> tuple[str, str]:
    """`PROJECT_PLAN!I2:I21` → (`PROJECT_PLAN!`, `I2:I21`). 시트 접두어를 보존하기 위해."""
    if "!" not in range_ref:
        return "", range_ref
    sheet, _, ref = range_ref.rpartition("!")
    return f"{sheet}!", ref


def _retarget_sheet_by_headers(
    text: str,
    entry: dict[str, Any] | None,
    digest: dict[str, Any] | None,
    prefix: str,
) -> tuple[dict[str, Any] | None, str]:
    """원문이 부른 열을 가장 많이 가진 시트로 대상을 옮긴다.

    "재고가 재주문점 이하인 제품" 은 Inventory 시트 이야기인데, 활성 시트가 Sales_Data면
    엉뚱한 열을 칠하고 0건으로 끝난다. "제품" 하나만 겹쳐도 활성 시트에 눌러앉지 않도록
    겹치는 열 개수로 고른다. 최고점이 하나가 아니면 옮기지 않는다 — 추측은 위험하다.
    """
    if not digest:
        return entry, prefix
    scored = [
        (len({hit["header"] for hit in find_header_mentions(text, _headers(sheet))}), sheet)
        for sheet in (digest.get("sheets") or [])
        if _headers(sheet)
    ]
    if not scored:
        return entry, prefix
    best = max(score for score, _ in scored)
    leaders = [sheet for score, sheet in scored if score == best]
    if best == 0 or len(leaders) != 1:
        return entry, prefix
    target = leaders[0]
    if target is entry:
        return entry, prefix
    return target, f"{target.get('name')}!"


_OPERATOR_EXPRESSION = re.compile(r"^\s*(.+?)\s*(<=|>=|<>|!=|<|>|==?)\s*(.+?)\s*$")


def _normalize_operator_expression(
    params: dict[str, Any],
    *,
    entry: dict[str, Any] | None,
    prefix: str,
    digest: dict[str, Any] | None = None,
) -> list[str]:
    """operator에 식을 통째로 넣어 오는 경우를 풀어 준다.

    플래너가 `operator: "Current_Stock < Reorder_Point"` 처럼 답할 때가 있다. 그대로
    넘기면 "지원하지 않는 연산자"로 죽는다. 양쪽이 열 이름이면 열 비교로, 오른쪽이
    숫자면 임계값 비교로 바꾼다. 식에 나온 열을 가진 시트를 직접 찾으므로 활성 시트가
    달라도 된다.
    """
    match = _OPERATOR_EXPRESSION.match(str(params.get("operator") or "").strip())
    if not match:
        return []
    left_name, symbol, right_name = match.group(1), match.group(2), match.group(3)
    symbol = {"==": "==", "=": "==", "!=": "<>"}.get(symbol, symbol)

    resolved = _sheet_owning_columns(left_name, right_name, entry, digest)
    if resolved is None:
        return []
    owner, left_letter, right_letter = resolved
    if owner is not entry and owner.get("name"):
        prefix = f"{owner['name']}!"

    params["operator"] = symbol
    params["target_range"] = f"{prefix}{left_letter}:{left_letter}"
    changes = [f"operator={symbol}", f"target_range={prefix}{left_letter}:{left_letter}"]

    if right_letter:
        params["compare_column"] = right_letter
        params.setdefault("threshold", 0)
        changes.append(f"compare_column={right_letter}")
        return changes
    try:
        params["threshold"] = float(str(right_name).replace(",", "").replace("%", ""))
        changes.append(f"threshold={params['threshold']}")
    except ValueError:
        pass
    return changes


def _sheet_owning_columns(
    left_name: str,
    right_name: str,
    entry: dict[str, Any] | None,
    digest: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, str] | None:
    """식의 왼쪽(그리고 가능하면 오른쪽) 열을 실제로 가진 시트를 찾는다.

    양쪽을 다 가진 시트를 우선한다. "제품" 같은 흔한 낱말이 다른 시트 머리글에
    우연히 걸려 엉뚱한 시트로 가는 걸 막기 위해서다.
    """
    candidates: list[dict[str, Any]] = []
    if entry:
        candidates.append(entry)
    for sheet in (digest or {}).get("sheets") or []:
        if sheet is not entry and _headers(sheet):
            candidates.append(sheet)

    partial: tuple[dict[str, Any], str, str] | None = None
    for candidate in candidates:
        headers = _headers(candidate)
        left_letter = _letter_for(candidate, resolve_header(left_name, headers))
        if not left_letter:
            continue
        right_letter = _letter_for(candidate, resolve_header(right_name, headers))
        if right_letter:
            return candidate, left_letter, right_letter
        if partial is None:
            partial = (candidate, left_letter, "")
    return partial


def _letter_for(entry: dict[str, Any], header: str | None) -> str:
    if not header:
        return ""
    return str(_column_meta(entry, header).get("letter") or "").upper()


def _bind_condition_format(
    params: dict[str, Any],
    *,
    message: str,
    entry: dict[str, Any] | None = None,
    digest: dict[str, Any] | None = None,
) -> list[str]:
    """ "매출이 10만 원 미만이면 빨간색" 처럼 원문에만 있는 대상 열·색·임계값을 채운다.

    플래너는 target_range를 A:Z로 뭉뚱그리거나, 반대로 `Project_Plan!J2:J21`처럼
    그럴듯하지만 틀린 열을 지어낸다. 원문이 열을 이름으로 불렀다면 그쪽이 최종 근거다.
    """
    changes: list[str] = []
    text = str(message or "")

    current = str(params.get("target_range") or "").strip().upper()
    prefix, current = _split_range_prefix(current)
    # 원문이 말한 열 이름이 다른 시트에만 있으면 그 시트로 옮긴다("진행률"은 Project_Plan에 있다).
    entry, prefix = _retarget_sheet_by_headers(text, entry, digest, prefix)
    broad = current in {"", "__ACTIVE_SELECTION__", "A:Z", "A:XFD"}
    letter = ""
    column = _COLUMN_ONLY_MENTION.search(text)
    if column:
        letter = column.group(1).upper()
    elif entry:
        # "매출이 10만 원 미만" — 열을 이름으로 불렀으면 그 열의 문자로 좁힌다.
        for hit in find_header_mentions(text, _headers(entry)):
            meta = _column_meta(entry, hit["header"])
            if meta.get("numeric") and meta.get("letter"):
                letter = str(meta["letter"]).upper()
                break
    # 원문이 범위를 직접 말하지 않았다면 플래너가 좁혀 놓은 범위를 믿지 않는다.
    trusted = bool(_EXPLICIT_RANGE_MENTION.search(text))
    if letter and (broad or not trusted):
        params["target_range"] = f"{prefix}{letter}:{letter}"
        changes.append(f"target_range={prefix}{letter}:{letter}")
    elif prefix and current:
        params["target_range"] = f"{prefix}{current}"

    lowered = text.lower()
    for word, hex_code in _COLOR_WORDS:
        if word in lowered:
            if str(params.get("fill_color") or "").upper() != hex_code:
                params["fill_color"] = hex_code
                changes.append(f"fill_color={hex_code}")
            break

    # "현재고가 재주문점 이하" — 기준이 숫자가 아니라 같은 행의 다른 열인 경우.
    comparison = _column_comparison(text, entry)
    if comparison:
        left, right, operator = comparison
        params["target_range"] = f"{prefix}{left}:{left}"
        params["compare_column"] = right
        params["operator"] = operator
        params.setdefault("threshold", 0)
        changes.extend(
            [f"target_range={prefix}{left}:{left}", f"compare_column={right}", f"operator={operator}"]
        )
        return changes

    changes.extend(_normalize_operator_expression(params, entry=entry, prefix=prefix, digest=digest))

    # 플래너는 같은 문장에도 '>=', 'greater_than', {'comparator': 'gt'} 를 번갈아 낸다.
    # 비교 방향과 기준값은 사용자가 이미 말했으므로 원문을 최종 근거로 삼는다.
    parsed = parse_condition(text)
    if parsed:
        operator, threshold, percent = parsed
        if str(params.get("operator") or "") != operator:
            params["operator"] = operator
            changes.append(f"operator={operator}")
        if percent and _looks_like_ratio_column(entry, letter):
            # 진행률이 0.8로 저장된 시트에 80을 넣으면 아무 행도 걸리지 않는다.
            threshold = threshold / 100.0
        params["threshold"] = threshold
        changes.append(f"threshold={threshold}")
    return changes


_COMPARISON_WORDS: dict[str, str] = {
    "이하": "<=",
    "이상": ">=",
    "미만": "<",
    "초과": ">",
    # "보다 적거나 같은"은 "같"에 먼저 걸려 ==로 새기 쉽다. 긴 표현부터 맞춘다.
    "보다 작거나 같": "<=",
    "보다 적거나 같": "<=",
    "보다 낮거나 같": "<=",
    "보다 크거나 같": ">=",
    "보다 많거나 같": ">=",
    "보다 높거나 같": ">=",
    "보다 작": "<",
    "보다 적": "<",
    "보다 낮": "<",
    "보다 크": ">",
    "보다 많": ">",
    "보다 높": ">",
    "같": "==",
}
_COMPARISON_WORDS_BY_LENGTH = sorted(_COMPARISON_WORDS.items(), key=lambda item: -len(item[0]))


def _column_comparison(text: str, entry: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """ "현재고가 재주문점 이하" → (E, F, "<="). 열끼리 비교하는 조건인지 판정한다.

    두 머리글 사이에 숫자가 끼어 있으면 열 비교가 아니라 값 비교이므로 넘긴다.
    """
    if not entry:
        return None
    headers = _headers(entry)
    mentions = find_header_mentions(text, headers)
    if len(mentions) < 2:
        return None
    left, right = mentions[0], mentions[1]
    between = text[left["end"] : right["start"]]
    if re.search(r"\d", between):
        return None
    tail = text[right["end"] : right["end"] + 16]
    operator = next((op for word, op in _COMPARISON_WORDS_BY_LENGTH if word in tail), None)
    if operator is None:
        return None
    left_letter = str(_column_meta(entry, left["header"]).get("letter") or "")
    right_letter = str(_column_meta(entry, right["header"]).get("letter") or "")
    if not left_letter or not right_letter:
        return None
    return left_letter.upper(), right_letter.upper(), operator


def _last_data_row(entry: dict[str, Any] | None) -> int:
    """시트 사용범위의 마지막 행. 알 수 없으면 헤더 다음 한 행만 채운다."""
    used = str((entry or {}).get("used_range") or "")
    match = re.search(r"(\d+)\s*$", used)
    if match:
        try:
            return max(2, int(match.group(1)))
        except ValueError:
            pass
    return 2


def _bind_named_formula(
    params: dict[str, Any], *, message: str, entry: dict[str, Any] | None
) -> tuple[list[str], bool]:
    """ "이익률 열에 매출이익 나누기 매출"을 =G2/F2 로 확정한다.

    돌려주는 두 번째 값은 해결 여부. 열을 못 찾으면 추측하지 않고 되묻게 한다.
    """
    raw = str(params.pop("named_formula_message", "") or "") or str(message or "")
    if not entry:
        return [], False
    headers = _headers(entry)
    parsed = parse_named_formula(raw, headers)
    if parsed is None:
        return [], False

    target_meta = _column_meta(entry, parsed.target)
    letters: list[str] = []
    for operand in parsed.operands:
        meta = _column_meta(entry, operand)
        letter = str(meta.get("letter") or "")
        if not letter:
            return [], False
        letters.append(letter)
    target_letter = str(target_meta.get("letter") or "")
    if not target_letter:
        return [], False

    start_row = 2
    end_row = _last_data_row(entry)
    formula = build_formula(parsed, letters, start_row)
    if not formula:
        return [], False
    params["range_ref"] = f"{target_letter}{start_row}:{target_letter}{end_row}"
    params["formula_a1"] = formula
    params.pop("formula_mode", None)
    return [f"range_ref={params['range_ref']}", f"formula_a1={formula}"], True


def _looks_like_ratio_column(entry: dict[str, Any] | None, letter: str) -> bool:
    """해당 열의 값이 0~1 비율로 저장돼 있는지 샘플로 판단한다."""
    if not entry or not letter:
        return False
    columns = entry.get("columns") or []
    offset = next((i for i, col in enumerate(columns) if str(col.get("letter") or "").upper() == letter), -1)
    if offset < 0:
        return False
    samples: list[float] = []
    for row in entry.get("sample_rows") or []:
        if offset >= len(row):
            continue
        try:
            samples.append(float(str(row[offset]).replace(",", "").rstrip("%")))
        except ValueError:
            continue
    return bool(samples) and all(0 <= v <= 1 for v in samples)


def _bind_message_only_slots(
    steps: list[PlanStep], *, message: str
) -> tuple[list[PlanStep], list[dict[str, Any]]]:
    """워크북 상태 없이도 원문만으로 확정할 수 있는 슬롯을 채운다."""
    bound: list[PlanStep] = []
    notes: list[dict[str, Any]] = []
    for step in steps:
        params = dict(step.params)
        changes: list[str] = []
        if step.action == "excel_live.write_range":
            changes.extend(_bind_write_values(params, message=message))
        if step.action in _CONDITION_FORMAT_ACTIONS:
            changes.extend(_bind_condition_format(params, message=message))
        if changes:
            notes.append({"action": step.action, "status": "bound", "changes": changes})
        bound.append(PlanStep(action=step.action, params=params, reason=step.reason))
    return bound, notes


def bind_plan_steps(
    steps: list[PlanStep],
    *,
    digest: dict[str, Any],
    message: str,
    sheet_name: str | None,
) -> tuple[list[PlanStep], list[dict[str, Any]]]:
    """플랜의 상징적 파라미터를 실제 워크북 좌표로 확정한다.

    다이제스트를 못 읽었으면(빈 워크북·연결 실패) 아무것도 바꾸지 않는다 — 추측으로 덮어쓰는 게 더 위험하다.
    """
    entry = sheet_entry(digest, sheet_name)
    headers_known = bool(entry and _headers(entry))
    if not headers_known:
        # 머리글을 모르면 열 바인딩은 추측이 되므로 하지 않는다.
        # 다만 원문에만 있는 리터럴(쓰기 값)은 워크북 상태와 무관하게 채울 수 있다.
        return _bind_message_only_slots(steps, message=message)

    candidates = mentioned_headers(message, entry, digest)
    # 원문이 기준 열을 한 번도 말하지 않았는지. 말하지 않았다면 정렬/중복제거는 되물어야 한다.
    column_stated = bool(candidates) or bool(_COLUMN_LETTER_MENTION.search(str(message or "")))
    bound: list[PlanStep] = []
    notes: list[dict[str, Any]] = []
    created_sheet = ""

    for step in steps:
        params = dict(step.params)
        used: set[str] = set()
        changes: list[str] = []

        if not column_stated:
            for slot in _REQUIRE_EXPLICIT_COLUMN.get(step.action, ()):
                # 원문이 기준을 말하지 않았다. 플래너가 채워 둔 값은 학습 데이터에서
                # 튀어나온 이름일 수 있으므로 "값이 있다"는 이유로 지워선 안 된다.
                notes.append(
                    {
                        "action": step.action,
                        "slot": slot,
                        "status": "unresolved",
                        "reason": "not_stated",
                    }
                )

        range_slot = _WHOLE_TABLE_RANGE_SLOTS.get(step.action)
        if range_slot and not _EXPLICIT_RANGE_MENTION.search(str(message or "")):
            # 원문이 범위를 말하지 않았는데 플래너가 "A1:L37" 같은 범위를 지어내면
            # 표의 일부만 집계·정렬해 놓고 성공했다고 보고한다. 표 전체로 되돌린다.
            current = str(params.get(range_slot) or "").strip()
            if current and current != "__ACTIVE_SELECTION__":
                params[range_slot] = "__ACTIVE_SELECTION__"
                changes.append(f"{range_slot}=__ACTIVE_SELECTION__")

        if step.action == "excel_live.pivot_table":
            changes.extend(_bind_pivot(params, message=message, entry=entry))
            for slot in ("row_field", "column_field", "value_field"):
                if isinstance(params.get(slot), str):
                    used.add(str(params[slot]))

        if step.action == "excel_live.sort_range" and column_stated:
            sort_key = _pick_sort_key(message, _headers(entry))
            if sort_key and sort_key != params.get("key_column"):
                params["key_column"] = sort_key
                changes.append(f"key_column={sort_key}")
                used.add(sort_key)

        for slot in _COLUMN_SLOTS.get(step.action, ()):  # 단일 열 슬롯
            if slot not in params:
                # 플래너가 기준 열을 아예 빼먹으면 검증기가 1번 열로 채워버린다.
                # 원문이 열을 말했다면 여기서 채우고, 아니면 미해결로 보고해 되묻는다.
                if slot not in _REQUIRED_COLUMN_SLOTS.get(step.action, ()):
                    continue
                params[slot] = None
            if params.get(slot) is None and slot == "column_field":
                continue
            before = params[slot]
            resolved, reason = _resolve_column_value(
                before, entry=entry, candidates=candidates, used=used
            )
            if isinstance(resolved, str):
                used.add(resolved)
            if reason == "unresolved":
                notes.append({"action": step.action, "slot": slot, "status": "unresolved"})
            elif resolved != before:
                params[slot] = resolved
                changes.append(f"{slot}={resolved}")

        for slot in _COLUMN_LIST_SLOTS.get(step.action, ()):
            raw_list = params.get(slot)
            if not isinstance(raw_list, list) or not raw_list:
                # "코드 열 기준으로 중복 제거"처럼 원문이 기준을 말했으면 채우고,
                # "중복 없애줘"처럼 기준이 없으면 임의로 정하지 말고 미해결로 보고한다.
                if candidates:
                    params[slot] = [candidates[0]]
                    changes.append(f"{slot}=[{candidates[0]}]")
                elif step.action not in _OPTIONAL_COLUMN_LIST_ACTIONS:
                    notes.append({"action": step.action, "slot": slot, "status": "unresolved"})
                continue
            resolved_list: list[Any] = []
            for item in raw_list:
                resolved, _reason = _resolve_column_value(
                    item, entry=entry, candidates=candidates, used=used
                )
                if isinstance(resolved, str):
                    used.add(resolved)
                resolved_list.append(resolved)
            if resolved_list != raw_list:
                params[slot] = resolved_list
                changes.append(f"{slot}={resolved_list}")

        if step.action == "excel_live.filter_rows":
            changes.extend(_bind_filter_value(params, message=message, entry=entry))
            changes.extend(_bind_filter_mode(params, message=message))

        if step.action == "excel_live.write_range":
            changes.extend(_bind_write_values(params, message=message))

        if step.action == "excel_live.set_formula" and (
            params.get("named_formula_message") or str(params.get("formula_mode") or "") == "named"
        ):
            formula_changes, resolved = _bind_named_formula(params, message=message, entry=entry)
            if resolved:
                changes.extend(formula_changes)
            else:
                notes.append({"action": step.action, "slot": "formula_a1", "status": "unresolved"})

        if step.action in _CONDITION_FORMAT_ACTIONS:
            changes.extend(_bind_condition_format(params, message=message, entry=entry, digest=digest))

        if step.action == "excel_live.consolidate_sheets":
            changes.extend(_bind_consolidate(params, message=message, digest=digest))

        if step.action in _OUTPUT_SHEET_ACTIONS:
            changes.extend(
                _normalize_output_sheet(
                    params,
                    message=message,
                    source_sheet=str(entry.get("name") or ""),
                    action=step.action,
                )
            )

        changes.extend(_bind_created_sheet_target(step.action, params, created_sheet=created_sheet))
        if step.action == "excel_live.create_sheet":
            created_sheet = str(params.get("sheet_name") or "").strip() or created_sheet

        # 미해결 보고는 슬롯 순회 시점에 남는데, 그 뒤 단계에서 채워지는 슬롯이 있다.
        # 예: "완료된 것만 남겨줘"는 열을 말하지 않았지만 값 '완료'가 상태 열에만
        # 있으므로 _bind_filter_value가 기준 열을 특정한다. 이미 정해진 슬롯을
        # 미해결로 남겨 두면 물어볼 필요가 없는 걸 묻게 된다.
        notes = [note for note in notes if not _is_stale_unresolved(note, step.action, params)]

        if changes:
            notes.append({"action": step.action, "status": "bound", "changes": changes})
        bound.append(PlanStep(action=step.action, params=params, reason=step.reason))

    return bound, notes


def _is_stale_unresolved(note: dict[str, Any], action: str, params: dict[str, Any]) -> bool:
    """이 단계에서 결국 채워진 슬롯의 미해결 보고인지."""
    if note.get("status") != "unresolved" or note.get("action") != action:
        return False
    if note.get("reason") == "not_stated":
        # 원문이 기준 열을 말하지 않았다. 플래너가 채운 값도, 그 값을 다듬은 결과도
        # 근거가 될 수 없다("Qty" → "QTY"처럼 학습 데이터의 열 이름이 그대로 나온다).
        # 데이터가 조용히 뒤섞이느니 되묻는 편이 낫다.
        return False
    value = params.get(str(note.get("slot") or ""))
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""


# 앞 단계에서 만든 시트에 이어서 써야 하는 액션. 원본을 읽어 집계하는 액션은 제외한다.
_WRITE_INTO_NEW_SHEET_ACTIONS = frozenset(
    {
        "excel_live.write_range",
        "excel_live.set_formula",
        "excel_live.fill_range",
        "excel_live.apply_border",
        "excel_live.create_table",
    }
)


def _bind_created_sheet_target(
    action: str, params: dict[str, Any], *, created_sheet: str
) -> list[str]:
    """ "Summary 시트 만들어서 A1에 ... 쓰고" 의 두 번째 단계가 갈 곳.

    시트를 만들면 활성 시트가 따라 옮겨갈 거라 기대하지만 실제로는 그렇지 않다.
    그대로 두면 write_range가 원본 시트 A1에 떨어져 머리글을 덮어쓴다. 되돌리기 전까지
    사용자는 "Summary 시트를 만들었습니다"라는 성공 메시지만 본다.
    """
    if not created_sheet or action not in _WRITE_INTO_NEW_SHEET_ACTIONS:
        return []
    if str(params.get("sheet_name") or "").strip():
        return []
    params["sheet_name"] = created_sheet
    return [f"sheet_name={created_sheet}"]
