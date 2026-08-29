r"""벤더 통합문서 가드 실물 실측 — 라운드 4-B1 판정용.

단위 테스트(가짜 COM)는 통과했지만, 감사 계획은 "실제 Excel + 데모 파일"을
요구한다. 이 스크립트는 **이미 떠 있는 Excel 인스턴스에 붙어서**(새로 안 띄움):

  1. 지금 활성인 사용자 통합문서로 `_resolve_workbook(None)`이 정상 해석되는지 (양성 대조)
  2. xlwings의 quickstart.xlsm(벤더 데모)을 읽기 전용으로 열어 활성화한 뒤
     같은 호출이 WorkbookNotFoundError로 **거부**되는지 (2026-08-04 사고 모양)
  3. 데모를 닫으면 다시 사용자 통합문서로 돌아오는지

를 잰다. 어떤 파일에도 쓰지 않는다(데모는 read_only, 사용자 파일은 손 안 댐).
Excel이 안 떠 있거나 사용자 통합문서가 없으면 측정 불가로 2를 돌려준다.

    & $PY scripts\smoke_vendor_workbook_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from office_claw_sidecar.services.excel_live_service import (
    ExcelLiveService,
    WorkbookNotFoundError,
    _is_user_workbook_path,
)

VENDOR = Path(__file__).resolve().parent.parent / ".venv/Lib/site-packages/xlwings/quickstart.xlsm"


def main() -> int:
    run_id = datetime.now().strftime("%m%d-%H%M%S-vendor-guard")
    service = ExcelLiveService()
    if not service.is_available():
        print("측정 불가: 실행 중인 Excel이 없다. Excel을 띄우고 다시 돌린다.")
        return 2
    if not VENDOR.exists():
        print(f"측정 불가: 데모 파일이 없다 — {VENDOR}")
        return 2

    app = service._app()
    before_active = str(getattr(app.books.active, "fullname", "") or "")
    if not _is_user_workbook_path(before_active):
        print(f"측정 불가: 지금 활성 통합문서가 이미 벤더 경로다 — {before_active}")
        return 2

    t0 = time.time()
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {"활성(시작)": before_active}

    # 1) 양성 대조 — 사용자 통합문서가 활성일 때는 폴백이 그 파일로 해석돼야 한다.
    resolved = service._resolve_workbook(None)
    checks["양성 대조: 사용자 파일로 해석"] = (
        str(getattr(resolved, "fullname", "")) == before_active
    )

    # 2) 데모 파일을 열어 활성화 — 폴백은 거부해야 한다(사고 모양 재현).
    demo = app.books.open(str(VENDOR), read_only=True)
    try:
        active_now = str(getattr(app.books.active, "fullname", "") or "")
        detail["활성(데모 연 뒤)"] = active_now
        checks["데모가 활성이 됨"] = active_now.lower() == str(VENDOR).lower()
        try:
            service._resolve_workbook(None)
            checks["가드: 벤더 활성 시 거부"] = False
            detail["가드"] = "예외 없이 통과해 버렸다"
        except WorkbookNotFoundError as exc:
            checks["가드: 벤더 활성 시 거부"] = "데모 파일" in str(exc)
            detail["가드"] = str(exc)
        # 명시적 지목은 가드를 우회해야 한다 — 사용자가 정말 원했다는 뜻이므로.
        explicit = service._resolve_workbook(str(VENDOR))
        checks["명시 지목은 우회"] = (
            str(getattr(explicit, "fullname", "")).lower() == str(VENDOR).lower()
        )
    finally:
        demo.close()

    # 3) 데모를 닫으면 원상복구 — 사용자 통합문서로 다시 해석돼야 한다.
    after = service._resolve_workbook(None)
    after_name = str(getattr(after, "fullname", ""))
    detail["활성(데모 닫은 뒤)"] = after_name
    checks["복귀: 다시 사용자 파일"] = after_name == before_active

    report = {
        "run_id": run_id,
        "checks": checks,
        "detail": detail,
        "초": round(time.time() - t0, 2),
    }
    out_dir = Path(__file__).resolve().parent.parent.parent / "logs" / "e2e"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, passed in checks.items():
        print(("✅" if passed else "❌"), name)
    print(f"run_id={run_id} · {report['초']}초")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
