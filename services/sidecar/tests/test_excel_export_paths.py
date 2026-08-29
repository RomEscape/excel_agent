"""내보내기 경로 정규화.

플래너는 "dashboard_report.pdf"처럼 폴더 없는 이름을 곧잘 지어낸다. 그대로 두면
사이드카가 실행된 폴더에 파일이 떨어져 사용자는 "저장했다"는 답만 받고 파일을 찾지 못한다.
결과 파일은 항상 통합문서 옆에 둔다.
"""

from __future__ import annotations

from pathlib import Path

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService

WORKBOOK = Path("C:/작업/보고/책자.xlsx")


def test_relative_pdf_name_lands_next_to_the_workbook():
    target = FileExcelLiveService._pdf_target(WORKBOOK, "Dashboard", "dashboard_report.pdf")
    assert target == WORKBOOK.parent / "dashboard_report.pdf"


def test_relative_pdf_path_with_folders_keeps_only_the_name():
    target = FileExcelLiveService._pdf_target(WORKBOOK, "Dashboard", "out/reports/요약.pdf")
    assert target == WORKBOOK.parent / "요약.pdf"


def test_absolute_pdf_path_is_respected():
    wanted = Path("D:/내보내기/보고서.pdf")
    assert FileExcelLiveService._pdf_target(WORKBOOK, "Dashboard", str(wanted)) == wanted


def test_missing_pdf_path_uses_the_sheet_name():
    target = FileExcelLiveService._pdf_target(WORKBOOK, "Dashboard", None)
    assert target == WORKBOOK.with_name("책자_Dashboard.pdf")


def test_sheet_name_with_spaces_is_made_filename_safe():
    target = FileExcelLiveService._pdf_target(WORKBOOK, "월간 보고 / 요약", None)
    assert target == WORKBOOK.with_name("책자_월간_보고_요약.pdf")


def test_whole_workbook_export_uses_the_workbook_name():
    assert FileExcelLiveService._pdf_target(WORKBOOK, None, None) == WORKBOOK.with_suffix(".pdf")
