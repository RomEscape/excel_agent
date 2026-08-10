"""Excel BasicBench v1 — 실제 플래너로 종단 성공률을 잰다.

## 기존 평가와의 관계

`eval_ax7b_shadow.py`는 154건에서 **액션 이름**이 맞는지 본다. 빠르고 표본이
크지만, 올바른 액션을 고르고 엉뚱한 열에 적용하는 실패를 잡지 못한다.

이 스크립트는 10건뿐이지만 실제 .xlsx를 만들고, 플래너가 낸 계획을 실행하고,
**저장된 파일을 다시 열어** 채점한다. 여기서 통과했다는 건 사용자가 그 명령을
쳤을 때 원하는 결과가 실제로 나온다는 뜻이다.

둘 다 필요하다. 넓게 보는 눈과 깊게 보는 눈이 다르다.

## 사용

    python scripts/run_excel_basicbench.py --model ax7b-planner-v3
    python scripts/run_excel_basicbench.py --model ax7b-planner-v5r --output-json ../logs/bench_v5r.json

Ollama가 떠 있어야 한다. 학습 중에는 GPU가 물려 있어 돌리지 말 것.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_agent import (
    parse_excel_live_command,
)
from office_claw_sidecar.services.llm_service import get_llm_service
from tests.excel_e2e.bench_cases import all_cases
from tests.excel_e2e.bench_core import (
    BenchCase,
    build_workbook,
    isolated_workspace,
    router_dispatcher,
)


def _digest_text(workbook_path: Path, sheet: str) -> str:
    """프로덕션과 같은 형식의 통합문서 상태 블록을 실제 파일에서 만든다."""
    from office_claw_sidecar.services.excel_live_service import get_excel_live_service
    from office_claw_sidecar.services.excel_workbook_digest import (
        build_workbook_digest,
        render_workbook_digest,
    )

    digest = build_workbook_digest(
        get_excel_live_service(),
        workbook_id=str(workbook_path),
        active_sheet_hint=sheet,
        use_cache=False,
    )
    return render_workbook_digest(digest)


async def _run_case(
    case: BenchCase, *, model: str, timeout: float
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "case_id": case.case_id,
        "category": case.category,
        "prompt": case.prompt,
        "passed": False,
        "planned_actions": [],
        "steps": 0,
        "elapsed_ms": 0,
        "detail": "",
        "error": "",
    }

    with isolated_workspace() as root:
        path = build_workbook(root, case)
        execute = router_dispatcher()
        started = time.perf_counter()
        try:
            context = {
                "workbook_id": str(path),
                "sheet_name": case.sheet,
                "context_range": None,
                "reasoning_mode": "deep",
                "complexity_score": 4,
                "planner_model": model,
                "workbook_digest_text": _digest_text(path, case.sheet),
            }
            parsed = await asyncio.wait_for(
                parse_excel_live_command(
                    case.prompt, llm_service=get_llm_service(), context=context
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 계획 실패도 결과다.
            outcome["error"] = f"plan: {exc}"
            outcome["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            return outcome

        outcome["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        plan = parsed.get("action_plan") or []
        outcome["planned_actions"] = [
            str(step.get("action") or "") for step in plan if isinstance(step, dict)
        ]
        outcome["steps"] = len(plan)

        try:
            for step in plan:
                if not isinstance(step, dict):
                    continue
                execute(
                    action=str(step.get("action") or ""),
                    params=dict(step.get("params") or {}),
                    workbook_id=str(path),
                    sheet_name=case.sheet,
                )
            execute(
                action="excel_live.save_workbook",
                params={},
                workbook_id=str(path),
                sheet_name=case.sheet,
            )
        except Exception as exc:  # noqa: BLE001 - 실행 실패도 결과다.
            outcome["error"] = f"execute: {exc}"
            return outcome

        result = case.expectation.check(path, case.sheet)
        outcome["passed"] = result.passed
        outcome["detail"] = result.detail
        return outcome


async def _main_async(args: argparse.Namespace) -> int:
    cases = all_cases()
    results = []
    for case in cases:
        result = await _run_case(case, model=args.model, timeout=args.timeout)
        mark = "PASS" if result["passed"] else "FAIL"
        note = result["error"] or result["detail"]
        print(f"  {mark}  {case.case_id:<28} {note[:90]}")
        results.append(result)

    passed = sum(1 for r in results if r["passed"])
    by_category: dict[str, list[int]] = {}
    for result in results:
        by_category.setdefault(result["category"], []).append(int(result["passed"]))

    report = {
        "model": args.model,
        "total": len(results),
        "passed": passed,
        "task_success_rate": round(passed / len(results), 4) if results else 0.0,
        "avg_steps": round(sum(r["steps"] for r in results) / len(results), 2)
        if results
        else 0.0,
        "avg_latency_ms": int(sum(r["elapsed_ms"] for r in results) / len(results))
        if results
        else 0,
        "by_category": {
            name: {"passed": sum(hits), "total": len(hits)}
            for name, hits in sorted(by_category.items())
        },
        "cases": results,
    }

    print()
    print(f"  Excel BasicBench v1 — {args.model}")
    print("  " + "─" * 44)
    for name, stat in report["by_category"].items():
        print(f"  {name:<12} {stat['passed']}/{stat['total']}")
    print("  " + "─" * 44)
    print(f"  Overall      {passed}/{len(results)} = {report['task_success_rate']:.1%}")
    print(f"  Avg steps    {report['avg_steps']}")
    print(f"  Avg latency  {report['avg_latency_ms']}ms")

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  저장: {out}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Excel 종단 벤치마크")
    parser.add_argument("--model", required=True, help="Ollama 플래너 모델명")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output-json", default="")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main_async(parse_args())))
