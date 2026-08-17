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

from .excel_param_binder import sheet_entry
from .llm_json import extract_json_object

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
find_replace(찾아 바꾸기), read(조회), other(그 외)

주의: "50 이상인 셀만 노란색"처럼 **조건이 붙은 색칠은 fill_color가 아니라
highlight**다.

규칙:
- 범위·좌표를 **만들어내지 마라.** 문장에 적힌 것만 옮겨 적는다.
- 열은 좌표가 아니라 머리글 이름으로 가리킨다.
- option: 색 이름, asc/desc, 필터 값, SUM/AVERAGE/MAX/MIN/COUNT, 새 값 등 하나.

예시:
문장: "B2:B9 파란색으로 칠해줘"
{{"task": "fill_color", "range": "B2:B9", "column": null, "option": "파란색"}}
문장: "매출 높은 순서로 보여줘"
{{"task": "sort", "range": null, "column": "금액", "option": "desc"}}
문장: "G1에 담당자별 평균 수식 넣어줘"
{{"task": "formula", "range": "G1", "column": "금액", "option": "AVERAGE"}}

문장: "{message}"
JSON:"""

_KNOWN_TASKS = {
    "fill_color", "font", "highlight", "number_format", "formula", "sort", "filter",
    "dedupe", "clear_values", "reset_all", "create_table", "pivot", "chart",
    "write_value", "find_replace", "read", "other",
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

# 모르는 색을 노란색으로 칠하면 안 된다 — 못 알아들은 색은 매핑 실패로 둔다.
_COLORS = {
    "노란색": "#FFFF00", "노랑": "#FFFF00", "노란": "#FFFF00", "yellow": "#FFFF00",
    "빨간색": "#FF4D4F", "빨강": "#FF4D4F", "빨간": "#FF4D4F", "red": "#FF4D4F",
    "파란색": "#4F8CFF", "파랑": "#4F8CFF", "blue": "#4F8CFF",
    "초록색": "#6AC36A", "초록": "#6AC36A", "green": "#6AC36A",
    "흰색": "#FFFFFF", "하얀색": "#FFFFFF", "하양": "#FFFFFF", "white": "#FFFFFF",
    "검은색": "#000000", "검정": "#000000", "black": "#000000",
    "회색": "#D9D9D9", "gray": "#D9D9D9", "grey": "#D9D9D9",
    "주황색": "#FFA500", "주황": "#FFA500", "orange": "#FFA500",
    "남색": "#1E6B4F", "navy": "#001F5B", "보라색": "#7B61FF", "보라": "#7B61FF",
}

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
        timeout=NORMALIZE_TIMEOUT_SECONDS,
    )
    intent = extract_json_object(raw, require_keys=("task",)) or {}
    task = str(intent.get("task") or "").strip().lower()
    if task not in _KNOWN_TASKS:
        return None
    intent["task"] = task
    return intent


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
    if re.search(r"콤마|쉼표|천\s*단위|자릿수|comma|thousand", lowered):
        return "#,##0"
    if re.search(r"소수점|decimal", lowered):
        return "0.00"
    if re.search(r"퍼센트|percent|%", lowered):
        return "0%"
    if re.search(r"통화|원화|₩|krw", lowered):
        return '"₩"#,##0'
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
        color = _COLORS.get(option_text.lower())
        if color:
            steps = [{
                "action": "excel_live.set_font",
                "params": {"target_range": rng or "__ACTIVE_SELECTION__", "color": color},
                "reason": "의도 정규화: 글자색",
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
                    "params": {"target_range": rng, "fill_color": "#FFFFFF"},
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
        elif rng and _SINGLE_CELL.fullmatch(rng) and option_text and len(option_text) <= 40:
            steps = [{
                "action": "excel_live.write_range",
                # "120"은 숫자다 — 문자열로 쓰면 SUM이 무시한다(50커맨드 실측).
                "params": {"start_cell": rng, "values_2d": [[_coerce_literal(option_text)]]},
                "reason": "의도 정규화: 값 입력",
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
