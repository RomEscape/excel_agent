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
from office_claw_sidecar.services.excel_macro_coverage import CoverageTracker
from office_claw_sidecar.services.llm_json import extract_json_object

# 한 매크로가 낼 수 있는 하위 명령 수 상한. 넘으면 사용자가 검토할 수 없고,
# 잘못된 분해가 통합문서를 크게 헤집을 수 있다.
#
# 2026-08-16: 30 -> 45 -> 36. 서식까지 계획에 넣게 된 뒤 단일 시트 대시보드가 이미
# 25단계를 쓰고, 상세 시트 한 장에 8~10단계가 더 든다. 그래서 30으로는 상세 시트가 잘렸다.
#
# 45로 올렸더니 이번엔 **모델이 못 버텼다** — 열이 13개인 통합문서로 재 보니 4회 중 1회만
# 성공했다(90초 타임아웃 1회, 출력이 잘려 JSON 파싱 실패 1회, ReadTimeout 1회).
# 45단계짜리 한국어 문장 목록은 4.4GB 로컬 모델이 제한 시간 안에 온전히 뱉기 어렵다.
# 36은 실측에서 안정적으로 나온 크기다(매출 대시보드 36단계 36/36 완주).
MAX_MACRO_STEPS = 36

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

# "Dashboard 시트 만들어줘"는 대시보드를 지어 달라는 말이 아니라 **그 이름의 시트를
# 하나** 만들어 달라는 말이다. 산출물어 바로 뒤에 '시트/탭'이 붙으면 이름으로 읽는다.
# 이걸 매크로로 넘기면 create_sheet 한 번이면 될 일이 17단계 승인 화면이 되고,
# 사용자가 그 화면을 지나치면 시트가 아예 안 생긴다(2026-08-16 실측).
_SHEET_NAMING_SUFFIX = re.compile(r"^(시트|탭|sheet|tab)", re.IGNORECASE)

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
    """복합 산출물을 '만들어 달라'고 한 것인지 본다 — 비유와 시트 이름은 뺀다."""
    for match in _COMPOSITE_ARTIFACT.finditer(text):
        tail = text[match.end() :].lstrip()
        if _COMPARISON_SUFFIX.match(tail):
            continue
        if _SHEET_NAMING_SUFFIX.match(tail):
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
        # 제목은 1행 단독. KPI는 3행부터 — 값이 든 칸 위에서 병합하면 그 값이 사라진다.
        "Dashboard 시트 A1에 2026 매출 대시보드 입력",
        "Dashboard 시트 A3:A5에 총매출,총이익,평균주문금액 입력",
        "Dashboard 시트 B3에 수식 =SUM(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 B4에 수식 =SUM(Sales_Data!K2:K61) 적용",
        "Dashboard 시트 B5에 수식 =AVERAGE(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 A7:C7에 지역,매출,이익 입력",
        "Dashboard 시트 A8:A13에 서울,경기,충청,영남,호남,강원 입력",
        (
            "Dashboard 시트 B8:B13에 수식 "
            "=SUMIF(Sales_Data!$C$2:$C$61,A8,Sales_Data!$J$2:$J$61) 적용"
        ),
        (
            "Dashboard 시트 C8:C13에 수식 "
            "=SUMIF(Sales_Data!$C$2:$C$61,A8,Sales_Data!$K$2:$K$61) 적용"
        ),
        "Dashboard 시트 A7:C13로 막대 차트 만들어줘",
        "Sales_Data 시트 J2:J61에 데이터 막대 적용해줘",
        "Sales_Data 시트 틀 고정해줘",
        # 마무리 서식 — 데이터·수식·차트가 다 들어간 뒤에 모아서 한다.
        "Dashboard 시트 A1:C1 병합해줘",
        "Dashboard 시트 A1:C1 배경색 #1F4E79로 칠해줘",
        "Dashboard 시트 A1 글씨 흰색 크기 16 굵게",
        "Dashboard 시트 A7:C7 배경색 #DDEBF7로 칠해줘",
        "Dashboard 시트 A7:C7 글자 굵게 해줘",
        "Dashboard 시트 A7:C13 범위에 경계선 적용해줘",
        "Dashboard 시트 B3:B5에 숫자 형식 #,##0 적용",
        "Dashboard 시트 B8:C13에 숫자 형식 #,##0 적용",
        "Dashboard 시트 열 너비 자동 맞춤",
        "Sales_Data 시트 J2:K61에 숫자 형식 #,##0 적용",
        # 상세 시트 한 장 — 요약과 다른 축(분류)으로. 머리글·수식·서식까지 갖춘다.
        "분류별_상세 시트 만들어줘",
        "분류별_상세 시트 A1에 분류별 매출 상세 입력",
        "분류별_상세 시트 A3:C3에 분류, 매출, 이익 입력",
        "분류별_상세 시트 A4:A7에 분류 목록 입력 (노트북, 모니터, 서버, 주변기기)",
        (
            "분류별_상세 시트 B4:B7에 수식 "
            "=SUMIF(Sales_Data!$I$2:$I$61,A4,Sales_Data!$J$2:$J$61) 적용"
        ),
        (
            "분류별_상세 시트 C4:C7에 수식 "
            "=SUMIF(Sales_Data!$I$2:$I$61,A4,Sales_Data!$K$2:$K$61) 적용"
        ),
        "분류별_상세 시트 A3:C3 배경색 #DDEBF7로 칠해줘",
        "분류별_상세 시트 A3:C3 글자 굵게 해줘",
        "분류별_상세 시트 A3:C7 범위에 경계선 적용해줘",
        "분류별_상세 시트 B4:C7에 숫자 형식 #,##0 적용",
        "분류별_상세 시트 열 너비 자동 맞춤",
    ]
    payload = json.dumps({"steps": example}, ensure_ascii=False)
    return (
        "예시 — 요청: \"Sales_Data로 매출 대시보드 만들어줘\"\n"
        "(Sales_Data 시트에 지역(C), 단가(E), 수량(F), 할인율(G), 원가(H), 분류(I) 열이 "
        "2~61행에 있는 상황)\n"
        f"{payload}"
    )


def build_macro_prompt(
    message: str,
    *,
    digest_text: str = "",
    phase: str = "",
    done_steps: list[str] | None = None,
) -> str:
    """분해 프롬프트를 만든다.

    `phase`를 주면 그 단계에 해당하는 명령만 내라고 요구한다. 한 번에 36단계를 뱉게 하면
    4.4GB 로컬 모델이 제한 시간 안에 온전한 JSON을 못 낸다 — 2026-08-16 실측에서 열이
    13개인 통합문서로 5회 중 4회가 타임아웃이거나 출력 절단이었다. 반으로 쪼개면
    한 번에 낼 길이가 절반이라 안정적으로 끝난다.

      structure — 값·수식·차트·시트 생성 (뼈대)
      format    — 병합·배경색·굵게·경계선·숫자서식 (마무리)
    """
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
        "   **파생 열(매출·이익처럼 새로 계산하는 값)은 위에 적힌 `빈열=` 위치부터 쓴다.**\n"
        "   아래 예시의 J·K는 그 통합문서의 빈 열일 뿐이다. 그대로 베끼지 마라 —\n"
        "   이미 값이 있는 열에 쓰면 원본이 사라진다.\n"
        "   기존 데이터가 있는 범위를 병합하지 않는다. 병합은 비어 있는 제목 줄에만 쓴다.\n"
        "5) 보기 좋게 만드는 서식도 계획에 넣는다. 데이터·수식·차트를 다 넣은 뒤 마지막에 모은다.\n"
        "   쓸 수 있는 것과 문구(이 형태를 그대로 쓴다):\n"
        "     - 제목 병합: \"<시트> 시트 A1:F2 병합해줘\"\n"
        "     - 배경색:   \"<시트> 시트 A1:F2 배경색 #1F4E79로 칠해줘\"\n"
        "     - 굵게:     \"<시트> 시트 A5:C5 글자 굵게 해줘\"\n"
        "     - 경계선:   \"<시트> 시트 A5:C11 범위에 경계선 적용해줘\"\n"
        "     - 숫자 형식: \"<시트> 시트 B6:C11에 숫자 형식 #,##0 적용\"\n"
        "     - 조건부 서식: \"<시트> 시트 F2:F9가 발주필요면 빨간 배경 조건부서식 넣어줘\"\n"
        "     - 글자 크기·색: \"<시트> 시트 A1 글씨 흰색 크기 16 굵게\"\n"
        "       (진한 배경 위 제목은 흰 글씨로. 색은 이름이나 #RRGGBB 둘 다 된다.)\n"
        "     - 아이콘·증감 표시: 도형은 못 넣지만 **이모지와 화살표는 셀 값으로 그대로** 쓴다.\n"
        "       예: \"<시트> 시트 A3에 📦 총 주문건수 입력\", \"<시트> 시트 C3에 ▲ 12.4% 입력\"\n"
        "       오름은 초록, 내림은 빨강으로: \"<시트> 시트 C3 글씨 초록\"\n"
        "   넣지 않는 것: Excel 표(ListObject), 도형·이미지 삽입.\n"
        "6) 사용자가 지우라고 하지 않았으면 기존 데이터를 지우거나 덮어쓰지 않는다.\n"
        f"7) 명령은 최대 {MAX_MACRO_STEPS}개까지.\n"
        "8) 순서가 중요하다. 값을 먼저 넣고 그 값을 참조하는 수식·차트를 뒤에 둔다.\n"
        "   수식이 참조하는 셀은 **그보다 앞선 단계가 반드시 값을 채워야 한다.**\n"
        "   예: B6:B11에 =SUMIF(...,A6,...)를 넣으려면 A6:A11 여섯 칸을 채우는 단계가 먼저 있어야 한다.\n"
        "   그 단계를 넣을 수 없으면 수식 단계도 계획에서 뺀다.\n"
        "9) 한 셀은 한 용도로만 쓴다. KPI는 레이블을 A열, 값을 B열에 나란히 둔다.\n"
        "   KPI 값을 넣은 셀을 뒤에서 다른 표의 머리글이나 수식 기준으로 재사용하지 않는다.\n"
        "10) 대시보드·보고서처럼 여러 관점을 담는 요청이면 요약 시트 한 장으로 끝내지 않는다.\n"
        "    통합문서에 실제로 있는 다른 축(월·분류·채널 등)으로 **상세 시트를 한 장 더** 만든다.\n"
        "    상세 시트도 머리글 + 집계 수식 + 서식까지 갖춘 완결된 표여야 한다.\n"
        "    단계가 모자라면 상세 시트를 아예 넣지 않는다 — 반쯤 만든 시트가 더 나쁘다.\n\n"
        f"{_fewshot_block()}\n\n"
        f"{_phase_block(phase, done_steps)}"
        f"요청: {message}"
    )


# 한 번에 다 뱉게 하면 로컬 모델이 못 버틴다. 두 번에 나눠 부르고 이어 붙인다.
MACRO_PHASES: tuple[str, ...] = ("structure", "format")

_PHASE_INSTRUCTIONS: dict[str, str] = {
    "structure": (
        "이번 호출에서는 **뼈대만** 낸다 — 시트 생성, 값 입력, 수식, 차트.\n"
        "서식(병합·배경색·굵게·경계선·숫자서식·글자색)은 **한 줄도 넣지 마라.** 다음 호출에서 받는다.\n\n"
    ),
    "format": (
        "이번 호출에서는 **마무리 서식만** 낸다 — 병합, 배경색, 굵게, 글자 크기·색, 경계선,\n"
        "숫자 형식, 조건부 서식, 열 너비 자동 맞춤, 틀 고정.\n"
        "값·수식·차트·시트 생성은 이미 끝났으니 **한 줄도 넣지 마라.**\n"
        "아래는 앞 호출이 이미 만들어 둔 명령 목록이다. 이 배치에 맞춰 서식을 입혀라.\n"
        "{done}\n\n"
    ),
}


def _phase_block(phase: str, done_steps: list[str] | None = None) -> str:
    template = _PHASE_INSTRUCTIONS.get(str(phase or ""), "")
    if not template:
        return ""
    if "{done}" not in template:
        return template
    listed = "\n".join(f"- {s}" for s in (done_steps or [])[:MAX_MACRO_STEPS])
    return template.replace("{done}", listed or "- (없음)")


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
    return _is_destructive(str(command or ""), _parse_or_none(command))


def _parse_or_none(command: Any) -> dict[str, Any] | None:
    """규칙 파서를 한 번만 돌린다. 파괴성 판정과 커버리지 검사가 같은 결과를 나눠 쓴다."""
    try:
        parsed = parse_command_rule_based(str(command or ""))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_destructive(text: str, parsed: dict[str, Any] | None) -> bool:
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
    # 셀 입도 커버리지. 시트 존재 여부만 보는 known_sheets로는 "이 수식이 참조하는
    # A6:A11을 채우는 단계가 계획에 있는가"를 알 수 없다.
    coverage = CoverageTracker(digest)
    active_sheet = str((digest or {}).get("active_sheet") or "").strip()
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

        parsed = _parse_or_none(command)
        # 읽기 판정을 먼저 한다 — 한 단계가 자기가 참조하는 셀을 스스로 채울 수는 없다.
        warnings.extend(coverage.check(command, parsed, fallback_sheet=active_sheet))
        warnings.extend(coverage.check_overwrite(command, parsed, fallback_sheet=active_sheet))
        coverage.record(command, parsed, fallback_sheet=active_sheet)

        steps.append(
            MacroStepPlan(
                index=len(steps) + 1,
                command=command,
                destructive=_is_destructive(command, parsed),
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
    timeout: float | None = None,
) -> list[MacroStepPlan]:
    """고수준 요청을 하위 명령 목록으로 펼친다.

    `timeout`을 안 주면 ollama_service의 기본값(120초)이 쓰인다. 호출자가 그보다 긴
    `wait_for` 예산을 잡아도 소켓이 먼저 끊겨 예산이 의미를 잃는다(2026-08-16 실측:
    바깥 180초, 안쪽 120초 → 124초에 ReadTimeout).
    """
    async def ask(phase: str, done: list[str]) -> list[str]:
        prompt = build_macro_prompt(
            message, digest_text=digest_text, phase=phase, done_steps=done
        )
        raw = await llm_service.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=MACRO_TEMPERATURE,
            json_only=True,
            timeout=timeout,
        )
        # 분해는 플래너 모델이 아니라 일반 대화 모델이 맡는다(`get_macro_model_name`).
        # 앞뒤로 설명을 붙이거나 사고 과정을 남기는 모델일 가능성이 그만큼 크다.
        parsed = extract_json_object(str(raw or ""), require_keys=("steps",))
        if parsed is None:
            raise ValueError("분해 결과 JSON을 찾지 못했습니다.")
        raw_steps = parsed.get("steps")
        if not isinstance(raw_steps, list):
            raise TypeError("분해 결과가 목록이 아닙니다.")
        return [" ".join(str(s or "").split()).strip() for s in raw_steps if str(s or "").strip()]

    # 뼈대를 먼저 받는다. 이건 실패하면 매크로 자체가 성립하지 않는다.
    commands = await ask(MACRO_PHASES[0], [])

    # 서식은 있으면 좋고 없어도 쓸 수 있다. 실패해도 뼈대는 살린다 —
    # 민무늬 대시보드가 "아무것도 없음"보다 낫다.
    try:
        commands.extend(await ask(MACRO_PHASES[1], commands))
    except Exception:
        pass

    return validate_macro_steps(commands, digest=digest)
