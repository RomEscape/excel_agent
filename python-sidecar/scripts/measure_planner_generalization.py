"""플래너 일반화 실험 — 같은 의도, 훈련체 vs 사용자체.

사용자 질문: "이 정도 작은 학습 데이터로 다양한 질문 형태를 처리하는 게 가능한가?"
직접 잰다: 6개 의도 × 6개 표현(훈련체 2 + 사용자체 4)을 규칙 우회로 플래너에만
보내고, 액션·핵심 파라미터 정답률을 훈련체/사용자체로 갈라 본다.

프롬프트는 실제 경로와 같은 build_planner_prompt, temperature도 실전과 동일.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

sys.path.insert(0, ".")

from office_claw_sidecar.services.excel_live_agent import PLAN_TEMPERATURE
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt
from office_claw_sidecar.services.llm_json import extract_json_object
from office_claw_sidecar.services.llm_service import get_llm_service

DIGEST = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:D9",
            "columns": [
                {"letter": "A", "header": "날짜"},
                {"letter": "B", "header": "지역"},
                {"letter": "C", "header": "담당자"},
                {"letter": "D", "header": "금액"},
            ],
        }
    ],
}
CONTEXT = {"workbook_digest": DIGEST}

# (의도, 정답 판정 함수, [(스타일, 문장)])  스타일: T=훈련체, U=사용자체
def _is(action):
    return lambda a, p: a == action

def _formula_sum_d(a, p):
    return a == "excel_live.set_formula" and "SUM" in str(p.get("formula_a1", "")).upper() and "D" in str(p.get("formula_a1", ""))

def _sort_desc(a, p):
    return a == "excel_live.sort_range" and str(p.get("order", "")).lower().startswith("desc")

def _filter_seoul(a, p):
    return a == "excel_live.filter_rows" and "서울" in json.dumps(p, ensure_ascii=False)

def _numfmt(a, p):
    return a == "excel_live.set_number_format" and (
        "#" in str(p.get("format_code", "")) or "0" in str(p.get("format_code", ""))
    ) and not re.search(r"[가-힣]", str(p.get("format_code", "")))

CASES = [
    ("배경색", _is("excel_live.fill_range"), [
        ("T", "A1:D1 노란색으로 채워줘"),
        ("T", "A1:D1 배경 노란색 적용"),
        ("U", "A1:D1 노란색으로 채워줄 수 있어?"),
        ("U", "혹시 A1:D1 좀 노랗게 해줄래?"),
        ("U", "머리글이 눈에 띄게 A1:D1 배경을 노랗게 하고 싶은데"),
        ("U", "A1:D1 이 부분 노랗게 만들어주면 좋겠어"),
    ]),
    ("합계수식", _formula_sum_d, [
        ("T", "F2에 금액 합계 수식 넣어줘"),
        ("T", "F2에 금액 합계 적용"),
        ("U", "F2에 금액 다 더한 값 넣어줄 수 있어?"),
        ("U", "금액 전부 합쳐서 F2에 보여줄래?"),
        ("U", "F2에다 금액 총합 좀 계산해줬으면 하는데"),
        ("U", "매출 총액이 얼마인지 F2에 넣어놔줘"),
    ]),
    ("내림차순정렬", _sort_desc, [
        ("T", "금액 기준 내림차순 정렬해줘"),
        ("T", "금액 내림차순 정렬 적용"),
        ("U", "금액 큰 순서대로 정렬해줄 수 있어?"),
        ("U", "금액 많은 것부터 위로 오게 해줄래?"),
        ("U", "비싼 거래가 먼저 보이게 좀 정리해줬으면 해"),
        ("U", "금액이 높은 순으로 줄 세워줘"),
    ]),
    ("필터", _filter_seoul, [
        ("T", "지역이 서울인 행만 필터해줘"),
        ("T", "지역 서울 필터 적용"),
        ("U", "서울 데이터만 남겨줄 수 있어?"),
        ("U", "서울 지역 것만 보고 싶은데"),
        ("U", "서울 아닌 데는 좀 치워줄래?"),
        ("U", "서울 거래만 추려줘"),
    ]),
    ("표시형식", _numfmt, [
        ("T", "D2:D9 천 단위 콤마 적용해줘"),
        ("T", "금액 열 천 단위 구분 기호 적용"),
        ("U", "금액에 천 단위 콤마 넣어줄 수 있어?"),
        ("U", "금액 숫자 읽기 편하게 콤마 좀 찍어줄래?"),
        ("U", "금액이 잘 안 읽혀서 자릿수 구분 해줬으면 해"),
        ("U", "D열 숫자에 쉼표 표시되게 해줘"),
    ]),
    ("중복제거", _is("excel_live.dedupe_rows"), [
        ("T", "중복 행 제거해줘"),
        ("T", "중복 데이터 제거 적용"),
        ("U", "중복된 행 지워줄 수 있어?"),
        ("U", "똑같은 행이 두 번 들어갔는데 정리해줄래?"),
        ("U", "겹치는 데이터 좀 없애줬으면 해"),
        ("U", "같은 내용이 반복된 줄은 빼줘"),
    ]),
]


async def main() -> None:
    llm = get_llm_service()
    model = "ax7bplanner-v3:latest"
    stats = {"T": [0, 0], "U": [0, 0]}
    rows = []
    for intent, judge, phrasings in CASES:
        for style, msg in phrasings:
            prompt = build_planner_prompt(msg, context=CONTEXT, planner_model=model)
            try:
                raw = await llm.chat(
                    [{"role": "user", "content": prompt}],
                    model=model, temperature=PLAN_TEMPERATURE, json_only=True, timeout=60,
                )
                parsed = extract_json_object(raw, require_keys=("action_plan", "action")) or {}
            except Exception as exc:
                parsed = {"action": f"(오류: {exc})"}
            plan = parsed.get("action_plan") or []
            first = plan[0] if plan and isinstance(plan[0], dict) else {}
            action = str(first.get("action") or parsed.get("action") or "")
            params = first.get("params") or parsed.get("params") or {}
            ok = bool(judge(action, params))
            stats[style][0] += ok
            stats[style][1] += 1
            mark = "OK  " if ok else "FAIL"
            print(f"{mark} [{style}] {intent:6s} {msg[:34]:36s} → {action[11:] or action} {json.dumps(params, ensure_ascii=False)[:60]}", flush=True)
            rows.append((intent, style, msg, action, ok))

    print("\n" + "=" * 78)
    for style, name in (("T", "훈련체"), ("U", "사용자체")):
        ok, total = stats[style]
        print(f"{name}: {ok}/{total} ({ok/total*100:.0f}%)")


asyncio.run(main())
