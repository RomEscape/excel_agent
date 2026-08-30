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
from .aggregate_lexicon import AGG_WORD_PATTERN, aggregate_func
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
find_replace(찾아 바꾸기), read(조회·열 집계 물음), create_sheet(새 시트 만들기),
delete_charts(차트 삭제), freeze(행·열 틀 고정), autofit(열 너비 자동 맞춤),
merge(셀 병합), unmerge(병합 해제), data_bar(데이터 막대),
color_scale(값 크기 따라 색조), rename_sheet(기존 시트 이름 바꾸기),
delete_sheet(있는 시트 삭제), drop_column(열 삭제), add_column(새 열 추가),
rename_column(열 이름 바꾸기), group_by(~별 집계 결과 조회),
comment(셀 메모·코멘트), named_range(범위에 이름 정의),
other(그 외)

주의: 색칠에 조건(이상·이하·넘는·~인 셀만 같은 말)이 붙어 있으면 highlight,
조건이 없으면 **어떤 색이든 전부 fill_color**다.
시트를 **새로** 만들면 create_sheet, **있는** 시트의 이름을 바꾸면 rename_sheet다.
~별로 묶은 집계 **결과를 알려달라**는 요청이 group_by이고, 집계 **표를 만들어달라**는
요청은 pivot이다.
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
문장: "A1:F9에 매출표라는 이름 정의해줘"
{{"task": "named_range", "range": "A1:F9", "column": null, "option": "매출표"}}
문장: "수도권을 서울권으로 바꿔줘"
{{"task": "find_replace", "range": null, "column": null, "option": {{"find": "수도권", "replace": "서울권"}}}}

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

#: **병합 해제**를 뜻하는 말 — 병합 규칙의 부정 가드와 여기가 **같은 목록을 봐야 한다.**
#: 규칙마다 부정을 따로 적었더니 구멍이 났다: 퀵룰은 `해제|풀어|unmerge|취소`뿐이라
#: "합쳐진 칸 원래대로 **나눠줘**"가 정반대인 **병합**으로 실행됐다(2026-08-27 감사 실측).
#: 병합은 왼쪽 위 칸만 남기므로 값이 사라지는 파괴 액션이다.
UNMERGE_WORDS: tuple[str, ...] = (
    r"해제", r"풀어", r"풀고", r"풀기", r"풀어줘", r"취소", r"unmerge",
    r"나눠", r"나누", r"분리", r"해체", r"되돌", r"떼어", r"떼줘", r"원래대로",
)
#: 위 목록을 하나의 정규식으로 — 문장에 병합 해제 뜻이 있는가.
UNMERGE_PATTERN = re.compile("|".join(UNMERGE_WORDS), re.IGNORECASE)


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
    drop_log: list[str] | None = None,
) -> dict[str, Any] | None:
    """상징 의도 → 실행 계획. 확신이 없으면 None — 플래너 폴백이 낫다.

    여기서 만드는 계획의 파라미터는 상징(머리글 이름)이거나 문장에 있던 범위뿐이다.
    바인더가 좌표를 확정하고, 못 하면 되묻는다.
    """
    if not isinstance(intent, dict):
        if drop_log is not None:
            drop_log.append("not_dict")
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
    # 모델의 축퇴 두 가지를 **모든 분기 전에** 정리한다(2026-08-25 게이트 실측 7건):
    # ① JSON null 대신 문자열 "null"을 낸다 — 그대로 두면 'null'이라는 시트가 생겼다.
    # ② option에 태스크 어휘를 되뇐다("autofit"·"clear_values") — 값이 아니다.
    #    예전엔 write_value 분기에만 이 가드가 있어 다른 분기가 전부 뚫렸다.
    if option_text.lower() in {"null", "none"} or option_text.lower() in _KNOWN_TASKS:
        option_text = ""
    if str(column or "").strip().lower() in {"null", "none"}:
        column = None
    if rng.lower() in {"null", "none"}:
        rng = ""

    plain_message = str(message or "")

    def _worded(*patterns: str) -> bool:
        """모델이 고른 종류·파라미터를 **문장이 뒷받침하는가.**

        게이트 실측(2026-08-25): "H1부터 M1까지 한 칸으로 붙여줘"(병합)를 autofit으로,
        "표 내용 싹 지워"(전체)를 지역 열 하나 지우기로 실행했다 — 모델이 지어낸
        분류·파라미터를 그대로 믿은 탓이다. 근거 없는 계획은 실행하지 않고 플래너로
        넘긴다. 플래너는 이 문장들을 전부 옳게 처리해 왔다(08-23 게이트 601/624).
        """
        return any(__import__("re").search(pat, plain_message, __import__("re").IGNORECASE) for pat in patterns)

    # 분류가 fill/font/write/formula로 나왔어도 문장에 조건어가 있으면 조건이 매핑에서
    # 사라진다 — 그 문장은 플래너·규칙 몫이다.
    # formula가 빠져 있던 것은 **잠복 결함**이었다(2026-08-26 감사): 지금은 모델이
    # 한국어 option('합계')을 내 아래 영어 전용 검사에서 죽지만, 그 검사에 한국어
    # 집계어를 받아들이는 순간 "금액이 100 넘는 것만 합해서 G1에"가 조건이 사라진
    # =SUM(전체)로 매핑된다. 개선이 사고가 되지 않게 가드를 **먼저** 넓힌다.
    if task in {"fill_color", "font", "write_value", "formula"} and _CONDITIONAL_MENTION.search(
        str(message or "")
    ):
        # 이 물러남만 관측에서 빠져 있었다 — 63/58/37 자체가 과소 보고였다.
        if drop_log is not None:
            drop_log.append(f"conditional:{task}")
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
            # 모델이 배경색만 뽑고 글씨색·굵게를 버리는 일이 잦다(2026-08-30 말투
            # 게이트 B팔 13건: "남색 배경 흰 글씨 굵게"가 fill만 실행 — bold=False).
            # 원문에 어휘가 있으면 set_font를 덧붙인다 — 규칙 경로와 같은 2단 형태.
            font_recovered: dict[str, Any] = {}
            if re.search(r"(흰|하얀|하양|백색|white)", plain_message, re.IGNORECASE):
                font_recovered["font_color"] = "#FFFFFF"
            if re.search(r"(굵게|굵은|볼드|bold|두껍게|두꺼운)", plain_message, re.IGNORECASE):
                font_recovered["bold"] = True
            if font_recovered:
                font_recovered["target_range"] = steps[0]["params"]["target_range"]
                steps.append({
                    "action": "excel_live.set_font",
                    "params": font_recovered,
                    "reason": "의도 정규화: 글자 서식(원문 회수)",
                })

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

    elif task == "filter" and _worded(
        # 문장 근거 — "E열은 완료/대기/취소 목록에서 **선택되도록 제한해줘**"(유효성 검사)가
        # 필터로 분류돼 filter_rows가 실행됐다(2026-08-25 커버리지 v2). 필터를 말한 문장에만.
        r"필터", r"filter", r"만\s*남", r"만\s*보", r"추려", r"골라", r"걸러", r"만\s*표시", r"만\s*뽑", r"치워", r"아닌\s*(?:데|건|것|거)"
    ):
        key = str(column or "").strip()
        if key and option_text and option_text in plain_message:
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
            # "비고 열 비워줘" — 범위 대신 열 이름을 부른 경우. **2행부터** 지운다.
            # 축자 검사(column in plain_message)는 프롬프트의 동의어 매핑 지시(매출↔금액)와
            # 정면 모순이라 동의어 문장이 전량 버려졌다(2026-08-26 감사 A2). 머리글 실재
            # 확인으로 바꾼다 — 의도 계획(plan_source=intent)은 확인 카드를 거치므로
            # 동의어 매핑은 실행 전에 사용자가 본다.
            # 단 "표 내용 싹 지워"(전체)에 모델이 column=지역을 지어낸 사고(2026-08-25
            # 게이트)는 계속 막는다: 전체를 뜻하는 문장에서 원문에 없는 열을 믿지 않는다.
            # 머리글까지 지우면 표가 뭉개진다(파괴 게이트 `clear_only_named`가 지키는 것).
            # "다 지어줘/다 비워"도 전체를 뜻한다 — 이 낱말이 빠져 "이 표 내용 다
            # 지어줘 빈칸으루"에서 지어낸 column=지역이 통과해 A열만 비웠다
            # (2026-08-26 게이트 0535 실측, 601→600 회귀 1건의 전부).
            whole_table_wording = bool(
                re.search(r"(전체|전부|싹|모두|몽땅|내용\s*다|다\s*지[워어]|다\s*비워|다\s*삭제)", plain_message)
            ) and str(column).strip() not in plain_message
            letter, last = _column_letter(entry, column), _last_row(entry)
            if letter and last > 2 and not whole_table_wording:
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

    elif task == "write_value" and not _worded(r"메모", r"주석", r"코멘트", r"comment"):
        # 메모·주석은 셀 값이 아니다 — "D2에 확인 필요 라고 메모 달아줘"가 값 쓰기로 실행돼
        # 셀 내용이 바뀌었다(2026-08-25 커버리지 v2). 메모 종류는 어휘에 없으니 물러난다.
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
        # 시트 이름은 option 또는 column에 실려 온다. 이름을 지어내지 않는다 —
        # 모델이 "null"을 이름으로 내 'null' 시트가 생겼다(2026-08-25).
        name = str(option_text or column or "").strip().strip("'\"")
        # '요약 시트'처럼 시트 낱말이 붙어 오면 꼬리를 뗀다(2026-08-30 말투 B:
        # '요약 시트'라는 이름의 시트가 생겼다).
        name = re.sub(r"\s*(?:워크시트|시트|sheet|탭|tab)$", "", name, flags=re.IGNORECASE).strip()
        # "확인자 열 새로 만들어줘"는 열 추가다 — 모델이 create_sheet로 오분류하면
        # 이름('확인자')이 원문에 있어도 시트를 만들면 안 된다(2026-08-26 커버리지 0906).
        column_wording = bool(
            re.search(r"(?:열|컬럼|column)\s*\S{0,6}\s*(?:만들|추가|새로|넣)", plain_message)
        )
        degenerate = {
            "시트", "sheet", "탭", "새 시트", "새", "새로운", "새로", "새루", "새로이",
            "하나", "빈", "임시", "null", "none", "undefined",
        }
        usable = (
            bool(name)
            and name.lower() not in degenerate
            and len(name) <= 31
            and not column_wording
        )
        if usable and name not in plain_message:
            # 모델이 "sheet 2"를 "시트 2"로 의역하는 부류 — 시트 낱말을 뗀 나머지가
            # 원문에 있으면 같은 이름으로 본다("2"가 "sheet 2"에 있다).
            stripped = re.sub(r"(워크시트|시트|sheet|탭|tab)", "", name, flags=re.IGNORECASE).strip()
            usable = bool(stripped) and stripped.casefold() in plain_message.casefold()
        if usable:
            steps = [{
                "action": "excel_live.create_sheet",
                "params": {"sheet_name": name},
                "reason": "의도 정규화: 새 시트",
            }]
        elif (
            (not name or name.lower() in degenerate)
            and not re.search(r"(?:이름|명)\s*(?:은|을|이|으로|로|의)", plain_message)
            and not column_wording
            and _worded(r"만들", r"맏드", r"만드", r"생성", r"추가", r"create", r"add")
        ):
            # 기본 이름으로 가기 전에 원문에서 이름을 회수한다 — "대시보드 시트 하나
            # 만들어줄래?"에서 모델이 이름을 못 뽑아 Sheet2가 생겼고, 이후 '대시보드'
            # 참조 ~20턴이 무너졌다(2026-08-30 두 팔 실측 0830-0734, 팔B).
            # ok=True인데 결과가 다른 미검출 오실행 부류라 원문이 최후의 근거다.
            recovered = ""
            m = re.search(
                r"([가-힣A-Za-z0-9_·&/-]{2,20}?)\s*(?:이라는|이란|라는)?\s*(?:워크시트|시트|sheet|탭)",
                plain_message,
                re.IGNORECASE,
            )
            if m:
                cand = m.group(1).strip()
                if cand and cand.lower() not in degenerate and cand not in {"새", "새로운", "다른"}:
                    recovered = cand
            if recovered:
                steps = [{
                    "action": "excel_live.create_sheet",
                    "params": {"sheet_name": recovered},
                    "reason": "의도 정규화: 새 시트(원문 이름 회수)",
                }]
        if not steps and (
            (not name or name.lower() in degenerate)
            and not re.search(r"(?:이름|명)\s*(?:은|을|이|으로|로|의)", plain_message)
            and not column_wording
            and _worded(r"만들", r"맏드", r"만드", r"생성", r"추가", r"create", r"add")
        ):
            # 이름 없는 생성("새로운 시트 만들어줘") — 조용히 버리면 플래너가 미라벨
            # 계획을 내 "'새로운' 시트를 찾을 수 없습니다"가 됐다(2026-08-25 GUI 실측).
            # 기본 이름은 디스패처가 채운다(sheet_name="" 계약). 단 두 경우는 물러난다:
            # ①이름이 주어졌는데 31자 초과·미근거로 못 쓰는 경우 — 기본 이름으로 만들면
            # 부른 이름과 조용히 달라진다. ②원문에 "이름은/이름의" 지정 구가 있는 경우 —
            # 이름 추출을 못 한 것이지 이름이 없는 게 아니다(퀵룰·플래너가 잡는다).
            steps = [{
                "action": "excel_live.create_sheet",
                "params": {"sheet_name": ""},
                "reason": "의도 정규화: 새 시트(기본 이름)",
            }]

    elif task == "delete_charts" and _worded(r"차트", r"그래프", r"chart"):
        # 파라미터가 없다 — 시트는 디스패치가 활성 시트로 확정한다. 파괴 액션이지만
        # CONFIRM 승인 게이트를 그대로 지나고, 값 스냅샷 면제 사유는
        # `_ROLLBACK_EXEMPT_ACTIONS`에 있다(차트는 셀 값이 아니다).
        steps = [{
            "action": "excel_live.delete_charts",
            "params": {},
            "reason": "의도 정규화: 차트 삭제",
        }]

    elif task == "freeze" and _worded(r"고정", r"틀", r"freeze"):
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

    elif task == "autofit" and _worded(r"너비", r"폭", r"맞춰", r"맞춤", r"autofit"):
        steps = [{
            "action": "excel_live.autofit_columns",
            "params": {"target_range": rng or "__USED_RANGE__"},
            "reason": "의도 정규화: 열 너비 자동",
        }]

    elif (
        task == "merge"
        and _worded(r"병합", r"merge", r"하나로\s*합", r"셀\s*합")
        and not _worded(r"해제", r"풀", r"취소")
    ):
        # 병합은 왼쪽 위 칸만 남긴다 — 범위를 지어내면 값이 사라진다. 명시 범위가
        # 있을 때만 맵핑하고 없으면 물러난다(실행기 가드가 값 손실 병합을 한 번 더 막는다).
        if rng:
            steps = [{
                "action": "excel_live.merge_cells",
                "params": {"target_range": rng},
                "reason": "의도 정규화: 셀 병합",
            }]

    elif task == "unmerge" and _worded(*UNMERGE_WORDS):
        # 해제는 값을 만들지도 지우지도 않는다 — 범위가 없으면 사용 범위 전체.
        steps = [{
            "action": "excel_live.unmerge_cells",
            "params": {"target_range": rng or "__USED_RANGE__"},
            "reason": "의도 정규화: 병합 해제",
        }]

    elif task == "data_bar" and _worded(r"데이터\s*막대", r"data\s*bar"):
        # 낱말 '막대'만으로는 안 된다 — '막대 그래프'(차트)를 빨아들인다.
        letter = _column_letter(entry, column)
        target = rng or (f"{letter}2:{letter}{_last_row(entry)}" if letter else "") or "__ACTIVE_SELECTION__"
        steps = [{
            "action": "excel_live.apply_data_bar",
            "params": {"target_range": target},
            "reason": "의도 정규화: 데이터 막대",
        }]

    elif task == "color_scale" and _worded(
        # "색깔 단계로"·"진하게…연하게"·"그라데이션"도 색조다(2026-08-25 커버리지 v2:
        # 단색 채우기·글꼴 규칙에 잡혀 오실행 2/4 — 규칙 쪽은 배제했고 여기서 받는다).
        r"색조", r"컬러\s*스케일", r"color\s*scale", r"단계", r"그라데이션", r"진하게.{0,14}연하게", r"연하게.{0,14}진하게"
    ):
        letter = _column_letter(entry, column)
        target = rng or (f"{letter}2:{letter}{_last_row(entry)}" if letter else "") or "__ACTIVE_SELECTION__"
        steps = [{
            "action": "excel_live.apply_color_scale",
            "params": {"target_range": target},
            "reason": "의도 정규화: 색조",
        }]

    elif task == "rename_sheet" and _worded(r"이름", r"rename", r"바꿔", r"변경", r"시트\s*명", r"탭\s*명"):
        # 새 이름은 option(또는 column)에 실려 온다 — create_sheet와 같은 규약.
        # "지역별실적으로 바꿔줘"처럼 조사가 붙어 오면 어간이 문장에 있는지로 벗긴다.
        raw_name = str(option_text or column or "").strip().strip("'\"")
        candidates = [raw_name]
        for particle in ("으로", "로"):
            if raw_name.endswith(particle) and len(raw_name) > len(particle):
                candidates.append(raw_name[: -len(particle)])
        new_name = next(
            (
                c
                for c in sorted(candidates, key=len)  # 조사 벗긴 쪽(짧은 쪽)을 먼저 본다
                if c and c.lower() not in {"시트", "sheet", "탭"} and len(c) <= 31 and c in plain_message
            ),
            "",
        )
        if new_name:
            params: dict[str, Any] = {"new_name": new_name}
            # 바꿀 대상 시트가 문장·슬롯에 지목됐고 실제로 있으면 함께 확정한다.
            sheet_names = {str(s.get("name") or "") for s in ((digest or {}).get("sheets") or [])}
            old = str(column or "").strip()
            if old and old != new_name and old in sheet_names:
                params["sheet_name"] = old
            steps = [{
                "action": "excel_live.rename_sheet",
                "params": params,
                "reason": "의도 정규화: 시트 이름 변경",
            }]
        if not steps:
            # 모델이 새 이름을 못 뽑으면 rename 근거가 있어도 통째로 버려져 '지역별'
            # 같은 낱말이 집계 훅에 강탈됐다(2026-08-30 말투 B 8건: 지역성과_집계
            # 시트가 생김). "…(으)로 바꿔/변경"의 목적어를 원문에서 회수한다.
            m = re.search(
                r"([가-힣A-Za-z0-9_]{2,31}?)\s*(?:으로|로)\s*(?:바꿔|바꾸|변경|변겅|고쳐|지어|정해)",
                plain_message,
            )
            if not m:
                # "시트 이름 지역별실적!"처럼 동사 생략 — 이름/명 뒤 명사를 회수.
                m = re.search(
                    r"(?:시트|탭|sheet)\s*(?:이름|명)\s*(?:은|을|를|:)?\s*([가-힣A-Za-z0-9_]{2,31})\s*[!.~\s]*$",
                    plain_message,
                )
            if m:
                recovered_name = m.group(1).strip()
                for particle in ("으로", "로"):
                    if recovered_name.endswith(particle) and len(recovered_name) > len(particle) + 1:
                        recovered_name = recovered_name[: -len(particle)]
                if recovered_name and recovered_name.lower() not in {"시트", "sheet", "탭", "이름"}:
                    steps = [{
                        "action": "excel_live.rename_sheet",
                        "params": {"new_name": recovered_name},
                        "reason": "의도 정규화: 시트 이름 변경(원문 회수)",
                    }]

    elif (
        task == "delete_sheet"
        and _worded(r"시트", r"탭", r"sheet")
        and _worded(r"삭제", r"지워", r"없애", r"제거")
    ):
        # 파괴 액션 — 이름이 문장에 있고 **실제로 존재하는 시트**일 때만. CONFIRM 카드와
        # 롤백 면제 사유(_ROLLBACK_EXEMPT_ACTIONS)는 기존 경로가 그대로 적용된다.
        name = str(option_text or column or "").strip().strip("'\"")
        sheet_names = {str(x.get("name") or "") for x in ((digest or {}).get("sheets") or [])}
        if name and name in plain_message and name in sheet_names:
            steps = [{
                "action": "excel_live.delete_sheet",
                "params": {"sheet_name": name},
                "reason": "의도 정규화: 시트 삭제",
            }]

    elif (
        task == "drop_column"
        and _worded(r"열", r"컬럼", r"column")
        and _worded(r"삭제", r"지워", r"없애", r"제거", r"빼")
    ):
        # 열 이름이 문장에 있고 머리글로 확정될 때만 — 짐작한 열을 지우면 그게 파괴다.
        header_name = str(column or "").strip()
        if header_name and header_name in plain_message and _column_letter(entry, header_name):
            steps = [{
                "action": "excel_live.drop_column",
                "params": {"column": header_name},
                "reason": "의도 정규화: 열 삭제",
            }]

    elif (
        task == "add_column"
        and _worded(r"열", r"컬럼", r"column")
        and _worded(r"추가", r"새", r"만들", r"넣")
    ):
        # 이름은 option 또는 column에 실려 온다 — 모델은 "확인자 열 추가해줘"의 '확인자'를
        # column 슬롯에 싣는다(2026-08-25 커버리지 v2 outcome 로그: add_column 분류 3/4가
        # 이름 없음으로 unmapped → 플래너가 조회로 오실행).
        name = str(option_text or column or "").strip().strip("'\"")
        if (
            name
            and name.lower() not in {"열", "컬럼", "column", "새 열"}
            and len(name) <= 40
            and name in plain_message
        ):
            steps = [{
                "action": "excel_live.add_column",
                "params": {"name": name},
                "reason": "의도 정규화: 열 추가",
            }]

    elif (
        task == "rename_column"
        # '헤더·머리글·제목·필드'도 열 이름을 가리키는 말이다 — "헤더 금액을 매출액으로"가
        # 치환 규칙에서 물러난 뒤 여기서 받아야 한다(2026-08-25 커버리지 v2).
        and _worded(r"열", r"컬럼", r"column", r"헤더", r"머리글", r"제목", r"필드")
        and _worded(r"이름", r"바꿔", r"변경", r"rename", r"고쳐")
    ):
        old = new = ""
        if isinstance(option, dict):
            old = str(option.get("from") or option.get("old") or option.get("find") or "").strip()
            new = str(option.get("to") or option.get("new") or option.get("replace") or "").strip()
        if not new:
            old = old or str(column or "").strip()
            raw_new = option_text.strip().strip("'\"")
            candidates = [raw_new]
            for particle in ("으로", "로"):
                if raw_new.endswith(particle) and len(raw_new) > len(particle):
                    candidates.append(raw_new[: -len(particle)])
            new = next(
                (c for c in sorted(candidates, key=len) if c and c in plain_message), ""
            )
        if new and old and old != new and _column_letter(entry, old):
            steps = [{
                "action": "excel_live.rename_column",
                "params": {"column": old, "new_name": new},
                "reason": "의도 정규화: 열 이름 변경",
            }]

    elif task == "group_by":
        # 읽기 전용 조회(group_by_aggregate는 시트를 바꾸지 않는다). 쓰기 낱말이
        # 보이면 물러난다 — "지역별 합계를 G열에 넣어줘"는 이 매핑의 일이 아니다.
        if not _worded(r"넣어", r"써\s*줘", r"입력", r"기록", r"채워", r"표로", r"시트에"):
            headers = [str(c.get("header") or "") for c in (entry.get("columns") or [])]
            group_header = next(
                (h for h in headers if h and f"{h}별" in plain_message), ""
            )
            found_agg = AGG_WORD_PATTERN.search(plain_message)
            agg_word = option_text or (found_agg.group(0) if found_agg else "")
            func = aggregate_func(agg_word)
            agg_param = {"SUM": "sum", "AVERAGE": "average", "COUNTA": "count",
                         "MAX": "max", "MIN": "min"}.get(func, "")
            # 값 열: 모델은 column 슬롯에 **묶음 기준**("지역")을 싣는 일이 잦다
            # (0825 실측: "지역별 금액 합계" → column=지역). 슬롯을 믿지 말고
            # 문장에 실제로 등장하는 다른 머리글에서 찾되, 후보가 둘 이상이면
            # 지어내지 않고 물러난다.
            value_header = str(column or "").strip()
            if not value_header or value_header == group_header or not _column_letter(entry, value_header):
                in_message = [
                    h for h in headers
                    if h and h != group_header and h in plain_message
                ]
                value_header = in_message[0] if len(in_message) == 1 else ""
            value_ok = bool(value_header) and value_header != group_header and bool(
                _column_letter(entry, value_header)
            )
            if group_header and agg_param and (value_ok or agg_param == "count"):
                params: dict[str, Any] = {"group_column": group_header, "agg": agg_param}
                if value_ok:
                    params["value_column"] = value_header
                steps = [{
                    "action": "excel_live.group_by_aggregate",
                    "params": params,
                    "reason": "의도 정규화: ~별 집계 조회",
                }]

    elif task == "read":
        # 조회는 한 분기에서 순서대로 본다 — 두 elif로 가르면 모델이 option/column을
        # 지어냈을 때 첫 분기가 체인을 소비해 행-수 검사에 못 떨어진다(2026-08-26
        # 커버리지 0938 실측: '몇 줄이야'가 option 채움 탓에 unmapped→플래너 오판).
        if re.search(r"몇\s*(?:줄|행)|(?:행|줄)\s*(?:수|개수)", plain_message):
            # "데이터 몇 줄이야?" — 행 수 조회. count 통계는 숫자만 세서 글자 열에서
            # 0이 나온다(서비스 실측) — 표를 그대로 읽어 행 수가 보이게 한다(읽기 전용).
            steps = [{
                "action": "excel_live.read_range",
                "params": {"range_ref": "__USED_RANGE__"},
                "reason": "의도 정규화: 행 수 조회",
            }]
        elif option_text and column:
            # "열 집계 물음" — "제일 큰 금액이 뭐야"가 정렬로 오실행됐다(2026-08-25).
            # 넓은 read는 여전히 슬롯·플래너 몫이다(아래 주석). 읽기 전용(SAFE).
            up = str(option_text).strip().upper()
            func = up if up in _AGG_FUNCS else str(aggregate_func(option_text) or "")
            stat = {
                "SUM": "sum",
                "AVERAGE": "average",
                "MAX": "max",
                "MIN": "min",
                "COUNT": "count",
                "COUNTA": "count",
            }.get(func.upper(), "")
            if stat and _column_letter(entry, column) and not _worded(
                r"넣어", r"써\s*줘", r"적어", r"입력", r"기록", r"채워"
            ):
                steps = [{
                    "action": "excel_live.calculate_column_stat",
                    "params": {"column": str(column).strip(), "stat": stat},
                    "reason": "의도 정규화: 열 집계 조회(읽기 전용)",
                }]

    elif task == "comment" and rng and _SINGLE_CELL.fullmatch(rng):
        # 메모는 단일 셀 + 내용이 문장에 실재할 때만 — 내용을 지어내 붙이면 없는 말이
        # 워크북에 남는다. 범위가 없으면 물러난다(활성 셀 추측 금지).
        text = str(option_text or "").strip()
        if text and text in plain_message:
            steps = [{
                "action": "excel_live.add_cell_comment",
                "params": {"target_range": rng, "text": text},
                "reason": "의도 정규화: 셀 메모",
            }]

    elif task == "named_range":
        # 이름 정의는 이름이 문장에 실재할 때만. rename_sheet처럼 조사를 벗긴다
        # ("매출표라는" → 매출표). 범위 리터럴이 없어도 "이 범위/선택 영역"이면 활성
        # 선택으로 푼다 — 검증기·디스패처가 __ACTIVE_SELECTION__을 이미 해석한다.
        # 물러나면 플래너가 convert_to_excel_table로 오해석했다(2026-08-26 커버리지 0906).
        nr_target = rng or (
            "__ACTIVE_SELECTION__"
            if re.search(r"(?:이|선택한?|현재|여기)\s*(?:범위|영역)", plain_message)
            else ""
        )
        # 이름은 option 또는 column에 실려 온다(create_sheet와 같은 편차 — 모델이
        # column 슬롯에 이름을 실으면 option만 봐선 놓친다. 2026-08-26 커버리지 0938).
        nr_name = re.sub(
            r"(?:이?라는|이?라고|이?란|으로|로)$",
            "",
            str(option_text or column or "").strip().strip("'\""),
        ).strip()
        if nr_target and nr_name and nr_name in plain_message:
            steps = [{
                "action": "excel_live.define_named_range",
                "params": {"target_range": nr_target, "name": nr_name},
                "reason": "의도 정규화: 범위 이름 정의",
            }]

    # dedupe·pivot·chart·create_table·넓은 read·other는 매핑하지 않는다 — 슬롯·플래너
    # 경로가 이미 소유하고 있고(배터리 24/24), 여기서 어설프게 겹치면 두 경로가
    # 서로를 되돌리는 부류(2026-08-17에 세 번 겪은)가 또 생긴다.

    if not steps:
        # 어떤 종류가 왜 버려졌는지 세야 가드 수정의 전/후를 잴 수 있다(2026-08-26 감사 A27).
        if drop_log is not None:
            drop_log.append(f"unmapped:{intent.get('task') or ''!s}")
        return None
    return {
        "action_plan": steps,
        "action": steps[0]["action"],
        "params": steps[0]["params"],
        "reason": "의도 정규화 기반 계획",
        "intent": "edit",
        "plan_source": "intent",
    }
