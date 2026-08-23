"""의도 → 계획 **매핑 커버리지**. 통역 AI가 뜻을 맞게 뽑았을 때 몇 %가 계획이 되나.

    & $PY scripts/measure_intent_coverage.py            # 표로 출력
    & $PY scripts/measure_intent_coverage.py --json     # 기계용

**LLM을 부르지 않는다.** `intent_to_plan`은 결정적 함수라 몇 초에 끝나고, 게이트·배터리와
자원을 다투지 않는다 — 아무 때나 돌릴 수 있는 유일한 계측이다.

왜 필요한가(2026-08-23): 이 저장소에 `intent_to_plan`을 호출해 본 스크립트가 **한 건도
없었다**(grep 0건). 그래서 "통역이 뜻은 맞게 뽑았는데 계획으로 못 옮긴 비율"을 잰 숫자가
없었고, 받아 적기를 넓히는 작업의 전후를 비교할 근거가 없었다.

코퍼스는 **통역 AI가 낼 법한 intent JSON**이다(문장이 아니다). 모델을 안 부르므로
여기 적힌 intent가 곧 "모델이 완벽히 이해한 경우"다 — 그 상태에서도 계획이 안 나오면
그건 순수하게 **받아 적는 코드의 구멍**이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import Any

sys.path.insert(0, ".")

from office_claw_sidecar.services.excel_intent_normalizer import (
    TASK_NAMES,
    intent_to_plan,
)

#: 계측용 통합문서 — 머리글이 있어야 열 이름을 좌표로 옮기는 경로가 산다.
DIGEST: dict[str, Any] = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:F9",
            "columns": [
                {"letter": "A", "header": "날짜"},
                {"letter": "B", "header": "지역"},
                {"letter": "C", "header": "담당자"},
                {"letter": "D", "header": "금액"},
                {"letter": "E", "header": "상태"},
                {"letter": "F", "header": "비고"},
            ],
            "sample_rows": [["2026-01-02", "서울", "김", 1200, "완료", ""]],
        }
    ],
}


def case(task: str, *, note: str, message: str = "", **intent: Any) -> dict[str, Any]:
    """한 케이스 = (통역 AI가 냈을 intent, 사람이 친 문장, 무엇을 재는지)."""
    return {
        "task": task,
        "note": note,
        "message": message,
        "intent": {"task": task, "range": None, "column": None, "option": None, **intent},
    }


#: 종류마다 **가장 흔한 모양 하나 + 지금 떨어지는 모양들**을 넣는다.
#: 떨어지는 케이스에는 note에 왜 떨어지는지 적어 둔다 — 고치면 그 줄이 초록으로 바뀐다.
CASES: list[dict[str, Any]] = [
    # ── 이미 매핑된다고 알려진 것들 ────────────────────────────────
    case("fill_color", range="B2:B9", option="노란색", note="범위+색", message="B2:B9 노란색"),
    case("fill_color", column="금액", option="노란색", note="열 이름+색", message="금액 열 노란색"),
    case("font", range="A1:F1", option="흰색", note="글자색", message="머리글 흰색"),
    case("font", range="A1:F1", option="굵게", note="굵게 — 색 아니면 매핑 실패", message="머리글 굵게"),
    case("font", range="A1:F1", option="14", note="크기", message="머리글 14로"),
    case("font", range="A1:F1", option="가운데", note="가로 맞춤", message="머리글 가운데 정렬"),
    case("number_format", column="금액", option="천 단위", note="콤마", message="금액 콤마"),
    case("formula", range="F2", column="금액", option="SUM", note="한 칸 집계", message="F2에 금액 합계"),
    case("sort", column="금액", option="desc", note="내림차순", message="금액 내림차순"),
    case("sort", column="금액", option=None, note="방향 없음", message="금액 정렬"),
    case("filter", column="지역", option="서울", note="값 일치", message="서울만"),
    case("filter", column="금액", option=">=1000", note="비교 — 값 일치만 지원", message="금액 1000 이상만"),
    case("clear_values", range="A2:F9", note="범위 비우기", message="A2:F9 비워줘"),
    case("clear_values", column="비고", note="열 비우기", message="비고 열 비워줘"),
    case("reset_all", range="A1:F9", note="서식 초기화", message="A1:F9 초기화"),
    case("find_replace", option={"find": "서울", "replace": "SEOUL"}, note="찾아 바꾸기", message="서울을 SEOUL로"),
    case("write_value", range="A12", option="합계", note="한 칸 쓰기", message="A12에 합계"),
    case("write_value", range="A2:A9", option="미정", note="범위 브로드캐스트", message="A2:A9에 미정"),
    case("write_value", column="비고", option="미정", note="열 전체", message="비고 열 전부 미정"),
    # ── 매핑이 아예 없는 종류들 ────────────────────────────────────
    case("highlight", column="금액", option="50 이상", note="조건부 강조", message="금액 50 넘는 것만"),
    case("read", column="금액", option="SUM", note="조회", message="금액 합계 알려줘"),
    case("dedupe", note="중복 제거", message="중복 행 지워줘"),
    case("chart", column="금액", option="bar", note="차트", message="금액 막대 그래프"),
    case("pivot", column="지역", option="SUM", note="피벗", message="지역별 금액 합계"),
    case("create_table", range="A1:F9", note="표 생성", message="A1:F9 표로"),
    case("other", note="폴백 버킷 — 매핑하지 않는 것이 정상", message="이거 어떻게 써?"),
]


def run() -> list[dict[str, Any]]:
    out = []
    for c in CASES:
        try:
            plan = intent_to_plan(c["intent"], digest=DIGEST, message=c["message"])
            error = ""
        except Exception as exc:  # 매핑이 터지면 그것도 결과다
            plan, error = None, f"{type(exc).__name__}: {exc}"
        # 반환 키는 `action_plan`이다(`steps`가 아니다 — 2026-08-23에 여기서 한 번 헛짚었다).
        steps = (plan or {}).get("action_plan") or []
        out.append(
            {
                **{k: c[k] for k in ("task", "note", "message")},
                "mapped": bool(steps),
                "actions": [str(s.get("action") or "") for s in steps],
                "error": error,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = run()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)

    print(f"{'종류':<14} {'케이스':>6} {'매핑됨':>6}  떨어지는 모양")
    print("-" * 78)
    for task in TASK_NAMES:
        group = by_task.get(task) or []
        if not group:
            continue
        mapped = sum(1 for r in group if r["mapped"])
        missing = [r["note"] for r in group if not r["mapped"]]
        print(f"{task:<14} {len(group):>6} {mapped:>6}  {', '.join(missing)}")

    total = Counter(r["mapped"] for r in rows)
    tasks_with_any = {r["task"] for r in rows if r["mapped"]}
    print("-" * 78)
    print(
        f"케이스 {len(rows)}개 중 매핑 {total[True]}개 ({100 * total[True] / len(rows):.0f}%) · "
        f"매핑이 하나라도 있는 종류 {len(tasks_with_any)}/{len(TASK_NAMES)}"
    )
    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n예외 {len(errors)}건:")
        for r in errors:
            print(f"  [{r['task']}] {r['note']} → {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
