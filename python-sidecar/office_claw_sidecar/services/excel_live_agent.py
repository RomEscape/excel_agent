"""
Excel Live Agent — 자연어 명령을 Excel Live 작업 액션으로 변환.

우선순위:
  1) 빠른 정규식 규칙 기반 파싱 (대표 시나리오)
  2) LLM JSON 분류 기반 폴백
"""

from __future__ import annotations

import re
import time
from typing import Any

from office_claw_sidecar.services import excel_observation
from office_claw_sidecar.services.decision_trace import (
    Long,
)
from office_claw_sidecar.services.decision_trace import (
    note as trace_note,
)
from office_claw_sidecar.services.decision_trace import (
    route as trace_route,
)
from office_claw_sidecar.services.excel_intent_normalizer import (
    intent_to_plan,
    normalize_intent,
)
from office_claw_sidecar.services.excel_live_plan_validator import (
    SUPPORTED_ACTIONS as VALIDATOR_SUPPORTED_ACTIONS,
)
from office_claw_sidecar.services.excel_live_table_presets import (
    match_table_preset,
    preset_follow_up,
)
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt
from office_claw_sidecar.services.llm_json import extract_json_object
from office_claw_sidecar.services.llm_service import (
    get_planner_model_name,
    get_strong_llm_service,
    get_strong_planner_model_name,
)

# 계획 수립은 창작이 아니다. 기본 샘플링(0.8)에서는 "Profit_Margin 열 이름을 마진율로 바꿔줘"가
# 실행할 때마다 rename_column이 되기도 하고 노란색 칠하기가 되기도 한다.
PLAN_TEMPERATURE = 0.0

# 라우터의 `asyncio.wait_for` 예산에서 이만큼 뺀 값을 HTTP 상한으로 준다. 소켓이
# 먼저 끊겨야 요청이 실제로 끝난다 — 바깥에서만 취소하면 httpx 요청은 백그라운드에
# 그대로 살아 있어 Ollama에 부하가 쌓인다.
HTTP_TIMEOUT_MARGIN_SECONDS = 2.0


def http_budget_for(parse_timeout_seconds: Any) -> float | None:
    """파서 예산에서 여유분을 뺀 HTTP 상한. 예산을 모르면 None(기본값 사용)."""
    try:
        budget = float(parse_timeout_seconds)
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    return max(1.0, budget - HTTP_TIMEOUT_MARGIN_SECONDS)

# 허용 액션은 검증기가 단일 소스다. 여기에 사본을 두면 한쪽만 늘어난다 —
# 실제로 clear_range·compare_ranges·forecast_linear 등 7종이 이 사본에만 빠져 있어서,
# 프롬프트는 쓰라고 안내하는데 플래너가 고르면 파싱 단계에서 통째로 반려됐다.
SUPPORTED_ACTIONS = VALIDATOR_SUPPORTED_ACTIONS


def _has_likely_edit_intent(message: str) -> bool:
    lowered = str(message or "").lower()
    token_hit = any(
        token in lowered
        for token in [
            "경계선",
            "테두리",
            "border",
            "입력",
            "작성",
            "적용",
            "수식",
            "함수",
            "계산",
            "검증",
            "강조",
            "칠해",
            "배경",
            "배경색",
            "색",
            "노란색",
            "빨간색",
            "파란색",
            "초록색",
            "표",
            "테이블",
            "table",
            "저장",
            "만들어",
            "만들",
            "생성",
            "채워",
            "바꿔",
            "정렬",
            "필터",
            "중복",
            "피벗",
            "차트",
            "그래프",
            "검증",
        ]
    )
    if token_hit:
        return True

    # "A열에서 10 이상인 셀" 같은 조건부 문장을 편집 의도로 인식한다.
    has_numeric_condition = bool(
        re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만|보다\s*크|보다\s*작|>=|<=|>|<|==|!=)", lowered)
    )
    has_cell_target = bool(re.search(r"([a-z]\s*열|[a-z]+\d+:[a-z]+\d+|[a-z]+\d+)", lowered, re.IGNORECASE))
    return has_numeric_condition and has_cell_target


# 범위 추출에 \b를 쓰면 안 된다.
# 파이썬 \w는 한글을 포함하므로 "A1:E9에"의 9와 '에' 사이에 경계가 없고,
# 그 결과 A1:E9 대신 짧은 대안인 A1만 매칭돼 한 칸에만 작업이 적용된다.
RANGE_REF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z]{1,3}\d{1,7}:[A-Za-z]{1,3}\d{1,7}|[A-Za-z]{1,3}:[A-Za-z]{1,3}|[A-Za-z]{1,3}\d{1,7})"
    r"(?![A-Za-z0-9:])"
)

# "C열", "C 열을"처럼 조사가 붙어도 열 문자를 인식한다.
COLUMN_LETTER_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*열")


def _extract_range_ref(text: str) -> str | None:
    match = RANGE_REF_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(1).upper()


def _extract_target_range_from_text(text: str) -> str | None:
    explicit = _extract_range_ref(text)
    if explicit:
        return explicit
    col_match = COLUMN_LETTER_PATTERN.search(str(text or ""))
    if col_match:
        col = col_match.group(1).upper()
        return f"{col}:{col}"
    return None


# "글자 크기 16", "16pt", "폰트 크기를 14로"
_FONT_SIZE_PATTERN = re.compile(
    r"(?:글자|글씨|폰트|글꼴|텍스트|font)?\s*(?:크기|사이즈|size)\s*(?:를|을)?\s*(\d{1,3})|(\d{1,3})\s*(?:pt|포인트)",
    re.IGNORECASE,
)
# 글자색을 가리키는 말. 배경색과 반드시 갈라야 한다 — 안 그러면 배경을 칠해 버린다.
_FONT_COLOR_MARKER = re.compile(
    r"(글자\s*색|글씨\s*색|폰트\s*색|글꼴\s*색|텍스트\s*색|font\s*colou?r)", re.IGNORECASE
)
_COLOR_TOKEN = re.compile(
    r"(#[0-9a-fA-F]{6}|노란색|노랑|노란|yellow|빨간색|빨강|빨간|red|파란색|파랑|blue"
    r"|초록색|초록|green|흰색|하얀색|하양|white|화이트|백색|검정|검은색|검은|black)",
    re.IGNORECASE,
)
_COLORED_TEXT_PATTERN = re.compile(
    r"(#[0-9a-fA-F]{6}|[가-힣]{1,4}색|[가-힣]{1,3})\s*(?:글씨|글자|텍스트)", re.IGNORECASE
)
# "글씨 흰색", "글자를 빨강으로" — 색이 뒤에 오는 형태.
#
# 조사 "도"가 빠져 있었다(2026-08-17 멀티턴 실측). 앞 턴에서 배경을 칠하고
# "글자도 흰색으로"라고 하는 건 가장 자연스러운 말투인데, 여기서 못 잡으면
# 배경색 규칙으로 새어 **방금 칠한 배경을 흰색으로 덮어썼다.**
#   "글자 흰색으로"   → set_font   (정상)
#   "글자도 흰색으로" → fill_range (배경 파괴)
_TEXT_THEN_COLOR_PATTERN = re.compile(
    r"(?:글씨|글자|텍스트|폰트|글꼴)\s*(?:도|만|은|는|를|을|의|색|색깔|색상)?\s*(?:을|를)?\s*"
    r"(#[0-9a-fA-F]{6}|노란색|노랑|yellow|빨간색|빨강|red|파란색|파랑|blue"
    r"|초록색|초록|green|흰색|하얀색|하양|white|화이트|백색|검정|검은색|black)",
    re.IGNORECASE,
)
# 글자색을 말하는 문장은 배경색 규칙이 가로채면 안 된다. "글씨 흰색으로"가 배경을
# 하얗게 칠해 버린 사례가 있다(2026-08-16 실측).
FONT_COLOR_CONTEXT = re.compile(
    r"(글씨|글자|텍스트|폰트\s*색|글꼴\s*색|font\s*colou?r)", re.IGNORECASE
)


def extract_font_params(text: str) -> dict[str, Any]:
    """문장에서 글꼴 속성(굵게·크기·색)을 뽑는다.

    지금까지는 `bold=True` 하나만 넣고 크기·색을 버렸다. `set_font`는 `size`·`color`를
    받는데 파서가 안 넘겨서 "글자 크기 16으로", "제목 글씨 흰색"이 통째로 무시됐다
    (2026-08-16 실측: 참고 대시보드의 흰 제목 글씨와 증감 표시 색을 못 만들었다).

    굵게를 말하지 않았으면 `bold`를 넣지 않는다 — 크기만 바꾸려던 요청에 굵기까지
    바꿔 버리면 사용자가 하지 않은 편집이 된다.
    """
    lowered = str(text or "").lower()
    out: dict[str, Any] = {}

    if re.search(r"(굵게|볼드|bold|굵은|두껍)", lowered):
        out["bold"] = True
    elif re.search(r"(굵기\s*(?:해제|없|빼)|보통\s*굵기|not\s*bold)", lowered):
        out["bold"] = False

    size_match = _FONT_SIZE_PATTERN.search(lowered)
    if size_match:
        raw = size_match.group(1) or size_match.group(2)
        try:
            size = float(raw)
        except (TypeError, ValueError):
            size = 0.0
        # 엑셀이 받는 범위 밖이면 무시한다. "2026년" 같은 숫자를 크기로 읽으면 안 된다.
        if 1 <= size <= 409:
            out["size"] = size

    color = ""
    marker = _FONT_COLOR_MARKER.search(lowered)
    if marker:
        token = _COLOR_TOKEN.search(lowered[marker.end() :])
        if token:
            color = _normalize_color(token.group(1))
    if not color:
        # "글씨 흰색", "글자를 빨강으로" — 색이 뒤에 오는 형태.
        after = _TEXT_THEN_COLOR_PATTERN.search(str(text or ""))
        if after:
            color = _normalize_color(after.group(1))
    if not color:
        # "빨간 글씨", "흰색 글자" — 색이 앞에 오는 형태.
        phrase = _COLORED_TEXT_PATTERN.search(str(text or ""))
        if phrase:
            candidate = _normalize_color(phrase.group(1))
            if candidate != "#FFFF00" or "노" in phrase.group(1):
                color = candidate
    if color:
        out["color"] = color
    return out


def _normalize_color(word: str) -> str:
    normalized = word.strip().lower()
    # `#1F4E79` 처럼 코드로 준 색. 대시보드 배색은 이름으로 부를 수 없는 색이 대부분인데,
    # 이걸 못 읽으면 전부 기본값(노랑)으로 칠해진다(2026-08-16 실측: 남색 제목 바가 노랗게 나왔다).
    hex_match = re.fullmatch(r"#?([0-9a-f]{6})", normalized)
    if hex_match:
        return f"#{hex_match.group(1).upper()}"
    if normalized in {"노란색", "노랑", "노란", "yellow"}:
        return "#FFFF00"
    if normalized in {"빨간색", "빨강", "빨간", "red"}:
        return "#FF0000"
    if normalized in {"초록색", "초록", "초록색으로", "green"}:
        return "#00FF00"
    if normalized in {"파란색", "파랑", "blue"}:
        return "#0000FF"
    # 글자색으로 가장 많이 쓰는 두 색이 빠져 있었다. 없으면 노랑으로 폴백해서
    # "제목 글씨 흰색으로"가 노란 글씨가 됐다(2026-08-16 실측).
    if normalized in {"흰색", "하얀색", "하양", "흰", "white", "화이트", "백색"}:
        return "#FFFFFF"
    if normalized in {"검정", "검은색", "검은", "black", "블랙"}:
        return "#000000"
    return "#FFFF00"


_TEXT_EQUALS_SKIP = frozenset(
    {
        "노란",
        "노랑",
        "노란색",
        "빨간",
        "빨강",
        "빨간색",
        "파란",
        "파랑",
        "파란색",
        "초록",
        "초록색",
        "흰",
        "하얀",
        "흰색",
        "검정",
        "검은",
        "yellow",
        "red",
        "blue",
        "green",
        "white",
        "black",
        "조건부",
        "서식",
        "배경",
        "하이라이트",
    }
)
_TEXT_EQUALS_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9_]{2,20})(?:이면|면|인\s*행|인\s*셀|일\s*때)"
)
_CONVERT_EXISTING_TABLE_PATTERN = re.compile(
    r"(엑셀\s*표|테이블로\s*(?:만들|변환|바꿔)|표로\s*(?:변환|바꿔)|listobject)",
    re.IGNORECASE,
)


def parse_text_equals_condition(message: str) -> str | None:
    """'발주필요인 행' / '미납이면'처럼 값 동등 조건을 뽑는다."""
    for match in _TEXT_EQUALS_PATTERN.finditer(str(message or "")):
        token = str(match.group(1) or "").strip()
        if not token or token.casefold() in _TEXT_EQUALS_SKIP:
            continue
        if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
            continue
        return token
    return None


def looks_like_existing_table_convert(message: str) -> bool:
    """이미 있는 데이터 범위를 Excel 표로 바꾸라는 문장인지.

    create_table은 빈 n×m 격자다. '엑셀 표로 만들어줘'를 그 슬롯에 넣으면
    크기 질문만 하고 ListObject는 안 생긴다.
    """
    text = str(message or "")
    lowered = text.lower()
    if re.search(r"집계표|피벗\s*테이블|피벗테이블|pivot\s*table", lowered):
        return False
    if not _CONVERT_EXISTING_TABLE_PATTERN.search(text):
        return False
    if re.search(r"\d{1,3}\s*(?:\*|x|×)\s*\d{1,3}", lowered):
        return False
    if re.search(r"\d{1,3}\s*(?:행|열|by)", lowered):
        return False
    return True


def _parse_operator_threshold(text: str) -> tuple[str, float] | None:
    op_map = {
        "이상": ">=",
        "초과": ">",
        "이하": "<=",
        "미만": "<",
        "같음": "==",
        "같지 않음": "!=",
        "크거나 같": ">=",
        "작거나 같": "<=",
        "보다 크": ">",
        "보다 작": "<",
        "greater than or equal": ">=",
        "less than or equal": "<=",
        "greater than": ">",
        "less than": "<",
    }
    # 50 이상 / 50 초과 / ...
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만|같음|같지 않음)", text)
    if m:
        return op_map[m.group(2)], float(m.group(1))
    # 50보다 크다 / 50보다 작다
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(크거나\s*같|작거나\s*같|큰|작은|크|작)", text)
    if m:
        key = m.group(2).replace(" ", "")
        key_map = {
            "크": ">",
            "작": "<",
            "큰": ">",
            "작은": "<",
            "크거나같": ">=",
            "작거나같": "<=",
        }
        operator = key_map.get(key)
        if operator:
            return operator, float(m.group(1))
    # > 50 / >= 50 / <= 10 / != 0
    m = re.search(r"(>=|<=|>|<|==|!=)\s*(-?\d+(?:\.\d+)?)", text)
    if m:
        return m.group(1), float(m.group(2))
    return None


def _parse_literal_value(raw: str) -> Any:
    text = raw.strip().strip("\"'")
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _build_formula_from_function(func_word: str, source_range: str) -> str:
    normalized = func_word.strip().lower()
    func_map = {
        "합계": "SUM",
        "합": "SUM",
        "sum": "SUM",
        "평균": "AVERAGE",
        "average": "AVERAGE",
        "avg": "AVERAGE",
        "최대": "MAX",
        "max": "MAX",
        "최소": "MIN",
        "min": "MIN",
        "개수": "COUNT",
        "카운트": "COUNT",
        "count": "COUNT",
    }
    fn = func_map.get(normalized, "SUM")
    return f"={fn}({source_range})"


def _range_to_empty_payload(range_ref: str) -> tuple[str, list[list[Any]]]:
    """
    A1 또는 A1:C3 범위를 write_range 입력 형태(start_cell + values_2d)로 변환.
    지우기 명령은 None 매트릭스를 써서 기존 값을 clear한다.
    """
    normalized = range_ref.strip().upper()
    if ":" not in normalized:
        return normalized, [[None]]

    left, right = normalized.split(":")
    left_col_m = re.match(r"([A-Z]+)(\d+)", left)
    right_col_m = re.match(r"([A-Z]+)(\d+)", right)
    if not left_col_m or not right_col_m:
        return left, [[None]]

    start_col, start_row = left_col_m.group(1), int(left_col_m.group(2))
    end_col, end_row = right_col_m.group(1), int(right_col_m.group(2))
    row_count = max(1, end_row - start_row + 1)
    col_count = _column_span(start_col, end_col)
    values_2d = [[None for _ in range(col_count)] for _ in range(row_count)]
    return left, values_2d


def parse_command_rule_based(message: str, *, context_range: str | None = None) -> dict[str, Any] | None:
    text = message.strip()
    lowered = text.lower()
    write_verbs = r"(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input|fill)"
    border_words = r"(경계선|테두리|border|보더)"
    border_apply_words = r"(적용|넣|추가|만들|그려|draw|apply|set)"

    if any(
        token in lowered
        for token in [
            "열린 엑셀",
            "워크북 목록",
            "열린 파일 목록",
            "열린 통합문서",
            "열려 있는 엑셀 파일",
            "workbook list",
            "list workbooks",
        ]
    ):
        return {
            "action": "excel_live.list_workbooks",
            "params": {},
            "reason": "열린 통합문서 목록 조회 요청",
        }

    if any(
        token in lowered
        for token in [
            "시트 목록",
            "탭 목록",
            "현재 시트",
            "시트들",
            "sheet list",
            "list sheets",
            "worksheet list",
        ]
    ):
        return {
            "action": "excel_live.list_sheets",
            "params": {},
            "reason": "시트 목록 조회 요청",
        }

    create_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:만들|생성|추가|create|add)",
        text,
        re.IGNORECASE,
    )
    if create_sheet_match:
        sheet_name = str(create_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return {
                "action": "excel_live.create_sheet",
                "params": {"sheet_name": sheet_name, "make_active": True},
                "reason": "새 시트 생성 요청",
            }

    rename_sheet_match = re.search(
        r"([A-Za-z0-9_가-힣]+)\s*(?:시트|sheet|탭)\s*(?:이름(?:을|를)?)?\s*(?:을|를)?\s*"
        r"([A-Za-z0-9_가-힣]+)\s*(?:으로|로)\s*(?:바꿔|변경|고쳐|rename)",
        text,
        re.IGNORECASE,
    )
    if rename_sheet_match:
        old_name = str(rename_sheet_match.group(1)).strip().strip("\"'")
        new_name = str(rename_sheet_match.group(2)).strip().strip("\"'")
        if old_name and new_name:
            return {
                "action": "excel_live.rename_sheet",
                "params": {"sheet_name": old_name, "new_name": new_name},
                "reason": "시트 이름 변경 요청",
            }

    delete_sheet_match = re.search(
        r"([A-Za-z0-9_가-힣]+)\s*(?:시트|sheet|탭)\s*(?:을|를)?\s*(?:삭제|제거|없애)",
        text,
        re.IGNORECASE,
    )
    if delete_sheet_match:
        sheet_name = str(delete_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return {
                "action": "excel_live.delete_sheet",
                "params": {"sheet_name": sheet_name},
                "reason": "시트 삭제 요청",
            }

    select_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:로|으로)?\s*(?:이동|전환|선택|활성화|switch|go)",
        text,
        re.IGNORECASE,
    )
    if select_sheet_match:
        sheet_name = str(select_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return {
                "action": "excel_live.select_sheet",
                "params": {"sheet_name": sheet_name},
                "reason": "작업 시트 전환 요청",
            }

    # 예: "워크북 text_1.xlsx 선택", "select workbook text_1.xlsx"
    select_match = re.search(
        r"(?:워크북|통합문서|파일|workbook)\s+([^\s]+\.xlsx|[^\s]+)\s*(?:선택|전환|열어|열기|select|switch)",
        text,
        re.IGNORECASE,
    )
    if select_match:
        target = select_match.group(1).strip().strip("\"'")
        return {
            "action": "excel_live.select_workbook",
            "params": {"workbook_id": target},
            "reason": "대상 워크북 선택 요청",
        }

    # 예: "C3 셀 내용 지워줘", "B2:D3 비워줘", "clear A1"
    clear_match = re.search(
        r"\b([a-z]+\d+:[a-z]+\d+|[a-z]+\d+)\b[^\n]*(지워|지우|삭제|비워|clear|erase|reset)",
        text,
        re.IGNORECASE,
    )
    if clear_match:
        target_range = clear_match.group(1).upper()
        start_cell, values_2d = _range_to_empty_payload(target_range)
        return {
            "action": "excel_live.write_range",
            "params": {"start_cell": start_cell, "values_2d": values_2d},
            "reason": "셀 값 삭제 요청",
        }

    if looks_like_existing_table_convert(text):
        target_range = _extract_target_range_from_text(text) or context_range or "__USED_RANGE__"
        return {
            "action": "excel_live.convert_to_excel_table",
            "params": {"target_range": target_range, "has_header": True},
            "reason": "기존 데이터 범위를 Excel 표로 변환",
        }

    # 예: "5 * 5 표 만들어줘", "3x4 테이블 생성"
    table_match = re.search(
        r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})\s*(?:표|테이블|table)",
        lowered,
        re.IGNORECASE,
    )
    if not table_match:
        table_match = re.search(
            r"(\d{1,3})\s*행\s*(\d{1,3})\s*열\s*(?:표|테이블|table)",
            lowered,
            re.IGNORECASE,
        )
    if table_match and re.search(r"(만들|생성|작성|create|make)", lowered):
        rows = max(1, min(100, int(table_match.group(1))))
        cols = max(1, min(50, int(table_match.group(2))))
        return {
            "action": "excel_live.create_table",
            "params": {
                "start_cell": "__ACTIVE_CELL__",
                "rows": rows,
                "cols": cols,
                "with_border": True,
            },
            "reason": "선택 셀 기준 표 생성 요청",
        }

    # "테두리 넣어줘"가 값 쓰기(write_range)로 잘못 분류되지 않도록
    # 경계선 의도를 읽기/쓰기보다 우선 판별한다.
    if re.search(border_words, lowered) and re.search(border_apply_words, lowered):
        target_range = _extract_target_range_from_text(lowered) or context_range or "__ACTIVE_SELECTION__"
        # 사용자 체감상 "적용이 안 됨"을 줄이기 위해 기본을 눈에 띄는 medium으로 둔다.
        weight = "medium"
        if re.search(r"(얇게|얇은|thin)", lowered):
            weight = "thin"
        elif re.search(r"(굵게|두껍|thick|굵은)", lowered):
            weight = "medium"
        # 기본 색상은 검정으로 사용해 Excel 기본 격자선보다 확실히 보이게 한다.
        color = "#000000"
        if re.search(r"(검정|검은|black)", lowered):
            color = "#000000"
        return {
            "action": "excel_live.apply_border",
            "params": {
                "target_range": target_range,
                "line_style": "continuous",
                "weight": weight,
                "color": color,
            },
            "reason": "선택 범위 경계선 적용 요청",
        }

    # "글씨 흰색으로"는 굵게·글꼴이라는 말이 없어 예전엔 배경색 규칙으로 흘러 배경을 칠했다.
    # 색·크기를 실제로 뽑아냈으면 글꼴 요청으로 본다.
    font_params = extract_font_params(text)
    if (
        re.search(r"(굵게|볼드|bold|글꼴|폰트)", lowered)
        or font_params.get("color")
        or font_params.get("size")
    ) and not re.search(r"(테두리|경계선|border|괘선)", lowered):
        header_font = bool(re.search(r"(머리글|헤더|header)", lowered))
        target_range = _extract_target_range_from_text(lowered) or (
            "1:1" if header_font else (context_range or "__ACTIVE_SELECTION__")
        )
        if not font_params:
            # "글꼴 바꿔줘"처럼 무엇을 바꿀지 없는 문장. 굵게로 단정하지 않는다.
            font_params = {"bold": True}
        return {
            "action": "excel_live.set_font",
            "params": {"target_range": target_range, **font_params},
            "reason": "글꼴 변경 요청",
        }

    if re.search(r"데이터\s*막대|data\s*bar", lowered):
        target_range = _extract_target_range_from_text(text) or context_range or "__ACTIVE_SELECTION__"
        return {
            "action": "excel_live.apply_data_bar",
            "params": {"target_range": target_range},
            "reason": "데이터 막대 조건부 서식",
        }

    if re.search(r"색조|컬러\s*스케일|color\s*scale", lowered):
        target_range = _extract_target_range_from_text(text) or context_range or "__ACTIVE_SELECTION__"
        return {
            "action": "excel_live.apply_color_scale",
            "params": {"target_range": target_range},
            "reason": "색조 조건부 서식",
        }

    # 예: "A1:C10 읽어줘", "B열 보여줘"
    read_verbs = r"(읽어|읽기|보여|조회|확인|read|show|display)"
    if (
        re.search(read_verbs, lowered)
        and not re.search(write_verbs, lowered)
        # "읽기 편하게 콤마 찍어줘"의 '읽기'는 조회가 아니라 가독성 이야기다.
        # 2026-08-17 실측: 이 오탐이 read_range를 만들어 표시 형식 계획을 덮었다.
        and not re.search(r"(읽기|보기)\s*(좋|편|쉽)", lowered)
        and not re.search(r"(콤마|쉼표|서식|포맷|형식|소수점|퍼센트|정렬|테두리)", lowered)
    ):
        range_ref = _extract_target_range_from_text(lowered)
        if not range_ref:
            range_ref = context_range or "__ACTIVE_SELECTION__"
        return {
            "action": "excel_live.read_range",
            "params": {"range_ref": range_ref},
            "reason": "범위 읽기 요청",
        }

    # 예: "C1에 A1:A10 합계 수식 넣어줘", "set sum formula in C1 from A1:A10"
    formula_template = re.search(
        r"([a-z]+\d+)\s*에\s*([a-z]+\d+:[a-z]+\d+)\s*(합계|합|평균|average|avg|sum|최대|max|최소|min|개수|카운트|count).*(수식|formula|넣|적용|set)",
        lowered,
    )
    if formula_template:
        target_cell = formula_template.group(1).upper()
        source_range = formula_template.group(2).upper()
        func_word = formula_template.group(3)
        return {
            "action": "excel_live.set_formula",
            "params": {
                "range_ref": target_cell,
                "formula_a1": _build_formula_from_function(func_word, source_range),
            },
            "reason": "집계 함수 수식 생성 요청",
        }

    formula_cf = bool(re.search(r"조건부\s*서식|수식\s*조건부", lowered))
    text_equals = parse_text_equals_condition(text)
    if formula_cf and text_equals:
        col_match = COLUMN_LETTER_PATTERN.search(text)
        col = str(col_match.group(1)).upper() if col_match else ""
        target_range = _extract_target_range_from_text(lowered) or (f"{col}:{col}" if col else "__USED_RANGE__")
        formula_col = col or "A"
        color_match = re.search(
            r"(#?[0-9a-fA-F]{6}|노란색|노랑|yellow|빨간색|빨강|red|초록색|초록|green|파란색|파랑|blue)",
            lowered,
        )
        fill = _normalize_color(color_match.group(1)) if color_match else "#FFC7CE"
        return {
            "action": "excel_live.apply_formula_cf",
            "params": {
                "target_range": target_range,
                "formula": f'=${formula_col}2="{text_equals}"',
                "fill_color": fill,
            },
            "reason": "수식 조건부 서식 요청",
        }

    # 예: "A열 50보다 큰 값 노란색으로 칠해줘", "A1:A20 >= 100 highlight"
    if re.search(r"(칠해|강조|표시|highlight|색|채워|배경|바꿔)", lowered):
        op_threshold = _parse_operator_threshold(lowered)
        color_match = re.search(
            r"(#?[0-9a-fA-F]{6}|노란색|노랑|yellow|빨간색|빨강|red|초록색|초록|green|파란색|파랑|blue)",
            lowered,
        )
        if op_threshold is None and text_equals:
            target_range = _extract_target_range_from_text(lowered) or "A:Z"
            return {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": target_range,
                    "operator": "==",
                    "threshold": 0,
                    "value": text_equals,
                    "fill_color": _normalize_color(color_match.group(1)) if color_match else "#FFFF00",
                },
                "reason": "값 동등 조건부 강조 요청",
            }
        # 예: "표 색을 전반적으로 노랗게 칠해줘"처럼 조건 없는 전체 채우기
        if op_threshold is None:
            broad_intent = bool(
                re.search(r"(전반|전체|전체적|모든|표\s*색|배경색|통으로)", lowered)
            )
            target_range = _extract_target_range_from_text(lowered) or (
                context_range or "__ACTIVE_SELECTION__" if broad_intent else "A:Z"
            )
            return {
                "action": "excel_live.fill_range",
                "params": {
                    "target_range": target_range,
                    "fill_color": _normalize_color(color_match.group(1)) if color_match else "#FFFF00",
                },
                "reason": "범위 전체 배경색 적용 요청",
            }

        # 범위를 특정하지 않은 강조 명령은 A:A로 좁게 잡으면 체감상 "안 된다"가 되기 쉽다.
        # 기본값을 A:Z로 넓혀 일반 표(좌측 영역)에서 자연어 명령이 더 잘 동작하게 한다.
        target_range = _extract_target_range_from_text(lowered) or "A:Z"
        if op_threshold:
            operator, threshold = op_threshold
            color = _normalize_color(color_match.group(1)) if color_match else "#FFFF00"
            return {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": target_range,
                    "operator": operator,
                    "threshold": threshold,
                    "fill_color": color,
                },
                "reason": "조건부 강조 요청",
            }

    # 예: "B2:D2에 헤더 써줘"
    header_match = re.search(
        r"([a-z]+\d+:[a-z]+\d+)\s*에?.*(헤더|header).*(써|작성|입력|write|set|fill)",
        lowered,
    )
    if not header_match:
        range_match = re.search(r"([a-z]+\d+:[a-z]+\d+)", lowered)
        has_header = bool(re.search(r"(헤더|header)", lowered))
        has_write_verb = bool(re.search(write_verbs, lowered))
        if range_match and has_header and has_write_verb:
            range_ref = range_match.group(1).upper()
            left, right = range_ref.split(":")
            left_col = re.match(r"([A-Z]+)\d+", left)
            right_col = re.match(r"([A-Z]+)\d+", right)
            row_match = re.match(r"[A-Z]+(\d+)", left)
            if left_col and right_col and row_match:
                start_col = left_col.group(1)
                end_col = right_col.group(1)
                row_no = row_match.group(1)
                col_count = _column_span(start_col, end_col)
                values = [[f"헤더{i+1}" for i in range(col_count)]]
                return {
                    "action": "excel_live.write_range",
                    "params": {
                        "start_cell": f"{start_col}{row_no}",
                        "values_2d": values,
                    },
                    "reason": "헤더 행 입력 요청",
                }
    if header_match:
        range_ref = header_match.group(1).upper()
        left, right = range_ref.split(":")
        left_col = re.match(r"([A-Z]+)\d+", left)
        right_col = re.match(r"([A-Z]+)\d+", right)
        row_match = re.match(r"[A-Z]+(\d+)", left)
        if left_col and right_col and row_match:
            start_col = left_col.group(1)
            end_col = right_col.group(1)
            row_no = row_match.group(1)
            col_count = _column_span(start_col, end_col)
            values = [[f"헤더{i+1}" for i in range(col_count)]]
            return {
                "action": "excel_live.write_range",
                "params": {
                    "start_cell": f"{start_col}{row_no}",
                    "values_2d": values,
                },
                "reason": "헤더 행 입력 요청",
            }

    # 예: "B2:D2에 이름,수량,금액 입력"
    #     "A2:C4에 가,1,2; 나,3,4; 다,5,6 입력"  ← 세미콜론·줄바꿈이 행 구분자
    row_write_step = parse_explicit_row_write(text)
    if row_write_step is not None:
        return row_write_step

    # 예: "A1에 120 입력", "C3 셀에 777 입력해줘", "C3 값을 777로 입력", "C3 777 입력"
    single_write_patterns = [
        r"([a-z]+\d+)\s*(?:셀)?\s*에\s*(?:값\s*)?['\"]?([^'\"]+?)['\"]?\s*(?:을|를)?\s*(?:로)?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)",
        r"([a-z]+\d+)\s*(?:셀)?\s*값(?:을|를)?\s*['\"]?([^'\"]+?)['\"]?\s*(?:로)?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)",
        r"\b([a-z]+\d+)\s+['\"]?([^'\"]+?)['\"]?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)\b",
    ]
    # 셀은 지목했는데 넣을 값이 없는 문장("H1에 넣어줘")을 만났는지.
    valueless_cell_write = False
    for pattern in single_write_patterns:
        single_write = re.search(pattern, text, re.IGNORECASE)
        if not single_write:
            continue
        cell = single_write.group(1).upper()
        raw_value = re.sub(r"\s*(?:값|value)\s*$", "", single_write.group(2).strip(), flags=re.IGNORECASE)
        # "합계 라고 입력해줘"의 '라고'는 인용 조사지 값이 아니다.
        # 2026-08-17 배터리 실측: A12에 '합계 라고'가 그대로 들어갔다.
        raw_value = re.sub(r"\s*이?라고\s*$", "", raw_value)
        if "수식" in raw_value or "formula" in raw_value.lower() or "=" in raw_value:
            continue
        if not raw_value:
            valueless_cell_write = True
            continue
        value = _parse_literal_value(raw_value)
        return {
            "action": "excel_live.write_range",
            "params": {"start_cell": cell, "values_2d": [[value]]},
            "reason": "단일 셀 값 입력 요청",
        }

    if valueless_cell_write:
        # "H1에 넣어줘" — 셀은 있는데 넣을 값이 없다. 계획을 만들면 values_2d=[[""]]가 되어
        # 빈 칸을 쓰고도 성공으로 보고된다(2026-08-16 실측: 되묻기 다음 턴의 '총매출'이
        # 이렇게 유실됐고, 검증기는 빈 값 대 빈 셀을 같다고 보아 통과시켰다).
        # 아래 "선택 셀 입력" 규칙으로 흘려도 안 된다 — 문장 자체("H1에")를 값으로 써 버린다.
        # 규칙으로는 못 푸는 문장이므로 계획을 만들지 않고 플래너·되묻기에 넘긴다.
        return None

    # 예: "777 입력해줘" (셀 미지정) -> 현재 선택 셀에 기록
    implicit_single_write = re.search(
        r"^\s*['\"]?([^'\"]+?)['\"]?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)\s*$",
        text,
        re.IGNORECASE,
    )
    if (
        implicit_single_write
        # 서식·구조 어휘가 든 문장은 "그 말을 값으로 쓰라"는 뜻이 아니다.
        # 2026-08-17 배터리 실측: "금액에 천 단위 콤마 넣어줘"가 이 규칙에 걸려
        # 셀에 '금액에 천 단위 콤마'라는 **텍스트**가 들어갔다 — 규칙이 정답
        # (set_number_format)을 이미 내놨는데도.
        and not re.search(
            r"(수식|formula|헤더|header|색|highlight|강조|표시|열|column|row"
            r"|콤마|쉼표|서식|포맷|형식|소수점|퍼센트|%|자리|정렬|테두리|경계선"
            r"|필터|차트|그래프|굵게|기울임|밑줄|병합)",
            lowered,
        )
    ):
        raw_value = implicit_single_write.group(1).strip()
        raw_value = re.sub(r"\s*이?라고\s*$", "", raw_value)
        if "수식" not in raw_value and "formula" not in raw_value.lower() and "=" not in raw_value:
            value = _parse_literal_value(raw_value)
            return {
                "action": "excel_live.write_range",
                "params": {"start_cell": "__ACTIVE_CELL__", "values_2d": [[value]]},
                "reason": "선택 셀 값 입력 요청",
            }

    # 예: "C7 777 set", "B2 done write"
    single_write_en = re.search(
        r"\b([a-z]+\d+)\s+['\"]?([^'\"]+?)['\"]?\s+(?:write|set|input)\b",
        text,
        re.IGNORECASE,
    )
    if single_write_en:
        cell = single_write_en.group(1).upper()
        raw_value = single_write_en.group(2).strip()
        if "formula" not in raw_value.lower() and "=" not in raw_value:
            value = _parse_literal_value(raw_value)
            return {
                "action": "excel_live.write_range",
                "params": {"start_cell": cell, "values_2d": [[value]]},
                "reason": "영문 단일 셀 값 입력 요청",
            }

    # 예: "A1:B10에 수식 =SUM(C1:C10) 적용"
    formula_match = re.search(
        r"([a-z]+\d+:[a-z]+\d+|[a-z]+\d+).*(수식|formula).*(=[^\n]+?)\s*(?:적용|넣|써|set|$)",
        text,
        re.IGNORECASE,
    )
    if formula_match:
        formula_a1 = re.sub(
            r"\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set)\s*$",
            "",
            formula_match.group(3).strip(),
            flags=re.IGNORECASE,
        )
        return {
            "action": "excel_live.set_formula",
            "params": {
                "range_ref": formula_match.group(1).upper(),
                "formula_a1": formula_a1,
            },
            "reason": "범위 수식 적용 요청",
        }

    # 예: "수식 =SUM(A1:A10) 적용" (범위 미지정) -> 현재 선택 범위에 적용
    implicit_formula = re.search(
        r"(수식|formula).*(=[^\n]+?)\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set|$)",
        text,
        re.IGNORECASE,
    )
    if implicit_formula:
        formula_a1 = re.sub(
            r"\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set)\s*$",
            "",
            implicit_formula.group(2).strip(),
            flags=re.IGNORECASE,
        )
        return {
            "action": "excel_live.set_formula",
            "params": {"range_ref": "__ACTIVE_SELECTION__", "formula_a1": formula_a1},
            "reason": "선택 범위 수식 적용 요청",
        }

    # 예: "엑셀 저장해줘", "save workbook"
    if re.search(r"(저장(?:해(?:줘)?)?|save)", lowered):
        return {
            "action": "excel_live.save_workbook",
            "params": {},
            "reason": "통합문서 저장 요청",
        }

    return None


def _ensure_action_step(step: dict[str, Any]) -> dict[str, Any]:
    action = str(step.get("action", "")).strip()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")
    params = step.get("params", {})
    if not isinstance(params, dict):
        params = {}
    reason = str(step.get("reason", "")).strip()
    return {"action": action, "params": params, "reason": reason}


PLAN_INTENTS = frozenset({"edit", "read", "navigate", "clarify"})


def clarify_question_from_plan(action_plan: list[dict[str, Any]] | None) -> str:
    """계획이 '되묻기'면 질문 문장을, 아니면 빈 문자열을 돌려준다.

    되묻기는 첫 단계일 때만 인정한다. 뒤에 붙은 clarify는 "실행하고 나서 묻겠다"는
    뜻이 되는데, 그건 이미 사용자 데이터를 바꾼 뒤라 되묻는 의미가 없다.
    """
    if not action_plan:
        return ""
    first = action_plan[0]
    if str(first.get("action", "")).strip() != "excel_live.clarify":
        return ""
    params = first.get("params") if isinstance(first.get("params"), dict) else {}
    return str(params.get("question") or params.get("follow_up_question") or "").strip()


def _assert_action_plan_contract(action_plan: list[dict[str, Any]]) -> None:
    if not action_plan:
        raise ValueError("LLM action_plan이 비어 있습니다.")
    if len(action_plan) > 4:
        raise ValueError("LLM action_plan 단계 수가 제한(4)을 초과했습니다.")
    if len(action_plan) > 1 and any(
        str(step.get("action", "")).strip() == "excel_live.clarify" for step in action_plan
    ):
        raise ValueError("clarify는 단독 계획이어야 합니다.")
    for idx, step in enumerate(action_plan, start=1):
        action = str(step.get("action", "")).strip()
        params = step.get("params")
        reason = str(step.get("reason", "")).strip()
        if not action.startswith("excel_live."):
            raise ValueError(f"step[{idx}] action 형식이 잘못되었습니다: {action}")
        if not isinstance(params, dict):
            raise ValueError(f"step[{idx}] params는 dict여야 합니다.")
        if len(reason) > 240:
            raise ValueError(f"step[{idx}] reason 길이가 너무 깁니다.")


async def parse_command_plan_with_llm(
    message: str,
    llm_service,
    *,
    context: dict[str, Any] | None = None,
    forbid_list_action: bool = False,
    require_edit_action: bool = False,
    forbid_clarify: bool = False,
) -> dict[str, Any]:
    """
    LLM 기반 계획형 파서.

    출력은 action_plan 배열을 우선으로 사용한다.
    - action_plan: [{"action": "...", "params": {...}, "reason": "..."}]
    """
    context = context or {}
    # 되묻기 억제는 호출자(라우터)가 세션 상태를 보고 정하기도 하고,
    # 아래 재계획 루프가 정하기도 한다. 둘 중 하나라도 막으면 막는다.
    forbid_clarify = forbid_clarify or bool(context.get("forbid_clarify"))
    planner_model = str(context.get("planner_model", "") or "").strip() or get_planner_model_name()
    # 에스컬레이션 사다리가 마지막 단계에서 강한 모델을 지목한다. 활성 프로바이더를
    # 갈아끼우지 않고 이 호출에서만 다른 서비스를 쓴다 — 다른 요청까지 클라우드로
    # 새어 나가면 "로컬에서만 처리한다"는 약속이 깨진다.
    if str(context.get("planner_provider", "")).strip() == "strong":
        strong = get_strong_llm_service()
        if strong is None:
            raise ValueError("강한 모델 플래너를 사용할 수 없습니다.")
        llm_service = strong
        planner_model = planner_model or get_strong_planner_model_name()
    # 프롬프트 조립은 excel_planner_prompt가 단일 소스 — SFT 데이터 생성도 같은 함수를 쓴다.
    prompt = build_planner_prompt(
        message,
        context=context,
        planner_model=planner_model,
        forbid_list_action=forbid_list_action,
        require_edit_action=require_edit_action,
        forbid_clarify=forbid_clarify,
    )
    started = time.perf_counter()
    raw = await llm_service.chat(
        [{"role": "user", "content": prompt}],
        model=planner_model or None,
        temperature=PLAN_TEMPERATURE,
        json_only=True,
        timeout=http_budget_for(context.get("parse_timeout_seconds")),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    # 계획 오브젝트를 우선 집는다. 모델이 프롬프트의 출력 예시를 먼저 따라 쓰고
    # 진짜 답을 뒤에 붙이면, 앞의 것을 집는 순간 예시를 실행하게 된다.
    parsed = extract_json_object(raw, require_keys=("action_plan", "action"))
    # 모델이 뭘 돌려줬는지 원본으로 남긴다. 파싱 실패를 "모델이 이상한 걸 뱉었다"와
    # "파서가 못 잡았다"로 갈라 보려면 이 문자열이 있어야 한다.
    # 모델이 무엇을 보고 답했는지는 `build_planner_prompt`가 planner_context로 남긴다.
    trace_note(
        "llm_call",
        purpose="planner",
        model=planner_model or "(기본)",
        forbid_list_action=forbid_list_action,
        require_edit_action=require_edit_action,
        forbid_clarify=forbid_clarify,
        prompt_chars=len(prompt),
        elapsed_ms=elapsed_ms,
        raw_response=Long(raw),
        json_found=parsed is not None,
    )
    if parsed is None:
        trace_route("planner:json_missing", why="응답에서 JSON 블록을 찾지 못함")
        raise ValueError("LLM 계획 JSON 파싱 실패")

    steps_raw = parsed.get("action_plan")
    if isinstance(steps_raw, list) and steps_raw:
        action_plan = []
        for raw_step in steps_raw[:4]:
            if isinstance(raw_step, dict):
                action_plan.append(_ensure_action_step(raw_step))
        _assert_action_plan_contract(action_plan)
        clarify_question = clarify_question_from_plan(action_plan)
        intent = str(parsed.get("intent", "")).strip().lower()
        if clarify_question:
            intent = "clarify"
        elif intent not in PLAN_INTENTS:
            intent = "unknown"
        return {
            "action_plan": action_plan,
            "reason": str(parsed.get("reason", "")).strip(),
            "intent": intent,
            "mutates_workbook": bool(parsed.get("mutates_workbook", intent == "edit"))
            and not clarify_question,
            "slot_fill": parsed.get("slot_fill") if isinstance(parsed.get("slot_fill"), dict) else {},
            "partial_params": (
                parsed.get("partial_params") if isinstance(parsed.get("partial_params"), dict) else {}
            ),
            # 모델이 clarify 단계만 내고 상위 follow_up_question을 비워 두는 경우가 잦다.
            # 질문은 단계 안에 이미 있으므로 그걸 끌어올린다.
            "follow_up_question": str(parsed.get("follow_up_question", "")).strip()
            or clarify_question,
        }

    # 하위 호환: action/params 단일 형태도 수용
    single = _ensure_action_step(parsed)
    _assert_action_plan_contract([single])
    clarify_question = clarify_question_from_plan([single])
    intent = str(parsed.get("intent", "")).strip().lower()
    if clarify_question:
        intent = "clarify"
    elif intent not in PLAN_INTENTS:
        intent = "unknown"
    return {
        "action_plan": [single],
        "reason": str(parsed.get("reason", "")).strip(),
        "intent": intent,
        "mutates_workbook": bool(parsed.get("mutates_workbook", intent == "edit"))
        and not clarify_question,
        "slot_fill": parsed.get("slot_fill") if isinstance(parsed.get("slot_fill"), dict) else {},
        "partial_params": parsed.get("partial_params") if isinstance(parsed.get("partial_params"), dict) else {},
        "follow_up_question": str(parsed.get("follow_up_question", "")).strip() or clarify_question,
    }


async def parse_excel_live_command(
    message: str,
    llm_service,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    lowered = str(message or "").lower()
    # 이해는 범용 모델에게, 좌표는 코드에게 (2026-08-17 실측: 같은 36문장에서
    # 정규화 100%/96% vs 플래너 67%/58% — 플래너의 실패는 표현이 아니라 파라미터
    # 암기였다. =SUM(E:E)를 여섯 표현 모두에 냈다). 정규화·매핑이 성공하면
    # 플래너를 부르지 않는다. 실패는 어떤 이유든 조용히 플래너로 폴백한다.
    # 재계획(직전 실행 실패)은 정규화를 건너뛴다. 정규화 프롬프트는 실패 정보를
    # 모르므로 원문을 다시 정규화하면 **같은 계획이 또 나와 같은 실패를 반복**한다.
    # 플래너 경로는 `render_execution_failure`로 실패한 액션·인자·원인을 프롬프트에
    # 받는다 — 검증 실패 되먹임 폐루프(로드맵 2-1, SheetCopilot ablation 최대 요인)는
    # 그 경로에만 존재한다.
    is_replan = bool(str(context.get("failed_error") or "").strip())
    if not context.get("skip_intent_normalizer") and not is_replan:
        try:
            intent = await normalize_intent(
                message, context.get("workbook_digest"), llm_service
            )
            normalized = intent_to_plan(
                intent, digest=context.get("workbook_digest"), message=message
            )
        except Exception:
            normalized = None
        if normalized is not None:
            trace_note(
                "llm_call",
                purpose="intent_normalizer",
                task=str((intent or {}).get("task") or ""),
                mapped_action=str(normalized.get("action") or ""),
            )
            return normalized
    # 목록 조회는 어느 모드에서든 편집 요청의 답이 아니다. 관측(read_range·
    # validate_data)만 모드에 따라 허용한다 — 그게 이번 실험의 변수다.
    non_edit_actions = {
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
    }
    if not excel_observation.allows_read_first():
        non_edit_actions |= set(excel_observation.OBSERVATION_ACTIONS)
    # 에이전트 단일 경로: 규칙 파서 없이 LLM 플래너만 사용한다.
    try:
        planned = await parse_command_plan_with_llm(
            message,
            llm_service,
            context=context,
        )
        action_plan = planned["action_plan"]
        intent = str(planned.get("intent", "unknown")).lower()
        # 되묻기는 아래 재계획 규칙(목록 조회 금지·편집 액션 강제)의 대상이 아니다.
        # 그 규칙들은 "실행해야 하는데 엉뚱한 걸 골랐다"를 고치는 장치인데,
        # 되묻기는 아직 실행하지 않기로 한 판단이라 강제로 편집을 시키면 뜻이 뒤집힌다.
        if clarify_question_from_plan(action_plan):
            first = action_plan[0]
            return {
                "action_plan": action_plan,
                "action": first["action"],
                "params": first["params"],
                "reason": planned.get("reason", "") or first.get("reason", ""),
                "intent": "clarify",
                "slot_fill": planned.get("slot_fill", {}),
                "partial_params": planned.get("partial_params", {}),
                "follow_up_question": planned.get("follow_up_question", ""),
            }
        explicit_list_intent = any(
            token in lowered
            for token in [
                "열린 통합문서",
                "워크북 목록",
                "열린 파일 목록",
                "list workbooks",
                "workbook list",
            ]
        )
        likely_edit_intent = intent == "edit" or (
            intent == "unknown" and _has_likely_edit_intent(message)
        )
        # 명시적 목록 조회가 아닌데 list_workbooks가 나오면 1회 재계획한다.
        if (
            not explicit_list_intent
            and action_plan
            and action_plan[0].get("action") == "excel_live.list_workbooks"
        ):
            planned = await parse_command_plan_with_llm(
                message,
                llm_service,
                context=context,
                forbid_list_action=True,
                forbid_clarify=True,
            )
            action_plan = planned["action_plan"]
            if (
                action_plan
                and action_plan[0].get("action") == "excel_live.list_workbooks"
                and likely_edit_intent
            ):
                raise ValueError("편집 의도 명령을 목록 조회로 해석했습니다.")
        # 편집 의도인데 관찰/조회 액션으로 끝나면 강제 재계획한다.
        if likely_edit_intent and action_plan and action_plan[0].get("action") in non_edit_actions:
            planned = await parse_command_plan_with_llm(
                message,
                llm_service,
                context=context,
                forbid_list_action=True,
                require_edit_action=True,
                forbid_clarify=True,
            )
            action_plan = planned["action_plan"]
            if action_plan and action_plan[0].get("action") in non_edit_actions:
                raise ValueError("편집 의도 명령을 편집 액션으로 계획하지 못했습니다.")
        first = action_plan[0]
        return {
            "action_plan": action_plan,
            "action": first["action"],
            "params": first["params"],
            "reason": planned.get("reason", "") or first.get("reason", ""),
            "intent": intent,
            "slot_fill": planned.get("slot_fill", {}),
            "partial_params": planned.get("partial_params", {}),
            "follow_up_question": planned.get("follow_up_question", ""),
        }
    except Exception as exc:
        raise ValueError(
            "엑셀 명령을 해석하지 못했습니다. 범위/동작을 함께 적어주세요. 예: 'B2:D5 범위에 경계선 적용해줘'"
        ) from exc


def _column_span(start_col: str, end_col: str) -> int:
    def col_to_num(col: str) -> int:
        n = 0
        for ch in col:
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n

    start = col_to_num(start_col)
    end = col_to_num(end_col)
    return max(1, end - start + 1)


_DATE_TOKEN_PATTERN = re.compile(r"\b\d{2,4}[./-]\d{1,2}[./-]\d{1,2}\b")


def _clean_table_cell(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _split_table_line(line: str, *, allow_comma: bool = False) -> list[str]:
    raw = str(line or "").strip()
    if not raw:
        return []
    parts: list[str] | None = None
    if "\t" in raw:
        parts = raw.split("\t")
    elif "|" in raw:
        parts = raw.split("|")
    elif allow_comma and "," in raw:
        parts = re.split(r"\s*,\s*", raw)
    if parts is None:
        return []
    out: list[str] = []
    for p in parts:
        cell = _clean_table_cell(p)
        if cell:
            out.append(cell)
    return out


def _normalize_values_2d(rows: list[list[Any]]) -> list[list[str]]:
    normalized: list[list[str]] = []
    max_cols = 0
    for raw_row in rows[:100]:
        cells = [_clean_table_cell(v) for v in raw_row]
        if not any(cells):
            continue
        normalized.append(cells)
        max_cols = max(max_cols, len(cells))
    if not normalized:
        return []
    max_cols = max(1, min(50, max_cols))
    out: list[list[str]] = []
    for row in normalized[:100]:
        trimmed = row[:max_cols]
        if len(trimmed) < max_cols:
            trimmed.extend([""] * (max_cols - len(trimmed)))
        out.append(trimmed)
    return out


def _detect_compact_expense_headers(header_text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", str(header_text or "")).strip()
    if not compact:
        return []
    # 탭/구분자 없이 붙여넣은 법인카드 내역 문장을 7열 표로 복원한다.
    must_tokens = ["날짜", "사용 목적", "사용처", "금액", "비용 유형"]
    if all(token in compact for token in must_tokens):
        return [
            "날짜",
            "사용 목적",
            "사용처",
            "법인카드 사용내역서 여부",
            "금액",
            "법인카드/조교카드 이체 여부",
            "비용 유형",
        ]
    return []


def _parse_compact_expense_row(segment: str) -> list[str] | None:
    m = re.match(
        r"^\s*(\d{2,4}[./-]\d{1,2}[./-]\d{1,2})\s+(.+?)\s+(O|X|-)\s+(-?\d[\d,]*(?:\.\d+)?)\s+(O|X|-)\s+(.+?)\s*$",
        str(segment or ""),
        re.IGNORECASE,
    )
    if not m:
        return None
    date = _clean_table_cell(m.group(1))
    purpose_place = _clean_table_cell(m.group(2))
    statement_flag = _clean_table_cell(m.group(3)).upper()
    amount = _clean_table_cell(m.group(4))
    transfer_flag = _clean_table_cell(m.group(5)).upper()
    category = _clean_table_cell(m.group(6))

    tokens = purpose_place.split()
    if len(tokens) >= 2:
        place = tokens[-1]
        purpose = " ".join(tokens[:-1]).strip()
    else:
        purpose = purpose_place
        place = ""
    return [date, purpose, place, statement_flag, amount, transfer_flag, category]


def _parse_compact_generic_row(segment: str, col_count: int) -> list[str] | None:
    compact = re.sub(r"\s+", " ", str(segment or "")).strip()
    if not compact:
        return None
    if col_count <= 1:
        return [_clean_table_cell(compact)]
    tokens = compact.split(" ")
    if len(tokens) >= col_count:
        row = [*tokens[: col_count - 1], " ".join(tokens[col_count - 1 :])]
    else:
        row = tokens + [""] * (col_count - len(tokens))
    return [_clean_table_cell(v) for v in row[:col_count]]


def _extract_inline_table_values_2d(text: str) -> list[list[str]]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    # 1) 줄바꿈+구분자(탭/파이프/콤마) 기반 표 추출
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) >= 2:
        header_idx = -1
        header: list[str] = []
        for idx, line in enumerate(lines):
            parsed_header = _split_table_line(line, allow_comma=True)
            if len(parsed_header) >= 2:
                header_idx = idx
                header = parsed_header
                break
        if header_idx >= 0 and len(header) >= 2:
            body_rows: list[list[str]] = []
            for line in lines[header_idx + 1 :]:
                parsed = _split_table_line(line, allow_comma=False)
                if not parsed and line.count(",") >= 2 and not re.search(r"\d{1,3},\d{3}", line):
                    parsed = _split_table_line(line, allow_comma=True)
                if parsed:
                    body_rows.append(parsed)
            if body_rows:
                matrix = _normalize_values_2d([header, *body_rows])
                if len(matrix) >= 2:
                    return matrix

    # 2) 날짜 토큰이 연속되는 압축 문장(공백 구분) 복원
    date_matches = list(_DATE_TOKEN_PATTERN.finditer(raw))
    if not date_matches:
        return []
    header_block = raw[: date_matches[0].start()].strip()
    if not header_block:
        return []
    headers = _detect_compact_expense_headers(header_block)
    if len(headers) < 2:
        headers = _split_table_line(header_block, allow_comma=True)
    if len(headers) < 2:
        return []

    rows: list[list[str]] = []
    col_count = len(headers)
    for idx, match in enumerate(date_matches):
        seg_start = match.start()
        seg_end = date_matches[idx + 1].start() if idx + 1 < len(date_matches) else len(raw)
        segment = raw[seg_start:seg_end].strip()
        if not segment:
            continue
        parsed_row: list[str] | None = None
        if col_count == 7 and "금액" in headers and "비용 유형" in headers:
            parsed_row = _parse_compact_expense_row(segment)
        if parsed_row is None:
            parsed_row = _parse_compact_generic_row(segment, col_count)
        if parsed_row:
            rows.append(parsed_row)
    if not rows:
        return []
    matrix = _normalize_values_2d([headers, *rows])
    return matrix if len(matrix) >= 2 else []


_TABLE_ROW_UNITS = ("행", "줄", "세로")
_TABLE_COL_UNITS = ("열", "칸", "가로")
_TABLE_AXIS_PATTERN = re.compile(
    r"(?:(?P<count_first>\d{1,3})\s*개?\s*(?P<unit_last>행|줄|세로|열|칸|가로)"
    r"|(?P<unit_first>행|줄|세로|열|칸|가로)\s*(?:은|는|이|가)?\s*[:=]?\s*(?P<count_last>\d{1,3})\s*개?)"
)
# "4*4"뿐 아니라 "4열*4행"처럼 단위가 붙은 크기 표기도 헤더 후보에서 걷어낸다.
_TABLE_SIZE_SPEC_PATTERN = re.compile(
    r"\d{1,3}\s*(?:\*|x|×|by)\s*\d{1,3}|\d{1,3}\s*개?\s*(?:행|줄|세로|열|칸|가로)",
    re.IGNORECASE,
)
# 사람이 헤더를 나열할 때 가장 흔한 형태. 따옴표 안은 통째로 한 항목이다.
_QUOTED_ITEM_PATTERN = re.compile(
    r"'([^'\n]{1,60})'|\"([^\"\n]{1,60})\"|\u2018([^\u2019\n]{1,60})\u2019|\u201c([^\u201d\n]{1,60})\u201d"
)
# "머리글은 이름, 점수"처럼 나열을 여는 안내말. 이 말 뒤부터가 머리글이다.
# 조사나 콜론을 반드시 요구한다 — "4열*3행, 제목, 사양"의 "제목"처럼 나열의 항목으로
# 쓰인 같은 낱말을 안내말로 오인하면 그 항목이 통째로 사라진다.
# "제목"을 목록에서 뺀 이유도 같다. 머리글보다 표 이름을 가리킬 때가 많다.
_HEADER_LEAD_IN_PATTERN = re.compile(
    r"(?:머리글|머리말|헤더행|헤더|컬럼|칼럼|열\s*이름|필드명|필드)"
    r"(?:\s*(?:은|는|이|가|에는|에|으로|로)|\s*[:=])\s*"
)
_TABLE_TAIL_PATTERN = re.compile(
    r"(?:표|테이블|table)\s*(?:로|을|를)?\s*(?:만들어줘|생성해줘|create.*)?$",
    re.IGNORECASE,
)


def _extract_quoted_headers(text: str) -> list[str]:
    """따옴표로 감싼 항목을 헤더 목록으로 뽑는다.

    두 개 이상 있을 때만 목록으로 본다. 하나뿐이면 헤더 나열이 아니라
    그냥 강조하거나 인용한 말일 가능성이 크다.
    """
    items: list[str] = []
    for match in _QUOTED_ITEM_PATTERN.finditer(str(text or "")):
        value = next((group for group in match.groups() if group is not None), "").strip()
        if value:
            items.append(value)
    return items if len(items) >= 2 else []


_ROW_WRITE_PATTERN = re.compile(
    r"\b([a-z]+\d+:[a-z]+\d+)\s*에\s*((?:[^\n]|\n)+?)\s*(입력(?:해)?|써|작성|적어|넣어|write|set)\b",
    re.IGNORECASE,
)
# 값 자리에 이런 낱말이 오면 값이 아니라 서식·차트 명령일 가능성이 크다 —
# 선점(strong) 모드에서는 쓰기로 채가지 않고 원래 규칙에 맡긴다.
_ROW_WRITE_FORMAT_VOCAB = re.compile(
    r"(콤마|서식|형식|퍼센트|굵게|기울임|테두리|경계선|배경|색상|차트|그래프|데이터\s*막대|병합|정렬|필터|틀\s*고정)"
)


def parse_explicit_row_write(text: str, *, strong_verb_only: bool = False) -> dict | None:
    """"범위에 값,값 입력" 완결 쓰기를 파싱한다. 세미콜론·줄바꿈이 행 구분자다.

    strong_verb_only는 값 낱말("예측", "순위")이 다른 키워드 규칙을 켠 문장
    위에 씌우는 선점용이다 — 이때는 "넣어"류를 빼고(데이터 막대 넣어줘와
    충돌) 서식 어휘가 값에 섞이면 물러난다. 2026-08-18 ex5 재현 실측:
    "A7:F7에 순위,…,영향예측 입력"의 '예측'이 forecast_linear 퀵 규칙에
    잡혀 라벨 행이 통째로 사라졌다.
    """
    source = str(text or "")
    lowered = source.lower()
    row_write = _ROW_WRITE_PATTERN.search(source)
    if not row_write or "헤더" in lowered or "header" in lowered:
        return None
    if strong_verb_only:
        verb = row_write.group(3)
        if verb.startswith(("넣", "적")):
            return None
        if _ROW_WRITE_FORMAT_VOCAB.search(row_write.group(2)):
            return None
    range_ref = row_write.group(1).upper()
    left, right = range_ref.split(":")
    left_col = re.match(r"([A-Z]+)\d+", left)
    right_col = re.match(r"([A-Z]+)\d+", right)
    row_match = re.match(r"[A-Z]+(\d+)", left)
    if not (left_col and right_col and row_match):
        return None
    raw_values = row_write.group(2).strip()
    # 수식 명령은 write_range가 아니라 set_formula 경로로 내려가야 한다.
    if "수식" in raw_values or "formula" in raw_values.lower() or "=" in raw_values:
        return None
    col_count = _column_span(left_col.group(1), right_col.group(1))
    # 세미콜론·줄바꿈은 행 구분자다. "한 턴 = 한 행"만 되면 표 하나에
    # 수십 턴이 든다(2026-08-18, 85턴 재현 대화가 과하다는 지적).
    # 행 안은 기존 그대로 쉼표 나열이다.
    row_groups = [g.strip() for g in re.split(r"[;\n]", raw_values) if g.strip()]
    rows_2d = []
    for group in row_groups:
        tokens = _split_header_tokens(group)
        if not tokens:
            continue
        row_values = [_parse_literal_value(t) for t in tokens[:col_count]]
        if len(row_values) < col_count:
            row_values.extend([""] * (col_count - len(row_values)))
        rows_2d.append(row_values)
    if not rows_2d:
        return None
    return {
        "action": "excel_live.write_range",
        "params": {"start_cell": left, "values_2d": rows_2d},
        "reason": (
            "행 범위 값 입력 요청"
            if len(rows_2d) == 1
            else f"여러 행({len(rows_2d)}) 값 일괄 입력 요청"
        ),
    }


def _split_header_tokens(source: str) -> list[str]:
    """나열을 토큰으로 쪼갠다 — 쉼표가 있으면 쉼표만 구분자로 쓴다.

    날짜("07/05")처럼 값 안에 빗금이 흔해서, 쉼표 나열에 빗금까지 구분자로
    쓰면 열이 밀린다(2026-08-18 ex2 실측: "토 07/05" → "토 07"과 "05" 두 칸).
    빗금·세로줄은 쉼표가 아예 없는 나열에서만 구분자다.
    """
    text = str(source or "")
    if "," in text:
        return [token.strip() for token in text.split(",") if token.strip()]
    return [token.strip() for token in re.split(r"[/|]", text) if token.strip()]


def _narrow_to_header_clause(source: str) -> str:
    """"머리글은 이름, 점수"처럼 안내말이 앞서면 그 뒤만 남긴다.

    안내말 뒤에 나열이 없으면 그대로 둔다. "날짜 헤더로 표 만들어줘"의 "헤더"는
    나열을 여는 말이 아니라 이미 끝난 나열을 닫는 말이라, 뒤만 취하면 머리글이 사라진다.
    """
    for match in _HEADER_LEAD_IN_PATTERN.finditer(source):
        tail = source[match.end() :]
        if len(_split_header_tokens(tail)) >= 2:
            return tail
    return source


def _strip_list_particle(token: str) -> str:
    """나열을 표에 잇는 조사를 뗀다 — "이름, 점수로 표 만들어줘"의 "점수로".

    표를 가리키던 꼬리가 실제로 잘려 나간 토큰에만 쓴다. 아무 데나 적용하면
    "확인"의 "인"까지 조사로 본다. 그래도 남는 위험이 있어 한 글자만 남으면 되돌린다.
    """
    stripped = re.sub(r"(?:으로|로|인|이렇게)$", "", token).strip()
    return stripped if len(stripped) >= 2 else token


def _extract_listed_headers(source: str) -> list[str]:
    """쉼표로 나열한 머리글을 뽑는다. 앞뒤에 붙은 지시문은 걷어낸다."""
    tokens = _split_header_tokens(_narrow_to_header_clause(source))
    if len(tokens) < 2:
        return []

    headers: list[str] = []
    for index, token in enumerate(tokens):
        candidate = token
        # 안내말 없이 "표 만들어줘. 이름, 점수"로 오면 첫 토큰에 앞 문장이 통째로 붙는다.
        if index == 0:
            candidate = re.split(r"[.。!?\n]", candidate)[-1].strip()
            # 구두점 없이 "…표를 만들어줘 날짜, 이름"으로 이어지면 동사 뒤가 목록이다.
            # 2026-08-18 GUI 실측: 첫 헤더가 "여기에 출석부 형태로 표를 만들어줘 날짜"
            # 통째로 잡혔다.
            verb_split = re.split(r"(?:만들어\s*줘|만들어|넣어\s*줘|넣어|해\s*줘|주고)\s+", candidate)
            if len(verb_split) > 1 and verb_split[-1].strip():
                candidate = verb_split[-1].strip()
        # "…비고 이렇게 헤더를 만들어줘"의 마지막 토큰 꼬리.
        candidate = re.sub(r"\s*이렇게\b.*$", "", candidate).strip()
        candidate = re.sub(r"\s*(?:헤더|컬럼|열)\s*(?:를|을)?\s*(?:만들|넣|해|지정).*$", "", candidate).strip()
        # 크기 표기를 걷어내고 남은 "*", "로" 같은 찌꺼기는 헤더가 아니다.
        if not re.search(r"[0-9A-Za-z가-힣]", candidate):
            continue
        candidate = re.sub(
            r"^(?:크기로|헤더는|헤더|컬럼은|컬럼)\s*", "", candidate, flags=re.IGNORECASE
        ).strip()
        without_tail = _TABLE_TAIL_PATTERN.sub("", candidate).strip()
        had_table_tail = without_tail != candidate
        candidate = without_tail
        # "금액, 장소, 날짜 헤더로 표 만들어줘"의 마지막 토큰에 붙는 꼬리를 뗀다.
        candidate = re.sub(r"\s*(?:헤더|컬럼|열)\s*(?:로|으로|는|은)?\s*$", "", candidate).strip()
        if had_table_tail:
            candidate = _strip_list_particle(candidate)
        if candidate:
            headers.append(candidate)
    return headers if len(headers) >= 2 else []


def _extract_axis_table_size(text: str) -> tuple[int | None, int | None]:
    """
    "4열*4행", "세로 4 가로 3", "행 4개 열 3개"처럼 단위가 붙은 크기 표기를 읽는다.

    "4행 4열" 순서만 알아듣던 탓에 열을 먼저 말하면 같은 질문이 반복됐다.
    """
    rows: int | None = None
    cols: int | None = None
    for match in _TABLE_AXIS_PATTERN.finditer(text):
        unit = match.group("unit_last") or match.group("unit_first")
        raw_count = match.group("count_first") or match.group("count_last")
        if not unit or not raw_count:
            continue
        count = int(raw_count)
        if unit in _TABLE_ROW_UNITS and rows is None:
            rows = max(1, min(100, count))
        elif unit in _TABLE_COL_UNITS and cols is None:
            cols = max(1, min(50, count))
    return rows, cols


def extract_create_table_slot_hints(message: str) -> dict[str, Any]:
    """
    create_table 멀티턴 슬롯필링용 힌트를 자연어에서 추출한다.

    반환 키:
    - rows: int | None
    - cols: int | None
    - headers: list[str]
    - values_2d: list[list[str]]
    - start_cell: str | None
    - table_intent: bool
    """
    text = str(message or "").strip()
    lowered = text.lower()
    preset = match_table_preset(text)

    # "집계표 만들어줘"의 '표'는 빈 격자가 아니라 피벗이다.
    table_scan = (
        lowered.replace("집계표", " ")
        .replace("피벗 테이블", " ")
        .replace("피벗테이블", " ")
        .replace("pivot table", " ")
    )
    table_intent = (
        any(token in table_scan for token in ["표", "테이블", "table"])
        and any(token in table_scan for token in ["만들", "생성", "create", "작성"])
    )
    if preset is not None:
        table_intent = True

    rows: int | None = None
    cols: int | None = None
    m = re.search(r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})", lowered)
    if not m:
        m = re.search(r"(\d{1,3})\s*행\s*(\d{1,3})\s*열", lowered)
    if not m:
        m = re.search(r"(\d{1,3})\s*by\s*(\d{1,3})", lowered)
    if m:
        rows = max(1, min(100, int(m.group(1))))
        cols = max(1, min(50, int(m.group(2))))
    if rows is None or cols is None:
        axis_rows, axis_cols = _extract_axis_table_size(text)
        rows = rows if rows is not None else axis_rows
        cols = cols if cols is not None else axis_cols

    start_cell = _extract_range_ref(text)
    if start_cell and ":" in start_cell:
        start_cell = start_cell.split(":")[0]

    # 따옴표로 감싼 목록이 있으면 그게 가장 확실한 헤더다.
    # 쉼표 분해는 "'법인카드, 조교카드 이체 여부'"처럼 항목 안에 쉼표가 있으면 쪼개지고,
    # "헤더에는 '날짜'"나 "'비용 유형' 이렇게 만들어줄 수 있어?"처럼 앞뒤 문장이 붙어 들어온다.
    headers: list[str] = _extract_quoted_headers(text)
    if not headers:
        headers = _extract_listed_headers(_TABLE_SIZE_SPEC_PATTERN.sub(" ", text))

    values_2d = _extract_inline_table_values_2d(text)
    if values_2d:
        table_intent = True
        inferred_rows = len(values_2d)
        inferred_cols = max((len(row) for row in values_2d), default=0)
        if inferred_rows > 0:
            rows = max(1, min(100, max(rows or 0, inferred_rows)))
        if inferred_cols > 0:
            cols = max(1, min(50, max(cols or 0, inferred_cols)))
        parsed_headers = [str(v).strip() for v in values_2d[0] if str(v).strip()]
        if parsed_headers:
            headers = parsed_headers

    if looks_like_existing_table_convert(text) and not values_2d and rows is None and cols is None:
        table_intent = False

    return {
        "rows": rows,
        "cols": cols,
        "headers": headers,
        "values_2d": values_2d,
        "start_cell": start_cell,
        "table_intent": table_intent,
        "template_key": preset.key if preset else None,
        "template_headers": list(preset.headers) if preset else [],
        "template_rows": preset.default_rows if preset else None,
        "template_cols": preset.default_cols if preset else None,
        "template_follow_up_question": preset_follow_up(preset, text) if preset else "",
        "blank_table": any(token in lowered for token in ["빈 표", "빈표", "그냥 빈"]),
        "affirmative": any(
            token in lowered
            for token in ["응", "네", "좋아", "그대로", "그 정도", "맞아", "yes", "ok", "okay"]
        ),
        "convert_existing": looks_like_existing_table_convert(text)
        and not values_2d
        and rows is None
        and cols is None,
    }

