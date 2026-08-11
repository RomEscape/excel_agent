"""복잡 시나리오 검증 보고서에서 실패 건만 추려 읽기 쉬운 텍스트로 뽑는다.

verify_excel_complex_scenarios.py 가 남긴 JSON은 통과/실패가 섞여 있어 원인을 찾기 어렵다.
이 스크립트는 실패한 시나리오의 턴별 요청·판정·검증 결과만 보여 준다.

    uv run python scripts/show_scenario_failures.py [보고서경로]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_REPORT = Path(__file__).resolve().parents[2] / "logs" / "excel_complex_verify_report.json"


def _clip(value: object, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = json.loads(path.read_text(encoding="utf-8"))
    results = report.get("results") or report.get("scenarios") or []

    failures = [item for item in results if not item.get("passed")]
    print(f"보고서={path}")
    print(f"전체={len(results)} 실패={len(failures)}\n")

    for item in failures:
        print("=" * 70)
        print(f"[{item.get('id')}] critical={item.get('critical_failure')}")
        for issue in item.get("issues") or []:
            print(f"  이슈: {_clip(issue)}")
        for turn in item.get("turns") or []:
            result = turn.get("result") if isinstance(turn.get("result"), dict) else {}
            print(f"  - 요청: {_clip(turn.get('message'), 120)}")
            print(f"    판정: action={turn.get('action')} ok={turn.get('ok')}")
            if turn.get("reason"):
                print(f"    사유: {_clip(turn.get('reason'), 160)}")
            detail = turn.get("failure_detail") or result.get("failure_detail")
            if detail:
                print(f"    상세: {_clip(detail, 240)}")
            if turn.get("issues"):
                print(f"    턴이슈: {_clip(turn.get('issues'), 240)}")
            planned = turn.get("planned") or result.get("planned_steps")
            if planned:
                print(f"    계획: {_clip(planned, 600)}")
        for assertion in item.get("assertion_results") or []:
            if not assertion.get("ok"):
                print(f"  결과검증 실패: {_clip(assertion.get('detail'), 240)}")
        if item.get("effect_checks"):
            print(f"  결과검증: {_clip(item.get('effect_checks'), 600)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
