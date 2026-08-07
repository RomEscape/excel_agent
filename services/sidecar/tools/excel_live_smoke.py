#!/usr/bin/env python
"""라이브 Excel 연동 스모크 테스트 — 실기기에서 수동 실행한다.

왜 필요한가:
    excel_live_service는 실행 중인 Excel을 조종하므로 CI에서 검증할 수 없다.
    단위 테스트는 가짜 객체를 쓰기 때문에 "Windows에선 되는데 macOS에선 안 되는"
    부류의 결함을 절대 못 잡는다. 실제로 이 스크립트가 두 가지를 잡아냈다:

      1) apply_border 가 COM 전용 `.api.Borders(idx)` 를 써서 macOS에서 죽던 것
      2) 테두리 색이 macOS에선 0~65535 RGB 리스트인데 COM 정수를 넘기면
         **예외 없이 조용히 검정**이 되던 것

    그래서 플랫폼을 새로 지원하거나 excel_live_service를 고친 뒤에는 각 OS에서
    이 스크립트를 한 번 돌려야 한다.

실행:
    cd services/sidecar
    uv run python tools/excel_live_smoke.py

주의:
    - Excel이 설치돼 있어야 한다. 스크립트가 임시 통합문서를 새로 만들어 쓰고
      끝나면 저장 없이 닫는다 — 열어 둔 파일은 건드리지 않는다.
    - macOS 첫 실행 시 Excel 제어 권한 프롬프트가 뜬다. 허용해야 진행된다.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import xlwings as xw

from office_claw_sidecar.services.excel_live_service import ExcelLiveService

SAMPLE = [
    ["제품군", "매출", "지역"],
    ["클라우드", 3200, "서울"],
    ["보안", 1850, "부산"],
    ["백업", 1320, "서울"],
]


def main() -> int:
    results: list[tuple[bool, str, str]] = []

    def check(label: str, fn) -> None:
        try:
            fn()
            results.append((True, label, ""))
        except Exception as exc:  # noqa: BLE001 - 스모크 목적상 전부 잡아 보고한다
            results.append((False, label, f"{type(exc).__name__}: {exc}"))

    # macOS에서는 visible=False 가 동작하지 않는다 (xlwings 플랫폼 차이).
    app = xw.App(visible=True, add_book=False)
    wb = app.books.add()
    sht = wb.sheets[0]
    sht.range("A1").value = SAMPLE

    # save_workbook 검사가 저장 경로 없는 새 통합문서를 현재 디렉터리에 떨구지
    # 않도록 임시 경로에 먼저 저장해 둔다.
    tmp_dir = tempfile.mkdtemp(prefix="kimdaeri-smoke-")
    wb.save(str(Path(tmp_dir) / "smoke.xlsx"))

    svc = ExcelLiveService()
    # 다른 통합문서가 열려 있을 수 있으므로 이름으로 정확히 지목한다.
    wbid = next(b["workbook_id"] for b in svc.list_workbooks() if b["name"] == wb.name)
    sheet = sht.name

    try:
        check("read_range", lambda: svc.read_range(wbid, sheet, "A1:C4"))
        check("write_range", lambda: svc.write_range(wbid, sheet, "E1", [["x"]]))
        check("set_formula", lambda: svc.set_formula(wbid, sheet, "E2", "=SUM(B2:B4)"))
        check(
            "highlight_by_condition",
            lambda: svc.highlight_by_condition(wbid, sheet, "B2:B4", ">", 1500, "#FF0000"),
        )
        check(
            "apply_border",
            lambda: svc.apply_border(wbid, sheet, "A1:C4", "continuous", "thin", "#FF0000"),
        )
        check(
            "calculate_column_stat",
            lambda: svc.calculate_column_stat(wbid, sheet, "매출", "sum"),
        )
        check(
            "group_by_aggregate",
            lambda: svc.group_by_aggregate(wbid, sheet, "지역", "sum", "매출"),
        )
        check("sort_rows", lambda: svc.sort_rows(wbid, sheet, "매출", True))
        check("filter_rows", lambda: svc.filter_rows(wbid, sheet, "매출", ">", 1000))
        check("dedupe_rows", lambda: svc.dedupe_rows(wbid, sheet, None))
        check("add_column", lambda: svc.add_column(wbid, sheet, "비고", None))
        check("rename_column", lambda: svc.rename_column(wbid, sheet, "비고", "메모"))
        check("drop_column", lambda: svc.drop_column(wbid, sheet, "메모"))
        check("get_active_selection_ref", lambda: svc.get_active_selection_ref(wbid, sheet))

        # 색은 "적용됐다"가 아니라 "요청한 색이 맞게 들어갔다"까지 봐야 한다.
        # macOS는 스케일이 달라서 조용히 검정이 되는 함정이 있었다.
        check("테두리 색 정확도", lambda: _verify_border_color(svc, wbid, sheet, sht))

        # save_workbook 은 통합문서 이름(=workbook_id)을 바꾸므로 반드시 마지막에
        # 둔다. 앞에 두면 이후 조회가 전부 WorkbookNotFoundError 로 깨진다.
        check("save_workbook", lambda: svc.save_workbook(wbid))
    finally:
        try:
            wb.close()
            app.quit()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print()
    print("=" * 72)
    for ok, label, detail in results:
        print(f"[{'  OK  ' if ok else ' FAIL '}] {label}")
        if detail:
            print(f"         → {detail}")
    failed = [r for r in results if not r[0]]
    print("=" * 72)
    print(f"플랫폼 {sys.platform} — 성공 {len(results) - len(failed)} / 실패 {len(failed)}")
    return 1 if failed else 0


def _verify_border_color(svc, wbid, sheet, sht) -> None:
    """요청한 색이 실제로 칠해졌는지 되읽어 확인한다."""
    expectations = [("#FF0000", (255, 0, 0)), ("#0000FF", (0, 0, 255))]
    for hex_color, expected in expectations:
        svc.apply_border(wbid, sheet, "A1:C4", "continuous", "thin", hex_color)
        got = _read_top_border_rgb(sht.range("A1:C4"))
        if got is None:
            return  # 되읽기 경로가 없는 플랫폼이면 검증을 건너뛴다
        if tuple(got) != expected:
            raise AssertionError(f"{hex_color} 요청했는데 {got} 가 칠해졌습니다 (기대 {expected})")


def _read_top_border_rgb(rng):
    api = getattr(rng, "api", None)
    if api is None:
        return None
    if sys.platform == "darwin":
        from appscript import k

        return api.get_border(which_border=k.border_top).color.get()
    # Windows COM: BGR 정수 → (R,G,B)
    color = api.Borders(8).Color
    return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)


if __name__ == "__main__":
    raise SystemExit(main())
