"""데모 워크북의 다이제스트(시트·열 문자·머리글)를 그대로 찍어 본다.

바인더가 고른 열이 맞는지 확인할 때 쓴다.

    uv run python scripts/probe_digest.py [시트명]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "복잡한 엑셀 작업을 위한 자료" / "AI_Excel_Automation_Demo.xlsx"
)


def main() -> int:
    os.environ.setdefault("EXCEL_LIVE_ENGINE", "file")
    from office_claw_sidecar.services.excel_live_service import get_excel_live_service
    from office_claw_sidecar.services.excel_workbook_digest import build_workbook_digest

    workdir = Path(tempfile.mkdtemp(prefix="officeclaw_digest_"))
    target = workdir / "demo.xlsx"
    shutil.copy2(TEMPLATE, target)
    os.environ["EXCEL_LIVE_PANDAS_ROOT"] = str(workdir)

    sheet = sys.argv[1] if len(sys.argv) > 1 else None
    digest = build_workbook_digest(
        get_excel_live_service(), workbook_id=str(target), active_sheet_hint=sheet, use_cache=False
    )
    for entry in digest.get("sheets") or []:
        print(f"[{entry.get('name')}] used={entry.get('used_range')}")
        for col in entry.get("columns") or []:
            print(f"  {col.get('letter')}: {col.get('header')} numeric={col.get('numeric')}")
    print(json.dumps({"active": digest.get("active_sheet")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
