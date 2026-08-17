"""매크로 분해 품질 측정 — 경고를 클래스별로 집계한다.

이전 실측(개발일지 2026-08-16): 4케이스에서 병합 리터럴을 지우자 경고가 24→38로
늘었다(병합→덮어쓰기 이동). 총량만 보면 두더지잡기라, 클래스별로 갈라서
"어떤 종류가 지배적인가"를 본다. 수리 로직을 넣으면 전/후 비교의 기준선이 된다.

실행:  & $PY scripts\\measure_macro_quality.py [--label 이름]
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

sys.path.insert(0, ".")

from office_claw_sidecar.services.excel_macro_planner import decompose_macro_request
from office_claw_sidecar.services.llm_service import get_llm_service, get_macro_model_name

# 데이터가 A~H에 이미 차 있는 워크북 — 파생 열은 I 이후가 맞다.
DIGEST_TEXT = """현재 통합문서 상태(실제 파일에서 읽음):
- 시트 Sales_Data (활성) 사용범위=A1:H61
  열: A=주문일 | B=주문번호 | C=지역 | D=제품 | E=단가 | F=수량 | G=할인율 | H=원가
  예시행: 2026-01-03 | ORD-001 | 서울 | 노트북 | 1200000 | 2 | 0.05 | 900000
"""
DIGEST = {
    "active_sheet": "Sales_Data",
    "sheets": [
        {
            "name": "Sales_Data",
            "used_range": "A1:H61",
            "columns": [
                {"letter": c, "header": h}
                for c, h in zip("ABCDEFGH", ["주문일", "주문번호", "지역", "제품", "단가", "수량", "할인율", "원가"])
            ],
        }
    ],
}

CASES = [
    "Sales_Data로 매출 대시보드 만들어줘",
    "월간 보고서 작성해줘",
    "지역별 실적 현황판 구축해줘",
    "판매 분석 자료 만들어줘",
    "제품별 수익성 정리한 리포트 만들어줘",
]

CLASSES = [
    ("병합 파괴", re.compile(r"병합하면.*사라집니다")),
    ("덮어쓰기", re.compile(r"덮어씁니다")),
    ("없는 시트", re.compile(r"시트는 지금 통합문서에 없습니다")),
    ("빈 참조", re.compile(r"채워|비어")),
]


async def main() -> None:
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "측정"
    llm = get_llm_service()
    model = get_macro_model_name()
    print(f"[{label}] 모델: {model}, 케이스 {len(CASES)}건\n")

    totals: dict[str, int] = {name: 0 for name, _ in CLASSES}
    totals["기타"] = 0
    total_steps = total_warn_steps = 0
    for case in CASES:
        try:
            steps = await decompose_macro_request(
                case, llm, digest=DIGEST, digest_text=DIGEST_TEXT, model=model
            )
        except Exception as exc:
            print(f"  분해 실패: {case} → {exc}")
            continue
        warn_steps = [s for s in steps if s.warnings]
        total_steps += len(steps)
        total_warn_steps += len(warn_steps)
        case_counts: dict[str, int] = {}
        for s in steps:
            for w in s.warnings:
                for name, pattern in CLASSES:
                    if pattern.search(w):
                        totals[name] += 1
                        case_counts[name] = case_counts.get(name, 0) + 1
                        break
                else:
                    totals["기타"] += 1
                    case_counts["기타"] = case_counts.get("기타", 0) + 1
        print(f"  {case[:30]:32s} 단계 {len(steps):2d} | 경고 단계 {len(warn_steps):2d} | {case_counts or '깨끗'}")
        for s in warn_steps[:3]:
            print(f"      예: {s.command[:52]} → {s.warnings[0][:56]}")

    rate = (total_warn_steps / total_steps * 100) if total_steps else 0.0
    print(f"\n[{label}] 총 단계 {total_steps} | 경고 단계 {total_warn_steps} ({rate:.0f}%)")
    print(f"[{label}] 클래스별: {json.dumps(totals, ensure_ascii=False)}")


asyncio.run(main())
