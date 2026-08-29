"""규칙표와 의도 해석이 **얼마나 자주 다른 답을 내는가**, 그리고 그 불일치가 오답을 예고하는가.

    & $PY scripts/measure_rule_intent_agreement.py            # 기본 120문장
    & $PY scripts/measure_rule_intent_agreement.py 40         # 앞 40문장만

왜 필요한가(2026-08-24): 로드맵 그림 3의 목표는 "둘의 판단이 다르면 실행하지 않고 묻는다"다.
그런데 그 **불일치가 얼마나 잦은지, 그리고 정말 위험 신호인지**를 잰 적이 없다.
- 너무 잦으면 되묻기가 폭증해 쓸 수 없다.
- 불일치가 오답과 무관하면 교차검증은 그냥 잡음이다.

자기보고 확신도는 이미 반증됐다 — 같은 날 44문장 전부 `high`였고 틀린 5건도 high였다.
모델은 애매해서 틀리는 게 아니라 **확신하며 틀린다.** 그래서 신호는 밖에서 와야 한다.

판정에 오라클은 쓰지 않는다. 여기서 재는 것은 정답률이 아니라 **두 경로의 일치율**이다.
"""

from __future__ import annotations

import asyncio
import collections
import json
import pathlib
import sys

sys.path.insert(0, ".")

from office_claw_sidecar.routers.excel_live import _build_quick_action_plan
from office_claw_sidecar.services.excel_intent_normalizer import (
    normalize_intent,
)
from office_claw_sidecar.services.excel_live_agent import normalize_common_typos
from office_claw_sidecar.services.llm_service import get_llm_service

CASES = pathlib.Path("../../datasets/eval/blind_paraphrases_v1.jsonl")

#: 게이트 씨앗과 같은 표를 흉내 낸 다이제스트. 열 이름이 있어야 통역이 열을 가리킬 수 있다.
DIGEST = {
    "active_sheet": "지역성과",
    "sheets": [
        {
            "name": "지역성과",
            "used_range": "A1:F6",
            "columns": [
                {"letter": "A", "header": "지역"},
                {"letter": "B", "header": "주문건수"},
                {"letter": "C", "header": "출고건수"},
                {"letter": "D", "header": "정시배송률"},
                {"letter": "E", "header": "지연건수"},
                {"letter": "F", "header": "클레임"},
            ],
            "sample_rows": [["수도권", 10452, 10158, 97.1, 145, 12]],
        }
    ],
}


#: 게이트 씨앗과 같은 표라서 문맥도 그것으로 준다 — 없으면 "표 아래 합계" 같은
#: 문맥 의존 규칙이 통째로 안 걸려 비교가 성립하지 않는다.
CONTEXT_RANGE = "A1:F6"

#: 작업 종류 → 그 종류가 내야 할 액션들. **계획이 아니라 종류로 견준다** —
#: 해석이 `highlight`로 분류하고도 매핑이 없어 계획을 안 내면, 계획끼리 견줘서는
#: 그 불일치가 보이지 않는다(2026-08-24: `B2:B9 노란색`을 해석은 highlight로 봤다).
TASK_ACTIONS: dict[str, set[str]] = {
    "fill_color": {"excel_live.fill_range"},
    "font": {"excel_live.set_font"},
    "highlight": {"excel_live.highlight_by_condition", "excel_live.apply_formula_cf"},
    "number_format": {"excel_live.set_number_format"},
    "formula": {"excel_live.set_formula", "excel_live.calculate_column_stat"},
    "sort": {"excel_live.sort_range", "excel_live.sort_rows"},
    "filter": {"excel_live.filter_rows"},
    "clear_values": {"excel_live.clear_range"},
    "reset_all": {"excel_live.clear_range", "excel_live.apply_border"},
    "write_value": {"excel_live.write_range"},
    "find_replace": {"excel_live.find_replace"},
    "create_table": {"excel_live.create_table", "excel_live.convert_to_excel_table"},
    "pivot": {"excel_live.pivot_table"},
    "chart": {"excel_live.create_chart"},
    "dedupe": {"excel_live.dedupe_rows", "excel_live.find_duplicates"},
    "read": {"excel_live.read_range", "excel_live.calculate_column_stat"},
}


def _action_of(plan) -> str:
    if not plan:
        return ""
    steps = plan if isinstance(plan, list) else (plan.get("action_plan") or [])
    return str((steps[0] or {}).get("action") or "") if steps else ""


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    every = [json.loads(x) for x in CASES.read_text(encoding="utf-8").splitlines() if x.strip()]
    # 코퍼스는 **과제별로 묶여** 있다 — 앞에서 N개를 자르면 한 과제만 재게 된다.
    # 띄엄띄엄 뽑아 종류가 골고루 섞이게 한다.
    stride = max(1, len(every) // max(limit, 1))
    rows = every[::stride][:limit]
    llm = get_llm_service()

    tally = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)

    for i, row in enumerate(rows, 1):
        text = normalize_common_typos(str(row["text"]))
        rule = _action_of(_build_quick_action_plan(text, CONTEXT_RANGE))
        try:
            intent = await normalize_intent(text, DIGEST, llm) or {}
        except Exception:
            intent = {}
        task = str(intent.get("task") or "")
        expected = TASK_ACTIONS.get(task, set())
        if rule and expected:
            key = "일치" if rule in expected else "불일치"
        elif rule:
            # 해석이 종류를 못 정했거나, 매핑 대상이 아닌 종류(other 등).
            key = "규칙만"
        elif task:
            key = "해석만"
        else:
            key = "둘 다 없음"
        tally[key] += 1
        if key == "불일치" and len(examples[key]) < 14:
            examples[key].append(f"{row['text'][:38]:40} 규칙={rule[12:]:24} 해석={task}")
        print(f"[{i:3d}/{len(rows)}] {key:6} {row['text'][:44]}", flush=True)

    total = sum(tally.values())
    print("\n" + "=" * 78)
    print(f"문장 {total}개")
    for key in ("일치", "불일치", "규칙만", "해석만", "둘 다 없음"):
        n = tally[key]
        print(f"  {key:8} {n:4d} ({100 * n / max(total, 1):5.1f}%)")
    both = tally["일치"] + tally["불일치"]
    if both:
        print(f"\n둘 다 계획을 낸 {both}건 중 불일치 {tally['불일치']}건 "
              f"({100 * tally['불일치'] / both:.1f}%) — 교차검증이 되묻을 비율")
    if examples["불일치"]:
        print("\n불일치 예시:")
        for line in examples["불일치"]:
            print(f"  {line}")


asyncio.run(main())
