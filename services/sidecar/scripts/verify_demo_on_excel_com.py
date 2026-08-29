"""시연 대본을 **실제 시연 조건**(엑셀을 띄워 놓은 상태)에서 검증한다.

`verify_excel_complex_scenarios.py`는 워크북을 파일로만 만들고 Excel에 띄우지 않는다.
그래서 `EXCEL_LIVE_ENGINE=auto`가 file 엔진(openpyxl)을 고르고, 정작 사용자가 시연할
때 도는 xlwings/COM 경로는 한 번도 안 지난다. 두 엔진은 서로 다른 파일의 다른 구현이라
한쪽이 통과했다고 다른 쪽을 보증하지 못한다.

이 스크립트는 같은 시나리오 팩을 그대로 쓰되, 턴을 돌리기 전에 워크북을 Excel에
연다. 즉 사용자가 엑셀을 켜 놓고 명령을 치는 흐름과 같은 조건이다.

사용:
    uv run python scripts/verify_demo_on_excel_com.py \
        --scenario-pack ../../datasets/excel_demo_scenarios_v1.json \
        --scenario-id demo-monthly-report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# COM 경로를 강제한다. 이 스크립트의 존재 이유가 그 경로를 재는 것이므로
# auto가 file로 흘러가면 의미가 없다. 서비스 임포트보다 먼저 세워야 한다.
os.environ["EXCEL_LIVE_ENGINE"] = "xlwings"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_excel_complex_scenarios import (
    _build_seed_workbook,
    _check_assertion,
    _load_pack,
    _run_turn,
)

from office_claw_sidecar.services.llm_service import get_llm_service


def _open_in_excel(path: Path):
    """워크북을 Excel에 띄우고 (app, book)을 돌려준다."""
    import xlwings as xw

    app = xw.App(visible=True, add_book=False)
    app.display_alerts = False
    book = app.books.open(str(path))
    return app, book


def _shutdown_excel(app, book) -> None:
    """Excel을 확실히 내린다.

    서비스가 COM 참조를 들고 있으면 `quit()`만으로는 프로세스가 안 죽는다. 실제로
    3회 연속 실행에서 EXCEL.EXE 7개가 좀비로 남았고, 남은 인스턴스가 다음 실행의
    워크북 탐색을 방해했다. 그래서 서비스 싱글톤을 먼저 버리고 kill까지 간다.
    """
    import office_claw_sidecar.services.excel_live_service as svc

    try:
        book.close()
    except Exception:
        pass
    svc._excel_live_service = None
    svc._excel_live_service_engine = None
    try:
        app.quit()
    except Exception:
        pass
    try:
        app.kill()
    except Exception:
        pass


async def _run_scenario_on_com(
    *, scenario: dict[str, Any], defaults: dict[str, Any], root: Path, turn_timeout: float
) -> dict[str, Any]:
    from openpyxl import load_workbook

    scenario_id = str(scenario.get("id", "unknown"))
    sheet_name = str(scenario.get("sheet_name") or defaults.get("sheet_name") or "매출")
    approve = bool(scenario.get("approve", defaults.get("approve", True)))
    seed_profile = str(scenario.get("seed_profile") or defaults.get("seed_profile") or "sales_core")

    # 절대경로여야 한다. `_find_workbook`은 후보 문자열을 워크북의 fullname(절대경로)과
    # 문자열로 비교하므로, 상대경로를 넘기면 열려 있는데도 "찾을 수 없습니다"가 난다.
    scenario_root = (root / scenario_id).resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    workbook_path = scenario_root / "scenario.xlsx"
    _build_seed_workbook(workbook_path, seed_profile)

    llm = get_llm_service()
    session_id = f"com-demo-{uuid.uuid4().hex[:8]}"
    turn_results: list[dict[str, Any]] = []

    app, book = _open_in_excel(workbook_path)
    try:
        for turn in scenario.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            result = await _run_turn(
                llm=llm,
                message=str(turn.get("message", "")),
                workbook_id=str(workbook_path),
                sheet_name=sheet_name,
                session_id=session_id,
                approve=approve,
                timeout_seconds=turn_timeout,
            )
            turn_results.append(result)
            print(
                f"  {result['message'][:40]:<42} -> {str(result['action'])[:28]:<30} "
                f"ok={result['ok']} {result['elapsed_ms']}ms",
                flush=True,
            )
            if result.get("reason"):
                print(f"      {str(result['reason'])[:160]}", flush=True)
        book.save()
    finally:
        _shutdown_excel(app, book)

    oracle = scenario.get("oracle") if isinstance(scenario.get("oracle"), dict) else {}
    result_oracle = oracle.get("result") if isinstance(oracle.get("result"), dict) else {}
    assertion_rows = result_oracle.get("assertions") if isinstance(result_oracle.get("assertions"), list) else []

    assertion_results: list[dict[str, Any]] = []
    wb = load_workbook(workbook_path, data_only=False)
    try:
        for assertion in assertion_rows:
            if not isinstance(assertion, dict):
                continue
            ok, detail = _check_assertion(wb, assertion, workbook_path)
            assertion_results.append({"ok": bool(ok), "detail": detail})
    finally:
        wb.close()

    passed = all(row["ok"] for row in assertion_results) and all(t["ok"] for t in turn_results)
    return {
        "id": scenario_id,
        "engine": "xlwings",
        "passed": passed,
        "workbook_path": str(workbook_path),
        "turns": turn_results,
        "assertion_results": assertion_results,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="시연 대본을 Excel COM 경로에서 검증")
    parser.add_argument(
        "--scenario-pack",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "datasets" / "excel_demo_scenarios_v1.json",
    )
    parser.add_argument("--scenario-id", type=str, default="")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "logs" / "demo_com_artifacts",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "logs" / "excel_demo_com_report.json",
    )
    parser.add_argument("--turn-timeout-seconds", type=float, default=90.0)
    args = parser.parse_args()

    pack = _load_pack(args.scenario_pack)
    defaults = pack.get("defaults") if isinstance(pack.get("defaults"), dict) else {}
    scenarios = [row for row in (pack.get("scenarios") or []) if isinstance(row, dict)]
    wanted = {p.strip() for p in str(args.scenario_id or "").split(",") if p.strip()}
    if wanted:
        scenarios = [row for row in scenarios if str(row.get("id", "")) in wanted]

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for idx, scenario in enumerate(scenarios, start=1):
        print(f"[{idx}/{len(scenarios)}] {scenario.get('id')} (engine=xlwings)", flush=True)
        row = await _run_scenario_on_com(
            scenario=scenario,
            defaults=defaults,
            root=args.artifact_dir,
            turn_timeout=float(args.turn_timeout_seconds),
        )
        for assertion in row["assertion_results"]:
            print(f"    assert {assertion['ok']}  {assertion['detail']}", flush=True)
        print(f"    => passed={row['passed']}", flush=True)
        results.append(row)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps({"engine": "xlwings", "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    passed = sum(1 for row in results if row["passed"])
    print(f"\n=== COM 경로 검증: {passed}/{len(results)} 통과 → {args.output_json}")


if __name__ == "__main__":
    asyncio.run(main())
