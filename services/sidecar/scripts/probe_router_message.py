"""테스트와 동일한 FakeExcelService 위에서 한 메시지를 여러 번 돌려 흔들림을 확인한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_excel_live_50_commands import HEADERS, _FakeExcelService, client

from office_claw_sidecar.routers import excel_live as excel_live_router

excel_live_router.get_excel_live_service = lambda: _FakeExcelService()


def main() -> int:
    message = sys.argv[1]
    repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    for _ in range(repeat):
        excel_live_router._pending_operation_slots.clear()
        excel_live_router._pending_create_table_slots.clear()
        resp = client.post(
            "/excel-live/command",
            json={
                "message": message,
                "workbook_id": r"C:\work\sales.xlsx",
                "sheet_name": "Sheet1",
                "approve": False,
            },
            headers=HEADERS,
        )
        body = resp.json()
        print(
            json.dumps(
                {
                    "status": resp.status_code,
                    "action": body.get("action"),
                    "reason": str(body.get("reason", ""))[:80],
                    "result": {
                        k: v
                        for k, v in (body.get("result") or {}).items()
                        if k in {"validation_error", "reasoning_profile", "param_bindings"}
                    },
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
