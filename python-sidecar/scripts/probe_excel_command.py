"""단일 Excel Live 명령을 시드 워크북에 실행하고 확정된 파라미터를 그대로 보여주는 진단 도구.

시나리오 검증기는 통과/실패만 알려주므로, 어떤 파라미터로 실행됐는지 보려면 이 스크립트를 쓴다.
사용법: uv run python scripts/probe_excel_command.py "매출 시트 A1:E9에서 ..." [--sheet 매출]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_excel_complex_scenarios import _build_seed_workbook  # noqa: E402

from office_claw_sidecar.routers import excel_live as router  # noqa: E402
from office_claw_sidecar.services.llm_service import (  # noqa: E402
    get_llm_service,
    load_llm_config,
    reload_llm_service,
    save_llm_config,
)

captured: list[dict[str, Any]] = []
_original_execute = router._execute_action


def _spy_execute(*, action: str, params: dict[str, Any], workbook_id, sheet_name):
    row: dict[str, Any] = {"action": action, "params": params, "sheet_name": sheet_name}
    captured.append(row)
    try:
        out = _original_execute(
            action=action, params=params, workbook_id=workbook_id, sheet_name=sheet_name
        )
        row["result"] = out
        return out
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        raise


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("messages", nargs="+")
    parser.add_argument("--sheet", default="매출")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    if args.model:
        cfg = load_llm_config()
        cfg["model"] = args.model
        save_llm_config(cfg)
        reload_llm_service()
    llm = get_llm_service()

    router._execute_action = _spy_execute
    root = Path(tempfile.mkdtemp(prefix="officeclaw_probe_"))
    workbook_path = root / "probe.xlsx"
    _build_seed_workbook(workbook_path, "sales_core")
    session_id = f"probe-{uuid.uuid4().hex[:8]}"

    for message in args.messages:
        captured.clear()
        response = await router.post_command(
            router.ExcelLiveCommandRequest(
                message=message,
                workbook_id=str(workbook_path),
                sheet_name=args.sheet,
                session_id=session_id,
                approve=True,
            ),
            llm=llm,
        )
        print(
            json.dumps(
                {
                    "message": message,
                    "action": response.action,
                    "ok": response.ok,
                    "reason": response.reason,
                    "executed": captured.copy(),
                    "result": response.result,
                },
                ensure_ascii=False,
                default=str,
            )
        )
    print(f"WORKBOOK={workbook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
