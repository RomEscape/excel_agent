"""각 편집 동작이 Excel이 열 수 있는 파일을 남기는지 하나씩 확인한다.

openpyxl로 다시 저장하면 원본에 있던 차트·피벗 같은 요소가 깨질 수 있다.
깨진 파일은 우리 코드로는 계속 읽히지만 Excel에서는 열리지 않아,
PDF 내보내기처럼 Excel을 거치는 기능에서만 뒤늦게 드러난다.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService


def excel_can_open(path: Path) -> str:
    import win32com.client  # type: ignore[import-not-found]

    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        book = excel.Workbooks.Open(str(path))
        book.Close(SaveChanges=False)
        return "열림"
    except Exception as exc:
        return f"열기 실패: {str(exc)[:120]}"
    finally:
        excel.Quit()


def main() -> int:
    source = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).open("w", encoding="utf-8")
    svc = FileExcelLiveService()

    operations: list[tuple[str, callable]] = [
        ("변경 없음", lambda p: None),
        ("read_computed_range", lambda p: svc.read_computed_range(str(p), "Sales_Data", "A1:C3")),
        ("fill_range", lambda p: svc.fill_range(str(p), "Sales_Data", "A1:A5", "#FFFF00")),
        (
            "highlight_by_condition",
            lambda p: svc.highlight_by_condition(str(p), "Sales_Data", "L2:L181", "<", 1000000.0, "#FF0000"),
        ),
        ("sort_range", lambda p: svc.sort_range(str(p), "Sales_Data", "A1:O181", 2, True, True)),
        ("write_range", lambda p: svc.write_range(str(p), "Sales_Data", "R1", [["테스트"]])),
        # 표 머리글 덮어쓰기 — Excel이 파일을 아예 못 열게 만들던 경로.
        ("write_range 머리글 덮어쓰기", lambda p: svc.write_range(str(p), "Sales_Data", "A1", [["바뀐머리글"]])),
        ("create_sheet", lambda p: svc.create_sheet(str(p), "새시트")),
        (
            "create_sheet 긴이름",
            lambda p: svc.create_sheet(str(p), "지역별로 매출이 얼마인지 뽑아서 새 시트에"),
        ),
        (
            "create_sheet+write_range",
            lambda p: (
                svc.create_sheet(str(p), "새시트"),
                svc.write_range(str(p), "새시트", "A1", [["지역", "매출"], ["서울", 1]]),
            ),
        ),
        ("set_formula", lambda p: svc.set_formula(str(p), "Sales_Data", "R2", "=1+1")),
        (
            "pivot_table",
            lambda p: svc.pivot_table(
                str(p),
                "Sales_Data",
                "A1:O181",
                row_field="Region",
                value_field="Sales",
                agg="sum",
                output_sheet="Pivot_Check",
            ),
        ),
        (
            "create_chart",
            lambda p: svc.create_chart(
                str(p),
                "Sales_Data",
                "A1:B10",
                chart_type="bar",
                title="확인용",
            ),
        ),
    ]

    # 누적 모드: 한 파일에 순서대로 쌓는다. 개별로는 멀쩡한데 여러 번
    # 다시 저장하면 깨지는 경우를 잡는다.
    cumulative = "--cumulative" in sys.argv
    if cumulative:
        target = source.with_name("_check_cumulative.xlsx")
        shutil.copy2(source, target)
        for label, operation in operations:
            try:
                operation(target)
                verdict = excel_can_open(target)
            except Exception as exc:
                verdict = f"동작 자체 실패: {str(exc)[:160]}"
            out.write(f"{label}까지 누적: {verdict}\n")
            out.flush()
        target.unlink(missing_ok=True)
        out.close()
        return 0

    for label, operation in operations:
        target = source.with_name(f"_check_{label}.xlsx")
        shutil.copy2(source, target)
        try:
            operation(target)
            verdict = excel_can_open(target)
        except Exception as exc:
            verdict = f"동작 자체 실패: {str(exc)[:160]}"
        out.write(f"{label}: {verdict}\n")
        out.flush()
        target.unlink(missing_ok=True)
    out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
