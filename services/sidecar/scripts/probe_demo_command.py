"""데모 워크북(AI_Excel_Automation_Demo.xlsx) 사본에 명령 한 줄을 실행해 결과를 그대로 찍는다.

복합 시나리오 러너는 30개를 다 돌려야 해서 한 문장을 고칠 때 쓰기엔 느리다.
이 스크립트는 같은 라우터를 같은 시드로 1회만 호출한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from office_claw_sidecar.routers.excel_live import ExcelLiveCommandRequest, post_command
from office_claw_sidecar.services.llm_service import (
    get_llm_service,
    load_llm_config,
    reload_llm_service,
    save_llm_config,
)

TEMPLATE = (
    Path(__file__).resolve().parents[3] / "복잡한 엑셀 작업을 위한 자료" / "AI_Excel_Automation_Demo.xlsx"
)


async def _run(args: argparse.Namespace) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="officeclaw_demo_probe_"))
    workbook = tmp / "demo.xlsx"
    shutil.copy2(TEMPLATE, workbook)

    llm = get_llm_service()
    session = "demo-probe"
    for message in args.message:
        req = ExcelLiveCommandRequest(
            message=message,
            workbook_id=str(workbook),
            sheet_name=args.sheet,
            session_id=session,
            approve=True,
        )
        response = await post_command(req=req, llm=llm)
        result = response.result if isinstance(response.result, dict) else {}
        print(
            json.dumps(
                {
                    "message": message,
                    "action": response.action,
                    "ok": response.ok,
                    "reason": response.reason,
                    "ops": [
                        {
                            "action": op.get("action"),
                            "sheet": op.get("sheet_name"),
                            "range": op.get("target_range"),
                            "params": op.get("params"),
                            "result": op.get("result"),
                            "error": op.get("error"),
                        }
                        for op in (result.get("xlwings_ops") or [])
                    ],
                    "plan": [
                        {
                            "action": step.get("action"),
                            "verified": step.get("verified"),
                            "error": step.get("error"),
                            "verify_detail": step.get("verify_detail"),
                        }
                        for step in (result.get("plan") or [])
                    ],
                    "failure_detail": result.get("failure_detail"),
                    "planned": [
                        {"action": s.get("action"), "params": s.get("params")}
                        for s in (result.get("planned_steps") or result.get("steps") or [])
                    ],
                    "binder_notes": result.get("binder_notes"),
                    "executed_steps": result.get("executed_steps"),
                    "param_bindings": result.get("param_bindings"),
                    "result_keys": sorted(result.keys()),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    print(f"WORKBOOK={workbook}")


def main() -> None:
    parser = argparse.ArgumentParser(description="데모 워크북 단일 명령 프로브")
    parser.add_argument("message", nargs="+")
    parser.add_argument("--sheet", default="Sales_Data")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    original = load_llm_config()
    try:
        if str(args.model or "").strip():
            save_llm_config({"provider": "ollama", "model": args.model.strip()})
            reload_llm_service()
        asyncio.run(_run(args))
    finally:
        save_llm_config(original)
        reload_llm_service()


if __name__ == "__main__":
    main()
