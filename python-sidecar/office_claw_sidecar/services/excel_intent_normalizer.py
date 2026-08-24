"""의도 정규화 — 이해는 범용 모델에게, 좌표는 코드에게.

2026-08-17 실측 (`scripts/measure_planner_generalization.py` vs
`scripts/measure_intent_normalizer.py`, 같은 36문장 + GUI 사고 문장 8건):

    플래너(ax7b SFT 1000건):  훈련체 67% / 사용자체 58%
    정규화(ax4-light, 학습 0): 훈련체 100% / 사용자체 96% / 사고 문장 8/8

작은 SFT의 실패는 표현이 아니라 **파라미터 암기**였다 — 여섯 표현 전부
`=SUM(E:E)`(없는 열), format_code="천단위"(낱말). 반면 범용 인스트럭트 모델은
"서울 아닌 데는 좀 치워줄래?" 같은 우회 표현까지 올바른 task로 분류했고 좌표를
한 번도 만들어내지 않았다.

그래서 역할을 가른다:
  1. 범용 모델: 문장 → 상징 의도 {task, 문장에 적힌 범위 그대로, 열은 머리글
     이름, 옵션 하나}. **좌표 생성 금지.**
  2. 이 모듈의 결정적 매퍼: 의도 → 액션 계획. 좌표·수식은 다이제스트를 보고
     코드가 조립한다.
  3. 매핑이 안 되는 의도(other, 복합 케이스)는 기존 플래너로 폴백.

매핑된 계획은 바인더(머리글→좌표 확정, 수식 검증)·검증기·승인 게이트를 그대로
지난다 — 정규화는 이해 계층일 뿐 안전 계층을 대체하지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

# 범위 모양·값 펴기는 바인더 것을 **빌려 쓴다**. 여기서 또 짜면 "1,000" 처리,
# 지시문 제외, 브로드캐스트 상한이 두 벌이 되어 반드시 갈라진다.
from .color_lexicon import COLOR_HEX
from .excel_live_service import _ALIGN_WORDS
from .excel_param_binder import _range_shape, _shape_write_values, sheet_entry
from .llm_json import extract_json_object
from .number_format_lexicon import format_code, format_code_in_text

# 정규화 호출 시간 예산. 이해 한 번이면 충분하다 — 플래너처럼 재시도 루프를 돌지 않는다.
NORMALIZE_TIMEOUT_SECONDS = 45.0

_PROMPT = """너는 Excel 명령 해석기다. 사용자 문장을 아래 JSON으로만 번역해라.

시트 머리글: {headers}

JSON 형식:
{{"task": "<작업>", "range": "<문장에 적힌 범위 그대로, 없으면 null>",
  "column": "<대상 열의 머리글 이름, 없으면 null>", "option": "<핵심 옵션, 없으면 null>"}}

task 목록: fill_color(배경색), font(글자 서식·색), highlight(조건에 맞는 셀만 강조),
number_format(표시 형식), formula(수식·계산), sort(정렬), filter(필터),
dedupe(중복 제거), clear_values(값 비우기), reset_all(서식까지 초기화),
create_table(표 생성), pivot(집계표·피벗), chart(차트), write_value(값 입력),
find_replace(찾아 바꾸기), read(조회), create_sheet(새 시트 만들기),
delete_charts(차트 삭제), freeze(행·열 틀 고정), autofit(열 너비 자동 맞춤),
other(그 외)

주의: 색칠에 조건(이상·이하·넘는·~인 셀만 같은 말)이 붙어 있으면 highlight,
조건이 없으면 **어떤 색이든 전부 fill_color**다.
option에는 그 문장에 실제로 나온 값만 적는다.

규칙:
- 범위·좌표를 **만들어내지 마라.** 문장에 적힌 것만 옮겨 적는다.
- 열은 좌표가 아니라 **시트 머리글에 실제로 있는** 이름으로 가리킨다. 문장의 낱말이
  머리글과 다르면(예: 매출↔금액) 가장 가까운 머리글을 골라라.
- option: 색 이름, asc/desc, 필터 값, SUM/AVERAGE/MAX/MIN/COUNT, 새 값 등 하나.
- 셀에 넣으라는 것이 **합계·총합·평균처럼 계산한 결과**면 write_value가 아니라
  formula다. write_value는 문장에 적힌 글자·숫자를 그대로 넣을 때만이다.

예시:
문장: "B2:B9 파란색으로 칠해줘"
{{"task": "fill_color", "range": "B2:B9", "column": null, "option": "파란색"}}
문장: "여기 C3:C7 좀 빨갛게 해줄래?"
{{"task": "fill_color", "range": "C3:C7", "column": null, "option": "빨간색"}}
문장: "매출 높은 순서로 보여줘"
{{"task": "sort", "range": null, "column": "금액", "option": "desc"}}
문장: "G1에 담당자별 평균 수식 넣어줘"
{{"task": "formula", "range": "G1", "column": "금액", "option": "AVERAGE"}}
문장: "D8에 지연건수 다 더한 값 넣어줘"
{{"task": "formula", "range": "D8", "column": "지연건수", "option": "SUM"}}

문장: "{message}"
JSON:"""

def _tasks_declared_in_prompt(prompt: str) -> tuple[str, ...]:
    """프롬프트의 `task 목록:` 줄에서 이름을 뽑는다 — **목록의 원본은 프롬프트 하나뿐**이다.

    같은 어휘를 두 곳에 두면 반드시 갈라진다(2026-08-20에 여러 번 데었다). 실제로
    `scripts/measure_intent_normalizer.py`에는 프롬프트 복제본이 있었고, 거기엔
    `highlight`가 통째로 빠져 있었다 — 그래서 계측이 프로덕션과 **다른 프롬프트**를
    재고 있었다(2026-08-23 확인). 집합을 손으로 또 적지 않고 여기서 파생시킨다.

    프롬프트를 고쳐 종류가 늘거나 줄면 이 집합도 따라 움직이고, 아래 회귀 핀이
    "17종이 맞는가"를 커밋마다 확인한다.
    """
    block = re.search(r"task 목록:(.+?)\n\n", prompt, re.DOTALL)
    if not block:  # 프롬프트 모양이 바뀌면 조용히 비는 것보다 터지는 편이 낫다
        raise RuntimeError("프롬프트에서 'task 목록:'을 찾지 못했습니다")
    return tuple(dict.fromkeys(re.findall(r"([a-z_]+)\s*\(", block.group(1))))


TASK_NAMES: tuple[str, ...] = _tasks_declared_in_prompt(_PROMPT)
_KNOWN_TASKS = set(TASK_NAMES)

# 스키마 강제 디코딩(2026-08-18 로드맵 1-1). task를 enum으로 선언하면 어휘 밖
# 액션 발명·형식 붕괴가 **토큰 수준에서** 불가능해진다 — 이 머신·ax4-light에서
# enum 강제를 실측 확인했다. 규칙(로드맵 §0): pattern·oneOf 같은 복잡 키워드 금지,
# 좌표를 만들 수 있는 필드 추가 금지(range는 "문장에 적힌 것 옮겨 적기"용 하나뿐).
# 스키마는 형식만 보장한다 — task의 의미는 프롬프트의 한국어 설명이 담당하므로
# 프롬프트를 줄이면 안 된다(실측: 불투명 enum만 주면 합계 요청에 정렬을 골랐다).
INTENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["task", "range", "column", "option"],
    "additionalProperties": False,
    "properties": {
        "task": {"type": "string", "enum": sorted(_KNOWN_TASKS)},
        "range": {"type": ["string", "null"]},
        "column": {"type": ["string", "null"]},
        # find_replace는 {find, replace} 객체를 낸다 — 문자열·숫자·객체 모두 허용.
        "option": {
            "type": ["string", "number", "object", "null"],
            "additionalProperties": {"type": ["string", "number", "null"]},
        },
    },
}

# 조건이 붙은 색칠·입력은 여기서 매핑하면 조건이 사라진다 — 플래너·규칙에 넘긴다.
# 실측(50커맨드): "A열에서 50 이상인 셀만 노란색"이 fill_color로 분류돼 조건 없이
# 전체를 칠할 뻔했다.
_CONDITIONAL_MENTION = re.compile(
    r"(이상|이하|초과|미만|보다\s*(크|큰|작|적)|넘는|못\s*미치|조건|"
    r"인\s*(셀|값|행|것)만|경우만|일\s*때만)"
)

_RANGE = re.compile(r"^[A-Za-z]{1,3}\d{1,7}(:[A-Za-z]{1,3}\d{1,7})?$")
_SINGLE_CELL = re.compile(r"^[A-Za-z]{1,3}\d{1,7}$")

# 색 사전은 `color_lexicon` 한 곳이다. 예전엔 여기 따로 적혀 있었고 값이 갈라져
# **`남색`이 초록(#1E6B4F)이었다**(2026-08-24 실측). 모르는 색은 여기서도
# 매핑 실패로 둔다 — 조용히 노란색을 칠하느니 되묻는 편이 낫다.
_COLORS = COLOR_HEX

_AGG_FUNCS = {"SUM", "AVERAGE", "MAX", "MIN", "COUNT", "COUNTA", "MEDIAN"}


async def normalize_intent(
    message: str, digest: dict[str, Any] | None, llm_service: Any
) -> dict[str, Any] | None:
    """문장을 상징 의도로 번역한다. 실패하면 None — 호출자는 플래너로 폴백한다."""
    text = str(message or "").strip()
    if not text:
        return None
    entry = sheet_entry(digest or {}, None)
    headers = [str(c.get("header") or "") for c in (entry.get("columns") or []) if c.get("header")]
    prompt = _PROMPT.format(headers=", ".join(headers) or "(없음)", message=text)
    raw = await llm_service.chat(
        [{"role": "user", "content": prompt}],
        model=None,  # 범용 모델(설정의 `model`, 에이닷) — 플래너 모델이 아니다
        temperature=0.0,
        json_only=True,
        json_schema=INTENT_JSON_SCHEMA,
        timeout=NORMALIZE_TIMEOUT_SECONDS,
    )
    intent = extract_json_object(raw, require_keys=("task",)) or {}
    task = str(intent.get("task") or "").strip().lower()
    if task not in _KNOWN_TASKS:
        return None
    intent["task"] = task
    return intent


#: 굵게. **기울임·밑줄은 일부러 없다** — 도구에 파라미터가 없어서, 매핑하면
#: 조용히 무시되고 "했다"고 보고된다(가짜 성공).
_BOLD_WORDS = re.compile(r"(굵게|굵은|굵직|진하게|진한|두껍게|볼드|bold)", re.IGNORECASE)
#: 굵게 **해제**. "굵게 풀어줘"를 굵게로 읽으면 정반대 편집이다.
_UNBOLD_WORDS = re.compile(r"(굵게\s*(?:해제|풀|빼|없애)|진하게\s*(?:해제|풀)|안\s*굵게|보통\s*굵기)")
#: 글자 크기. "14", "14pt", "14포인트", "크기 14" 모두 같은 뜻이다.
_SIZE_WORDS = re.compile(r"(?:크기\s*)?(\d{1,3})\s*(?:pt|포인트|포인|px)?\s*$", re.IGNORECASE)


def _font_params_from(option_text: str) -> dict[str, Any]:
    """글자 서식 옵션 한 덩어리를 `set_font` 파라미터로. 못 알아들으면 빈 dict.

    검증기(`excel_live_plan_validator.py:749`)와 실행기가 이미 색·굵게·크기·맞춤을
    모두 받는다. 여기 낱말 사전이 없어서 색 말고는 전부 플래너로 넘어가고 있었다.
    """
    text = str(option_text or "").strip()
    if not text:
        return {}
    color = _COLORS.get(text.lower())
    if color:
        return {"color": color}
    if _UNBOLD_WORDS.search(text):
        return {"bold": False}
    if _BOLD_WORDS.search(text):
        return {"bold": True}
    align = _ALIGN_WORDS.get(text.lower()) or _ALIGN_WORDS.get(
        re.sub(r"\s*(?:정렬|맞춤|으로|로)\s*$", "", text).strip().lower()
    )
    if align:
        return {"align": align}
    size = _SIZE_WORDS.fullmatch(text)
    if size:
        value = int(size.group(1))
        # 엑셀이 받는 범위 밖이면 매핑하지 않는다 — 1pt·500pt는 사람 뜻이 아니다.
        if 6 <= value <= 72:
            return {"size": float(value)}
    return {}


def _column_letter(entry: dict[str, Any], column: Any) -> str:
    """머리글 이름(또는 이미 열 문자)을 열 문자로. 못 찾으면 ""."""
    name = str(column or "").strip()
    if not name:
        return ""
    if re.fullmatch(r"[A-Za-z]{1,3}", name):
        return name.upper()
    lowered = name.lower()
    for col in entry.get("columns") or []:
        header = str(col.get("header") or "").strip()
        if header and (header.lower() == lowered or lowered in header.lower()):
            return str(col.get("letter") or "").upper()
    return ""


def _last_row(entry: dict[str, Any]) -> int:
    match = re.search(r"(\d+)\s*$", str(entry.get("used_range") or ""))
    try:
        return max(2, int(match.group(1))) if match else 2
    except ValueError:
        return 2


def _norm_range(value: Any) -> str:
    text = str(value or "").strip().upper().replace("$", "")
    return text if _RANGE.fullmatch(text) else ""


def _coerce_literal(text: str) -> Any:
    """숫자로 생긴 값은 숫자로 쓴다. "120"을 문자열로 쓰면 SUM이 무시한다."""
    raw = str(text).strip()
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return raw
    if re.fullmatch(r"-?\d+\.\d+", raw):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _format_code_from(option: str) -> str:
    """옵션 낱말 → 표시 형식 코드. 모르는 말은 빈 문자열 — 추측하지 않는다."""
    text = str(option or "").strip()
    if text and not re.search(r"[가-힣]", text) and re.search(r"[0#@%.,]", text):
        return text  # 이미 형식 코드다
    lowered = text.lower()
    # 사전이 먼저다 — 예전엔 여기 사다리가 따로 있어 `퍼센트`가 0%였고
    # 라우터는 0.0%, 검증기는 0.00%였다(2026-08-24 실측).
    exact = format_code_in_text(lowered)
    if exact:
        return exact
    # 사전에 없는 표현은 느슨하게 한 번 더 본다("자릿수", "%" 기호만 온 꼴).
    if re.search(r"자릿수", lowered):
        return format_code("천단위")
    if re.search(r"%", lowered):
        return format_code("퍼센트")
    if re.search(r"₩", lowered):
        return format_code("통화")
    return ""


def intent_to_plan(
    intent: dict[str, Any] | None,
    *,
    digest: dict[str, Any] | None,
    message: str = "",
) -> dict[str, Any] | None:
    """상징 의도 → 실행 계획. 확신이 없으면 None — 플래너 폴백이 낫다.

    여기서 만드는 계획의 파라미터는 상징(머리글 이름)이거나 문장에 있던 범위뿐이다.
    바인더가 좌표를 확정하고, 못 하면 되묻는다.
    """
    if not isinstance(intent, dict):
        return None
    entry = sheet_entry(digest or {}, None)
    task = str(intent.get("task") or "")
    rng = _norm_range(intent.get("range"))
    column = intent.get("column")
    # 스키마 강제 후 실측: 모델이 셀 주소(F2)를 column 필드에 넣는 편차가 있다.
    # 셀 모양이면 range로 옮긴다 — column은 머리글 이름 자리다.
    if not rng and _SINGLE_CELL.fullmatch(str(column or "").strip().upper()):
        rng = str(column).strip().upper()
        column = None
    option = intent.get("option")
    option_text = "" if option is None else str(option).strip()

    # 분류가 fill/font/write로 나왔어도 문장에 조건어가 있으면 조건이 매핑에서
    # 사라진다 — 그 문장은 플래너·규칙 몫이다.
    if task in {"fill_color", "font", "write_value"} and _CONDITIONAL_MENTION.search(
        str(message or "")
    ):
        return None

    steps: list[dict[str, Any]] | None = None

    if task == "fill_color":
        color = _COLORS.get(option_text.lower())
        if color:
            steps = [{
                "action": "excel_live.fill_range",
                "params": {"target_range": rng or "__ACTIVE_SELECTION__", "fill_color": color},
                "reason": "의도 정규화: 배경색",
            }]

    elif task == "font":
        font_params = _font_params_from(option_text)
        if font_params:
            steps = [{
                "action": "excel_live.set_font",
                "params": {"target_range": rng or "__ACTIVE_SELECTION__", **font_params},
                "reason": f"의도 정규화: 글자 서식({next(iter(font_params))})",
            }]

    elif task == "number_format":
        code = _format_code_from(option_text)
        letter = _column_letter(entry, column)
        target = rng or (f"{letter}2:{letter}{_last_row(entry)}" if letter else "")
        if code and target:
            steps = [{
                "action": "excel_live.set_number_format",
                "params": {"target_range": target, "format_code": code},
                "reason": "의도 정규화: 표시 형식",
            }]

    elif task == "formula":
        func = option_text.upper()
        letter = _column_letter(entry, column)
        if func in _AGG_FUNCS and letter and rng and _SINGLE_CELL.fullmatch(rng):
            formula = f"={func}({letter}2:{letter}{_last_row(entry)})"
            steps = [{
                "action": "excel_live.set_formula",
                "params": {"range_ref": rng, "formula_a1": formula},
                "reason": f"의도 정규화: {func} 수식",
            }]

    elif task == "sort":
        order = "desc" if "desc" in option_text.lower() else (
            "asc" if "asc" in option_text.lower() else ""
        )
        key = str(column or "").strip()
        if order and key:
            steps = [{
                "action": "excel_live.sort_range",
                "params": {"target_range": "__ACTIVE_SELECTION__", "key_column": key, "order": order},
                "reason": "의도 정규화: 정렬",
            }]

    elif task == "filter":
        key = str(column or "").strip()
        if key and option_text:
            steps = [{
                "action": "excel_live.filter_rows",
                "params": {
                    "target_range": "__ACTIVE_SELECTION__",
                    "column": key,
                    "operator": "==",
                    "value": option_text,
                    "mode": "keep",
                },
                "reason": "의도 정규화: 필터",
            }]

    elif task == "clear_values":
        if rng:
            steps = [{
                "action": "excel_live.clear_range",
                "params": {"target_range": rng},
                "reason": "의도 정규화: 값 비우기",
            }]
        elif column:
            # "비고 열 비워줘" — 범위 대신 열 이름을 부른 경우. **2행부터** 지운다:
            # 머리글까지 지우면 표가 뭉개진다(파괴 게이트 `clear_only_named`가 지키는 것).
            letter, last = _column_letter(entry, column), _last_row(entry)
            if letter and last > 2:
                # 데이터 끝을 모르면(폴백 2) 물러난다 — 한 칸만 지우고 "비웠다"고
                # 답하는 건 조용한 부분 실행이다(2026-08-23 쓰기 쪽에서 겪은 그것).
                steps = [{
                    "action": "excel_live.clear_range",
                    "params": {"target_range": f"{letter}2:{letter}{last}"},
                    "reason": "의도 정규화: 값 비우기(열 전체)",
                }]

    elif task == "reset_all":
        if rng:
            steps = [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": rng, "line_style": "none", "weight": "thin", "color": "#D9D9D9"},
                    "reason": "의도 정규화: 테두리 제거",
                },
                {
                    "action": "excel_live.fill_range",
                    "params": {"target_range": rng, "fill_color": "none"},
                    "reason": "의도 정규화: 배경 제거",
                },
                {
                    "action": "excel_live.clear_range",
                    "params": {"target_range": rng},
                    "reason": "의도 정규화: 내용 비우기",
                },
            ]

    elif task == "find_replace":
        find_text = replace_text = None
        if isinstance(option, dict):
            find_text = option.get("find") or option.get("find_text")
            replace_text = option.get("replace") or option.get("replace_text")
        if isinstance(find_text, str) and find_text.strip() and isinstance(replace_text, str):
            steps = [{
                "action": "excel_live.find_replace",
                "params": {
                    "target_range": "__USED_RANGE__",
                    "find_text": find_text.strip(),
                    "replace_text": replace_text,
                },
                "reason": "의도 정규화: 찾아 바꾸기",
            }]

    elif task == "write_value":
        # 집계 옵션이 붙은 write는 사실 수식 요청이다 — 실측에서 유일하게 빗나간
        # 분류("매출 총액이 얼마인지 F2에 넣어놔줘")가 이 형태로 회수된다.
        func = option_text.upper()
        letter = _column_letter(entry, column)
        if func in _AGG_FUNCS and letter and rng and _SINGLE_CELL.fullmatch(rng):
            steps = [{
                "action": "excel_live.set_formula",
                "params": {"range_ref": rng, "formula_a1": f"={func}({letter}2:{letter}{_last_row(entry)})"},
                "reason": f"의도 정규화: {func} 수식(값 입력으로 표현됨)",
            }]
        elif option_text.lower() in _KNOWN_TASKS:
            # 스키마 강제 실측 퇴행: 모델이 option에 태스크 이름을 되뇌는 축퇴가
            # 있다("write_value"가 셀에 쓰였다). 스키마 어휘는 값이 아니다.
            steps = None
        elif rng and option_text and len(option_text) <= 40:
            # 한 칸이든 범위든 **모양 맞추기는 바인더 것 하나만 쓴다.** 여기서 또
            # 짜면 "1,000"·지시문·상한 판정이 두 벌이 되어 반드시 갈라진다
            # (2026-08-23: 예전엔 여기가 `_SINGLE_CELL`만 받아 "A2:A9에 0 넣어줘"가
            # 통째로 플래너로 넘어갔다 — 실사용에서 가장 많이 걸린 지점이다).
            shape = _range_shape(rng)
            if shape is not None:
                top_left, row_count, col_count = shape
                # "120"은 숫자다 — 문자열로 쓰면 SUM이 무시한다(50커맨드 실측).
                values = _shape_write_values(option_text, row_count, col_count)
                if values:
                    steps = [{
                        "action": "excel_live.write_range",
                        "params": {"start_cell": top_left, "values_2d": values},
                        "reason": "의도 정규화: 값 입력",
                    }]
        elif column and option_text and len(option_text) <= 40:
            # "비고 열 전부 미정" — 범위 대신 열 이름을 부른 경우.
            letter, last = _column_letter(entry, column), _last_row(entry)
            if letter and last > 2:
                # `_last_row`는 사용 범위를 못 읽으면 2를 돌려준다. 그 상태로 채우면
                # "전부"가 한 칸이 되므로, 데이터 끝을 모르면 아예 물러난다.
                values = _shape_write_values(option_text, last - 1, 1)
                if values:
                    steps = [{
                        "action": "excel_live.write_range",
                        "params": {"start_cell": f"{letter}2", "values_2d": values},
                        "reason": "의도 정규화: 값 입력(열 전체)",
                    }]

    elif task == "create_sheet":
        # 시트 이름은 option 또는 column에 실려 온다. 이름이 없으면 물러난다 —
        # 이름을 지어내 만들면 사용자가 부른 적 없는 시트가 생긴다.
        name = str(option_text or column or "").strip().strip("'\"")
        # "시트"라는 낱말 자체가 이름으로 오는 축퇴는 거른다.
        if name and name.lower() not in {"시트", "sheet", "탭", "새 시트"} and len(name) <= 31:
            steps = [{
                "action": "excel_live.create_sheet",
                "params": {"sheet_name": name},
                "reason": "의도 정규화: 새 시트",
            }]

    elif task == "delete_charts":
        # 파라미터가 없다 — 시트는 디스패치가 활성 시트로 확정한다. 파괴 액션이지만
        # CONFIRM 승인 게이트를 그대로 지나고, 값 스냅샷 면제 사유는
        # `_ROLLBACK_EXEMPT_ACTIONS`에 있다(차트는 셀 값이 아니다).
        steps = [{
            "action": "excel_live.delete_charts",
            "params": {},
            "reason": "의도 정규화: 차트 삭제",
        }]

    elif task == "freeze":
        # "첫 줄 고정" = A2에서 고정. 숫자 N이 오면 N행**까지** 고정 = A{N+1}.
        row = None
        digits = re.search(r"(\d{1,3})", option_text)
        if digits:
            row = int(digits.group(1))
        freeze_at = f"A{row + 1}" if row and 1 <= row <= 100 else "A2"
        steps = [{
            "action": "excel_live.freeze_panes",
            "params": {"freeze_at": freeze_at},
            "reason": "의도 정규화: 틀 고정",
        }]

    elif task == "autofit":
        steps = [{
            "action": "excel_live.autofit_columns",
            "params": {"target_range": rng or "__USED_RANGE__"},
            "reason": "의도 정규화: 열 너비 자동",
        }]

    # dedupe·pivot·chart·create_table·read·other는 매핑하지 않는다 — 슬롯·플래너
    # 경로가 이미 소유하고 있고(배터리 24/24), 여기서 어설프게 겹치면 두 경로가
    # 서로를 되돌리는 부류(2026-08-17에 세 번 겪은)가 또 생긴다.

    if not steps:
        return None
    return {
        "action_plan": steps,
        "action": steps[0]["action"],
        "params": steps[0]["params"],
        "reason": "의도 정규화 기반 계획",
        "intent": "edit",
        "plan_source": "intent",
    }
