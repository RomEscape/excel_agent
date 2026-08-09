"""
Excel Live 플래너 프롬프트 단일 소스.

프로덕션 추론(`excel_live_agent.parse_command_plan_with_llm`)과 SFT 데이터
생성(`scripts/build_planner_sft_jsonl.py`)이 **같은 함수**로 프롬프트를 만든다.

이 모듈이 생긴 이유: 학습 데이터는
`"너는 Excel 플래너다. JSON만 출력해라." + 사용자 문장`이라는 짧은 프롬프트로
만들어졌는데, 실제 추론은 액션 카탈로그·통합문서 다이제스트·reasoning 지침이
붙은 훨씬 긴 프롬프트를 보냈다. 모델이 학습 때 본 적 없는 형식을 받게 되어
파인튜닝 효과가 사라졌다. 두 경로가 다시 어긋나지 않도록 여기로 모은다.
"""

from __future__ import annotations

from typing import Any

from office_claw_sidecar.services.excel_live_plan_validator import EDIT_ACTIONS

# 프롬프트에 붙일 설명. 이름만 나열하면 소형 모델이 highlight_by_condition과 헷갈린다.
LATER_TOOL_LINES = (
    "- excel_live.calculate_column_stat (한 열의 합계/평균/최대 등을 '계산해서 알려주기'. 시트 수정 없음)\n"
    "- excel_live.group_by_aggregate (그룹별 집계를 '알려주기'만. 새 시트에 쓰려면 pivot_table)\n"
    "- excel_live.sort_rows (머리글 이름 기준 정렬)\n"
    "- excel_live.drop_column (열 통째로 삭제. '이 열 지워줘'는 clear_range가 아니라 이것)\n"
    "- excel_live.rename_column (열 머리글 이름만 변경)\n"
    "- excel_live.add_column (맨 뒤 또는 지정 위치에 새 열 추가)\n"
    "- excel_live.set_data_validation (입력 값 제한/드롭다운 목록)\n"
    "- excel_live.protect_sheet (시트 잠금/보호)\n"
    "- excel_live.find_replace (텍스트 찾아서 다른 텍스트로 바꾸기, 일괄 치환)\n"
    "- excel_live.merge_cells (지정 범위 셀 병합)\n"
    "- excel_live.unmerge_cells (병합된 셀 해제)\n"
    "- excel_live.freeze_panes (머리글 행/열 틀 고정. freeze_at은 고정선 바로 아래/오른쪽 셀, 예: 첫 행 고정=\"A2\")\n"
    "- excel_live.autofit_columns (열 너비를 내용에 맞게 자동 조정)\n"
    "- excel_live.define_named_range (지정 범위에 이름 정의, name은 영문 식별자)\n"
    "- excel_live.set_print_area (인쇄 영역/용지 방향/한 페이지 맞춤 설정)\n"
    "- excel_live.add_cell_comment (셀 하나에 메모/코멘트 추가)\n"
    "- excel_live.apply_color_scale (값 크기에 따라 색을 점진적으로 칠하는 색조 조건부 서식)\n"
    "- excel_live.apply_data_bar (값 크기를 셀 안 막대로 시각화하는 데이터 막대 조건부 서식)\n"
    "- excel_live.set_number_format (표시 형식 변경. format_code는 'percent'/'comma'/'currency'/'date'"
    " 같은 개념어 또는 실제 Excel 서식 코드)\n"
    "- excel_live.compare_ranges (두 범위를 비교해 차이 나는 셀/값을 찾기)\n"
    "- excel_live.consolidate_sheets (같은 통합문서의 여러 시트를 하나로 통합)\n"
    "- excel_live.consolidate_workbooks_from_folder (폴더 안 여러 Excel 파일을 현재 통합문서로 통합)\n"
    "- excel_live.forecast_linear (시계열 숫자의 단순 선형 추세 예측)\n"
    "- excel_live.refresh_power_query (Power Query/외부 연결 새로고침)\n"
    "- excel_live.run_vba_macro (이름을 지정한 VBA 매크로 실행)\n"
)

# 편집 액션 목록은 검증기가 단일 소스다. 여기에 다시 적어 두면 한쪽만 갱신되어
# 프롬프트가 "편집 액션이 아니다"라고 말한 액션을 검증기는 편집으로 처리하는
# 어긋남이 생긴다. 실제로 clear_range 등 7종이 그렇게 누락돼 있었다.
_EDIT_ACTION_NAMES = "/".join(
    sorted(name.removeprefix("excel_live.") for name in EDIT_ACTIONS)
)

VALID_REASONING_MODES = frozenset({"fast", "deep", "reflect"})


def _reasoning_line(mode: str, complexity_score: int, reflection_note: str, previous_first_action: str) -> str:
    if mode == "deep":
        return (
            "추가 지침(Deep reasoning): 복잡 명령이다. 실행 전 내부적으로 단계/의존성을 점검하고 "
            "누락 파라미터를 보수적으로 채운 뒤 JSON만 반환하라.\n"
            f"복잡도 점수={complexity_score}\n"
        )
    if mode == "reflect":
        return (
            "추가 지침(Reflection 1회): 이전 저신뢰 계획을 교정하는 재해석 단계다. "
            "원인(의도 불일치/수동 액션 과다)을 피해서 더 직접적인 실행 계획으로 수정하라.\n"
            f"reflection_note={reflection_note or 'none'}, "
            f"previous_first_action={previous_first_action or 'none'}\n"
        )
    return (
        "추가 지침(Fast path): 단순 명령 우선, 불필요한 단계 분해를 피하고 최소 단계로 계획하라.\n"
        f"복잡도 점수={complexity_score}\n"
    )


def normalize_planner_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """
    프롬프트 조립에 쓰이는 컨텍스트 값만 뽑아 정규화한다.

    프로덕션과 데이터 생성이 동일한 정규화를 거쳐야 프롬프트가 바이트 단위로 같아진다.
    """
    context = context or {}
    reasoning_mode = str(context.get("reasoning_mode", "fast") or "").strip().lower()
    if reasoning_mode not in VALID_REASONING_MODES:
        reasoning_mode = "fast"
    try:
        complexity_score = int(context.get("complexity_score", 0))
    except (TypeError, ValueError):
        complexity_score = 0

    return {
        "context_range": str(context.get("context_range", "") or "").strip().upper(),
        "workbook_id": str(context.get("workbook_id", "") or "").strip(),
        "sheet_name": str(context.get("sheet_name", "") or "").strip(),
        "reasoning_mode": reasoning_mode,
        "complexity_score": complexity_score,
        "reflection_note": str(context.get("reflection_note", "") or "").strip(),
        "previous_first_action": str(context.get("previous_first_action", "") or "").strip(),
        "personalization_hint": str(context.get("personalization_hint", "") or "").strip(),
        "workbook_digest_text": str(context.get("workbook_digest_text") or ""),
    }


def build_planner_prompt(
    message: str,
    *,
    context: dict[str, Any] | None = None,
    planner_model: str = "",
    forbid_list_action: bool = False,
    require_edit_action: bool = False,
) -> str:
    """
    플래너 LLM에 보낼 단일 user 메시지 본문을 만든다.

    호출자는 이 문자열을 그대로 `[{"role": "user", "content": prompt}]`로 보낸다.
    system 역할은 쓰지 않는다 — 학습 데이터도 반드시 같은 구조여야 한다.
    """
    ctx = normalize_planner_context(context)

    context_line = (
        f"최근 컨텍스트: workbook_id={ctx['workbook_id'] or 'auto'}, "
        f"sheet={ctx['sheet_name'] or 'auto'}, "
        f"context_range={ctx['context_range'] or 'none'}\n"
    )

    personalization_line = ""
    if ctx["personalization_hint"]:
        personalization_line = (
            "추가 지침(Persona memory): 아래 사용자 개인화 힌트를 우선 참고하되, "
            "안전하지 않거나 모호한 경우에는 follow_up_question으로 되물어라.\n"
            f"{ctx['personalization_hint'][:1200]}\n"
        )

    reasoning_line = _reasoning_line(
        ctx["reasoning_mode"],
        ctx["complexity_score"],
        ctx["reflection_note"],
        ctx["previous_first_action"],
    )

    return (
        "너는 Excel Live 작업 플래너다. 사용자 메시지를 실행 계획 JSON으로만 반환해라.\n"
        "허용 action:\n"
        "- excel_live.list_workbooks\n"
        "- excel_live.select_workbook\n"
        "- excel_live.list_sheets\n"
        "- excel_live.select_sheet\n"
        "- excel_live.create_sheet\n"
        "- excel_live.read_range\n"
        "- excel_live.write_range\n"
        "- excel_live.create_table\n"
        "- excel_live.highlight_by_condition\n"
        "- excel_live.fill_range\n"
        "- excel_live.clear_range (범위의 값/수식을 비우기. 서식은 유지)\n"
        "- excel_live.save_workbook\n"
        "- excel_live.apply_border\n"
        "- excel_live.set_formula\n\n"
        "- excel_live.verify_formula_result\n\n"
        "- excel_live.sort_range\n"
        "- excel_live.filter_rows\n"
        "- excel_live.dedupe_rows (중복 '제거')\n"
        "- excel_live.find_duplicates (중복을 지우지 않고 '찾기/확인')\n"
        "- excel_live.pivot_table\n"
        "- excel_live.create_chart\n"
        "- excel_live.validate_data\n"
        "- excel_live.recalculate (수식 갱신/새로고침)\n"
        "- excel_live.export_pdf\n"
        f"{LATER_TOOL_LINES}\n"
        "규칙:\n"
        "1) JSON 외 텍스트 금지\n"
        "2) action_plan은 1~4개 단계\n"
        "3) 각 단계는 action/params/reason 포함\n"
        "4) 범위가 없으면 __ACTIVE_SELECTION__ 또는 __ACTIVE_CELL__ 사용 가능\n"
        "4-1) context_range가 주어졌고 사용자가 '이 범위/여기/전반적으로'처럼 모호하게 말하면 context_range를 우선 사용\n"
        "5) plan 상위에 intent를 반드시 포함한다: edit | read | navigate\n"
        "6) intent=edit이면 첫 단계는 편집 action이어야 한다\n"
        f"   ({_EDIT_ACTION_NAMES})\n"
        f"6) forbid_list_action={str(bool(forbid_list_action)).lower()} 일 때 첫 단계를 excel_live.list_workbooks로 반환하면 안 된다\n\n"
        f"7) require_edit_action={str(bool(require_edit_action)).lower()} 일 때 첫 단계는 반드시 편집 액션이어야 한다\n"
        f"   (편집 액션: {_EDIT_ACTION_NAMES})\n\n"
        "8) create_table 정보가 부족하면 slot_fill/partial_params/follow_up_question를 함께 넣을 수 있다\n"
        "   - slot_fill 예: {\"rows\":5,\"cols\":5,\"headers\":[\"금액\",\"장소\"]}\n"
        "   - follow_up_question 예: \"표 크기와 헤더를 알려주세요. 예: 5*5, 금액, 장소, 날짜\"\n\n"
        f"9) 현재 planner 모델 힌트: {planner_model}\n"
        "10) 아래 '통합문서 상태'에 실제 시트명과 머리글이 있다. 열은 반드시 그 머리글 이름\n"
        "    (예: key_column=\"금액\")으로 지정하고, 시트명을 열 이름으로 쓰지 마라.\n"
        "11) 결과를 새로 써 넣는 액션(pivot_table/forecast_linear/compare_ranges/consolidate)의\n"
        "    output_sheet는 원본 시트와 달라야 한다. 원본을 덮어쓰면 데이터가 사라진다.\n"
        "12) 범위를 특정할 수 있으면 __ACTIVE_SELECTION__ 대신 실제 사용범위를 써라.\n"
        "13) 사용자가 말하지 않은 편집(테두리·색칠·정렬·표 생성)을 임의로 덧붙이지 마라.\n"
        "14) 머리글이 영문이어도 사용자는 한국어로 부른다. '지역'→Region, '매출'→Sales처럼\n"
        "    통합문서 상태의 실제 머리글로 바꿔서 params에 넣어라.\n\n"
        f"{ctx['workbook_digest_text']}"
        f"{context_line}"
        f"{personalization_line}"
        f"{reasoning_line}"
        "출력 형식:\n"
        '{"intent":"edit","mutates_workbook":true,"action_plan":[{"action":"excel_live.fill_range","params":{"target_range":"__ACTIVE_SELECTION__","fill_color":"#FFFF00"},"reason":"범위 배경색 변경"}],"slot_fill":{},"partial_params":{},"follow_up_question":"","reason":"한 줄 한국어"}\n\n'
        f"사용자 메시지: {message}"
    )
