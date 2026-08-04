"""같은 명령을 N회 반복 실행해 흔들림(flakiness)을 잡아낸다.

시나리오 러너는 한 번에 한 결과만 보여주기 때문에, 15회 중 1회 실패하는 문제를
재현하려면 같은 입력을 반복해서 돌려 실패한 회차의 계획을 그대로 봐야 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from office_claw_sidecar.routers.excel_live import ExcelLiveCommandRequest, post_command
from office_claw_sidecar.services.llm_service import get_llm_service

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "복잡한 엑셀 작업을 위한 자료" / "AI_Excel_Automation_Demo.xlsx"
)


async def _run(args: argparse.Namespace) -> None:
    llm = get_llm_service()
    failures = 0
    for attempt in range(1, args.repeat + 1):
        tmp = Path(tempfile.mkdtemp(prefix="officeclaw_repeat_"))
        workbook = tmp / "demo.xlsx"
        shutil.copy2(TEMPLATE, workbook)
        session = f"repeat-{attempt}"
        ok = True
        for message in args.message:
            response = await post_command(
                req=ExcelLiveCommandRequest(
                    message=message,
                    workbook_id=str(workbook),
                    sheet_name=args.sheet,
                    session_id=session,
                    approve=True,
                ),
                llm=llm,
            )
            result = response.result if isinstance(response.result, dict) else {}
            if not response.ok:
                ok = False
                failures += 1
                print(
                    json.dumps(
                        {
                            "attempt": attempt,
                            "message": message,
                            "action": response.action,
                            "failure_detail": result.get("failure_detail"),
                            "failed_step_index": result.get("failed_step_index"),
                            "planned_steps": result.get("planned_steps"),
                            "param_bindings": result.get("param_bindings"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        if ok:
            print(json.dumps({"attempt": attempt, "ok": True}, ensure_ascii=False), flush=True)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"FAILURES={failures}/{args.repeat}")


def main() -> None:
    parser = argparse.ArgumentParser(description="같은 명령 반복 실행 프로브")
    parser.add_argument("message", nargs="+")
    parser.add_argument("--sheet", default="Sales_Data")
    parser.add_argument("--repeat", type=int, default=10)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
