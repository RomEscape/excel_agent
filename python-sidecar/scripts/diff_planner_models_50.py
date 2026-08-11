"""
50개 실사용 명령을 두 플래너 모델로 각각 돌려 액션 불일치를 전부 모은다.

test_excel_live_50_commands는 첫 실패에서 멈추기 때문에, 모델을 바꿨을 때
회귀가 몇 건인지 한눈에 볼 수 없다. 배포 후보를 판단하려면 전체 목록이 필요하다.

사용:
    uv run python scripts/diff_planner_models_50.py \
        --models skt/A.X-4.0-Light:latest ax7bplanner-v2:latest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 모델별 50개 명령 액션 비교")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output-json", type=Path, default=Path("../logs/planner_50_diff.json"))
    return parser.parse_args()


def run_model(model: str) -> list[dict]:
    """모델을 환경변수로 고정한 뒤 앱을 새로 임포트해 50개 명령을 실행한다."""
    os.environ["OFFICECLAW_PLANNER_MODEL"] = model

    # 모듈 캐시를 비워야 planner 모델 변경이 반영된다.
    for name in list(sys.modules):
        if name.startswith("office_claw_sidecar") or name == "test_excel_live_50_commands":
            del sys.modules[name]

    import test_excel_live_50_commands as suite

    from office_claw_sidecar.routers import excel_live as excel_live_router

    # 테스트와 동일하게 실제 Excel COM 대신 가짜 서비스를 물린다.
    excel_live_router.get_excel_live_service = lambda: suite._FakeExcelService()

    results = []
    for case in suite.SCENARIOS:
        message = case["message"]
        expected = case["action"]
        try:
            resp = suite.client.post(
                "/excel-live/command",
                json={
                    "message": message,
                    "workbook_id": r"C:\work\sales.xlsx",
                    "sheet_name": "Sheet1",
                    "approve": False,
                },
                headers=suite.HEADERS,
            )
            actual = resp.json().get("action", "") if resp.status_code == 200 else ""
            error = "" if resp.status_code == 200 else f"HTTP {resp.status_code}"
        except Exception as exc:
            actual, error = "", str(exc)[:120]

        results.append(
            {
                "message": message,
                "expected": expected,
                "actual": actual,
                "match": actual == expected,
                "difficulty": case.get("difficulty", ""),
                "error": error,
            }
        )
    return results


def main() -> None:
    args = parse_args()
    report: dict[str, list[dict]] = {}

    for model in args.models:
        print(f"\n=== {model} ===")
        results = run_model(model)
        report[model] = results
        matched = sum(1 for r in results if r["match"])
        print(f"  {matched}/{len(results)} 일치 ({matched / len(results):.0%})")
        for r in results:
            if not r["match"]:
                print(f"  MISS [{r['difficulty']}] {r['message'][:45]!r}")
                print(f"       expected={r['expected']} actual={r['actual'] or '(실패)'} {r['error']}")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[DONE] {args.output_json}")


if __name__ == "__main__":
    main()
