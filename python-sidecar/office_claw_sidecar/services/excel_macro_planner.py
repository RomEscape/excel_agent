"""매크로 계층 — 한 문장의 고수준 요청을 여러 하위 명령으로 펼친다.

플래너는 한 명령당 4단계가 상한이라("대시보드 만들어줘"는 실측에서 18개 명령이 필요했다)
고수준 요청을 담지 못한다. 이 모듈은 요청을 **하위 명령 문장 목록**으로 바꾸는 일만 한다.
각 문장이 실제로 어떤 액션이 되는지는 기존 /command 경로(규칙 파서 → 플래너 → 바인더 →
되묻기)가 그대로 맡는다. 여기서 액션을 직접 만들지 않는 이유가 그것이다 — 그 경로에
쌓인 보정 로직을 우회하면 품질이 도로 떨어진다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from office_claw_sidecar.services.excel_live_agent import parse_command_rule_based
from office_claw_sidecar.services.llm_json import extract_json_object

# 한 매크로가 낼 수 있는 하위 명령 수 상한. 넘으면 사용자가 검토할 수 없고,
# 잘못된 분해가 통합문서를 크게 헤집을 수 있다.
MAX_MACRO_STEPS = 30

MACRO_TEMPERATURE = 0.0

# 규모를 암시하는 동사. 이것만으로는 부족해서 산출물어와 함께 봐야 한다.
_SCALE_VERB = re.compile(
    r"(만들어|만들 ?줘|만들자|구축|작성해|작성 ?줘|정리해|정리 ?줘|꾸며|"
    r"구성해|세팅|셋업|build|create)",
    re.IGNORECASE,
)

# 여러 단계를 거쳐야 나오는 복합 산출물. 단일 액션으로 끝나는 "표"는 일부러 뺐다 —
# create_table 멀티턴 슬롯 경로가 이미 담당한다.
_COMPOSITE_ARTIFACT = re.compile(
    r"(대시보드|dashboard|보고서|리포트|report|집계표|요약표|현황판|"
    r"분석 ?자료|월간 ?보고|주간 ?보고|정산서)",
    re.IGNORECASE,
)

# "대시보드처럼 정리해줘"는 비유지 대시보드를 만들어 달라는 말이 아니다.
_COMPARISON_SUFFIX = re.compile(r"^(처럼|같이|같은|스럽|식으로|풍으로|느낌)")

# 셀/범위를 콕 집었으면 이미 구체적인 단일 명령이다.
# \b를 쓰면 "A1에"처럼 조사가 붙었을 때 한글이 단어 문자로 취급돼 경계가 생기지 않는다.
_CELL_REF = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\$?[0-9]{1,7}(?![0-9])")

# 규칙 파서가 못 잡는 파괴적 의도를 문장에서 직접 본다.
_DESTRUCTIVE_WORD = re.compile(
    r"(지워|지운|삭제|제거|비워|비우|clear|delete|remove|초기화|덮어 ?쓰)",
    re.IGNORECASE,
)

# 실행되면 데이터가 사라지는 액션. 승인 화면에서 따로 표시해 준다.
DESTRUCTIVE_ACTIONS = frozenset(
    {
        "excel_live.clear_range",
        "excel_live.drop_column",
        "excel_live.dedupe_rows",
        "excel_live.filter_rows",
        "excel_live.sort_range",
        "excel_live.sort_rows",
        "excel_live.protect_sheet",
    }
)


@dataclass
class MacroStepPlan:
    """사용자에게 보여주고 승인받을 하위 명령 한 줄."""

    index: int
    command: str
    destructive: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "command": self.command,
            "destructive": self.destructive,
            "warnings": list(self.warnings),
        }


def _requests_composite_artifact(text: str) -> bool:
    """복합 산출물을 '만들어 달라'고 한 것인지 본다 — 비유는 뺀다."""
    for match in _COMPOSITE_ARTIFACT.finditer(text):
        tail = text[match.end() :].lstrip()
        if _COMPARISON_SUFFIX.match(tail):
            continue
        return True
    return False


def looks_like_macro_request(message: str) -> bool:
    """
    한 번의 계획(4단계)으로는 못 담을 고수준 요청인지 판정한다.

    오탐이 가장 위험하다 — 단순 명령을 매크로로 오인하면 멀쩡하던 경로에 승인 화면이
    끼어든다. 그래서 규모 동사와 복합 산출물어가 **둘 다** 있고 셀 표기가 **없을 때만**
    참을 반환한다.
    """
    text = str(message or "").strip()
    if not text:
        return False
    if _CELL_REF.search(text):
        return False
    if not _requests_composite_artifact(text):
        return False
    return bool(_SCALE_VERB.search(text))


def _fewshot_block() -> str:
    """실측으로 검증된 분해 예시.

    scratch/measure_dashboard_build.py의 18개 명령을 그대로 옮겼다. 이 목록은
    명령 18/18, 산출물 검사 14/14로 통과한 적이 있어 "좋은 분해"의 기준이 된다.
    """
    example = [
        "Sales_Data 시트 J1에 매출 입력",
        "Sales_Data 시트 J2:J61에 수식 =E2*F2*(1-G2) 적용",
        "Sales_Data 시트 K1에 이익 입력",
        "Sales_Data 시트 K2:K61에 수식 =J2-(H2*F2) 적용",
        "Dashboard 시트 만들어줘",
        "Dashboard 시트 A1:A3에 총매출,총이익,평균주문금액 입력",
        "Dashboard 시트 B1에 수식 =SUM(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 B2에 수식 =SUM(Sales_Data!K2:K61) 적용",
        "Dashboard 시트 B3에 수식 =AVERAGE(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 A5:C5에 지역,매출,이익 입력",
        "Dashboard 시트 A6:A11에 서울,경기,충청,영남,호남,강원 입력",
        (
            "Dashboard 시트 B6:B11에 수식 "
            "=SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$J$2:$J$61) 적용"
        ),
        (
            "Dashboard 시트 C6:C11에 수식 "
            "=SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$K$2:$K$61) 적용"
        ),
        "Dashboard 시트 A5:C11로 막대 차트 만들어줘",
        "Sales_Data 시트 J2:J61에 데이터 막대 적용해줘",
        "Sales_Data 시트 틀 고정해줘",
        "Dashboard 시트 열 너비 자동 맞춤",
        "Sales_Data 시트 J2:K61에 숫자 형식 #,##0 적용",
    ]
    payload = json.dumps({"steps": example}, ensure_ascii=False)
    return (
        "예시 — 요청: \"Sales_Data로 매출 대시보드 만들어줘\"\n"
        "(Sales_Data 시트에 지역(C), 단가(E), 수량(F), 할인율(G), 원가(H) 열이 "
        "2~61행에 있는 상황)\n"
        f"{payload}"
    )


def build_macro_prompt(message: str, *, digest_text: str = "") -> str:
    """분해 프롬프트를 만든다."""
    digest_block = f"{digest_text}\n" if str(digest_text or "").strip() else ""
    return (
        "너는 Excel 작업 분해기다. 사용자의 큰 요청을 하나씩 실행 가능한 한국어 명령 "
        "문장으로 쪼개라.\n\n"
        f"{digest_block}"
        "규칙:\n"
        "1) JSON만 출력한다. 형식: {\"steps\": [\"명령1\", \"명령2\"]}\n"
        "2) 각 명령은 그 자체로 실행 가능해야 한다. 시트명과 셀/범위를 반드시 밝힌다.\n"
        "   나쁨: \"매출 계산해줘\"  좋음: \"Sales_Data 시트 J2:J61에 수식 =E2*F2 적용\"\n"
        "3) 한 명령이 너무 커지면 안 된다. 명령 하나는 4단계 이내로 끝나야 한다.\n"
        "4) 위 통합문서 상태에 실제로 있는 시트명과 열만 쓴다. 없는 열을 지어내지 않는다.\n"
        "5) 만들 수 없는 것은 계획에 넣지 않는다 — Excel 표(ListObject), 수식 기반 "
        "조건부 서식, 글꼴/굵게 같은 글자 스타일.\n"
        "6) 사용자가 지우라고 하지 않았으면 기존 데이터를 지우거나 덮어쓰지 않는다.\n"
        f"7) 명령은 최대 {MAX_MACRO_STEPS}개까지.\n"
        "8) 순서가 중요하다. 값을 먼저 넣고 그 값을 참조하는 수식·차트를 뒤에 둔다.\n\n"
        f"{_fewshot_block()}\n\n"
        f"요청: {message}"
    )


def _sheet_names(digest: dict[str, Any] | None) -> set[str]:
    sheets = (digest or {}).get("sheets") or []
    names = set()
    for sheet in sheets:
        name = str((sheet or {}).get("name") or "").strip()
        if name:
            names.add(name.lower())
    return names


def _erases_cells(parsed: dict[str, Any]) -> bool:
    """값을 전부 비우는 write_range인지 본다.

    규칙 파서는 "A1:B2 지워줘"를 None으로 채우는 write_range로 만든다. 액션 이름만
    보면 평범한 입력이지만 실제로는 지우는 동작이다.
    """
    if str(parsed.get("action") or "") != "excel_live.write_range":
        return False
    rows = (parsed.get("params") or {}).get("values_2d")
    if not isinstance(rows, list) or not rows:
        return False
    return all(
        cell is None or str(cell).strip() == ""
        for row in rows
        if isinstance(row, list)
        for cell in row
    )


def _predict_destructive(command: str) -> bool:
    """이 명령이 데이터를 지울 것 같은지 본다.

    규칙 파서는 정규식이라 비용이 없다. 파서가 액션을 못 뽑으면 문장의 파괴적 어휘로
    한 번 더 본다 — 승인 화면에서 놓치는 것보다 과하게 표시하는 편이 낫다.
    """
    text = str(command or "")
    try:
        parsed = parse_command_rule_based(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        if str(parsed.get("action") or "").strip() in DESTRUCTIVE_ACTIONS:
            return True
        if _erases_cells(parsed):
            return True
    return bool(_DESTRUCTIVE_WORD.search(text))


def validate_macro_steps(
    raw_steps: Any,
    *,
    digest: dict[str, Any] | None = None,
) -> list[MacroStepPlan]:
    """
    LLM이 낸 하위 명령 목록을 사람에게 보여줄 수 있는 형태로 정리한다.

    Raises
    ------
    TypeError
        분해 결과가 목록이 아닐 때.
    ValueError
        쓸 만한 명령이 하나도 없을 때.
    """
    if not isinstance(raw_steps, list):
        raise TypeError("분해 결과가 목록이 아닙니다.")

    known_sheets = _sheet_names(digest)
    steps: list[MacroStepPlan] = []
    seen: set[str] = set()

    for raw in raw_steps:
        if len(steps) >= MAX_MACRO_STEPS:
            break
        command = " ".join(str(raw or "").split()).strip()
        if not command or len(command) < 2:
            continue
        # 같은 명령을 두 번 실행하면 값이 덮이거나 시트가 중복 생성된다.
        fingerprint = command.lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        warnings: list[str] = []
        creates_sheet = bool(re.search(r"시트\s*(만들|생성|추가)", command))
        for name in re.findall(r"([^\s]+)\s*시트", command):
            cleaned = name.strip().strip("'\"")
            if not cleaned:
                continue
            if creates_sheet:
                # 계획 안에서 만드는 시트다. 뒤 단계가 이 시트를 쓰는 건 정상이므로
                # 여기서 등록해 두지 않으면 나머지 단계가 전부 경고로 뒤덮인다.
                known_sheets.add(cleaned.lower())
                continue
            if known_sheets and cleaned.lower() not in known_sheets:
                warnings.append(f"'{cleaned}' 시트는 지금 통합문서에 없습니다.")

        steps.append(
            MacroStepPlan(
                index=len(steps) + 1,
                command=command,
                destructive=_predict_destructive(command),
                warnings=warnings,
            )
        )

    if not steps:
        raise ValueError("실행할 수 있는 하위 명령을 만들지 못했습니다.")
    return steps


async def decompose_macro_request(
    message: str,
    llm_service,
    *,
    digest: dict[str, Any] | None = None,
    digest_text: str = "",
    model: str | None = None,
) -> list[MacroStepPlan]:
    """고수준 요청을 하위 명령 목록으로 펼친다."""
    prompt = build_macro_prompt(message, digest_text=digest_text)
    raw = await llm_service.chat(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=MACRO_TEMPERATURE,
        json_only=True,
    )
    # 분해는 플래너 모델이 아니라 일반 대화 모델이 맡는다(`get_macro_model_name`).
    # 앞뒤로 설명을 붙이거나 사고 과정을 남기는 모델일 가능성이 그만큼 크다.
    parsed = extract_json_object(str(raw or ""), require_keys=("steps",))
    if parsed is None:
        raise ValueError("분해 결과 JSON을 찾지 못했습니다.")
    return validate_macro_steps(parsed.get("steps"), digest=digest)
