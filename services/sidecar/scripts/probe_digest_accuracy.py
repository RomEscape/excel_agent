# -*- coding: utf-8 -*-
"""모델이 보는 **통합문서 상태**가 파일과 맞는지 잰다 — 관측층 정확도.

## 왜 필요한가

"AI 가 엑셀 작업 상황을 제대로 읽는가"는 두 층으로 갈린다.

    관측층: 파일 상태를 정확히 요약하는가  ← 이 스크립트가 재는 것
    활용층: 모델이 그 요약을 실제로 쓰는가

관측층이 틀리면 아래는 볼 필요가 없다. 그런데 `build_workbook_digest` 는 도크스트링에
"실패해도 명령 처리를 막지 않도록 **예외는 모두 삼키고** 부분 결과를 돌려준다"고
적혀 있다 — 그 패턴이 채점 기준을 조용히 멀게 만든 전례가 있다(2026-09-10: 삼키는
except 가 표 생성을 영영 못 보게 했다). 삼킨 자리는 사람이 재 봐야만 드러난다.

## 무엇을 재는가

사람이 실제로 만드는 상태를 만들고, 다이제스트의 각 항목을 **파일에서 직접 구한 정답**과
대조한다. 각본이 아니라 상태를 다룬다 — 붙여넣기로만 재던 코퍼스가 사람이 직접 타자한
상태를 한 번도 검증하지 못했던 것과 같은 실수를 피하려는 것이다(2026-09-08).

사용:
    python scripts/probe_digest_accuracy.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _console import force_utf8  # noqa: E402

force_utf8()

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import PatternFill  # noqa: E402
from openpyxl.worksheet.table import Table  # noqa: E402

from office_claw_sidecar.services.excel_live_file_service import FileExcelLiveService  # noqa: E402
from office_claw_sidecar.services.excel_workbook_digest import (  # noqa: E402
    build_workbook_digest,
    invalidate_workbook_digest,
    render_workbook_digest,
)

TABLE = [
    ["제품", "카테고리", "단가", "판매량", "매출", "재고"],
    ["노트북", "전자", 1200000, 45, 54000000, 12],
    ["마우스", "주변기기", 25000, 320, 8000000, 150],
    ["키보드", "주변기기", 55000, 210, 11550000, 80],
]


def _save(wb: Workbook, name: str) -> Path:
    path = Path(tempfile.mkdtemp()) / f"{name}.xlsx"
    wb.save(path)
    wb.close()
    return path


# ── 사람이 실제로 만드는 상태들 ────────────────────────────────────────────────


def state_empty() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    wb.active.title = "작업물"
    return _save(wb, "빈통합문서"), {"headers": [], "last_row": 0}


def state_header_only() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws.append(TABLE[0])
    return _save(wb, "머리글만"), {"headers": TABLE[0], "last_row": 1, "free_row": 2, "free_col": "G"}


def state_partial() -> tuple[Path, dict[str, Any]]:
    """가장 흔한 상태 — 머리글 + 일부 행만 채워 둔 것."""
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    for row in TABLE[:3]:
        ws.append(row)
    return _save(wb, "일부행"), {"headers": TABLE[0], "last_row": 3, "free_row": 4, "free_col": "G"}


def state_title_above() -> tuple[Path, dict[str, Any]]:
    """제목 줄이 위에 있고 머리글이 3행에서 시작하는 표 — 사람이 아주 흔히 만든다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws["A1"] = "2026년 3분기 판매 실적"
    ws.append([])
    for row in TABLE:
        ws.append(row)
    return _save(wb, "제목이위에"), {"headers": TABLE[0], "header_row": 3, "last_row": 7}


def state_gap_row() -> tuple[Path, dict[str, Any]]:
    """표 중간에 빈 행이 하나 있는 것."""
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws.append(TABLE[0])
    ws.append(TABLE[1])
    ws.append([])
    ws.append(TABLE[2])
    return _save(wb, "중간빈행"), {"headers": TABLE[0], "last_row": 4}


def state_numbers_as_text() -> tuple[Path, dict[str, Any]]:
    """숫자가 문자열로 들어간 열 — 사람이 붙여넣다 자주 만든다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws.append(TABLE[0])
    ws.append(["노트북", "전자", "1200000", "45", "54000000", "12"])
    return _save(wb, "숫자가글자"), {"headers": TABLE[0], "last_row": 2}


def state_formula_column() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws.append(TABLE[0] + ["검산"])
    for i, row in enumerate(TABLE[1:], start=2):
        ws.append(row + [f"=C{i}*D{i}"])
    return _save(wb, "수식열"), {"headers": TABLE[0] + ["검산"], "last_row": 4, "free_col": "H"}


def state_merged_title() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    ws["A1"] = "판매 실적"
    ws.merge_cells("A1:F1")
    for row in TABLE:
        ws.append(row)
    return _save(wb, "병합제목"), {"headers": TABLE[0], "header_row": 2, "last_row": 5}


def state_two_sheets() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "판매"
    for row in TABLE:
        ws.append(row)
    other = wb.create_sheet("재고현황")
    other.append(["제품", "창고재고"])
    other.append(["노트북", 12])
    return _save(wb, "두시트"), {"sheets": ["판매", "재고현황"], "headers": TABLE[0]}


def state_excel_table() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    for row in TABLE:
        ws.append(row)
    ws.add_table(Table(displayName="판매표", ref="A1:F4"))
    return _save(wb, "엑셀표"), {"headers": TABLE[0], "last_row": 4}


def state_colored() -> tuple[Path, dict[str, Any]]:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    for row in TABLE:
        ws.append(row)
    yellow = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")
    for col in range(1, 7):
        ws.cell(row=1, column=col).fill = yellow
    return _save(wb, "머리글색"), {"headers": TABLE[0], "last_row": 4}


def state_far_island() -> tuple[Path, dict[str, Any]]:
    """표 하나와, 멀리 떨어진 작은 메모 블록 — 사용범위가 통째로 늘어난다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    for row in TABLE:
        ws.append(row)
    ws["J20"] = "메모: 3분기 확정"
    return _save(wb, "멀리떨어진섬"), {"headers": TABLE[0], "last_row": 20}


STATES: list[tuple[str, Callable[[], tuple[Path, dict[str, Any]]]]] = [
    ("빈 통합문서", state_empty),
    ("머리글만", state_header_only),
    ("머리글+일부행(가장 흔함)", state_partial),
    ("제목 줄이 위에 있는 표", state_title_above),
    ("표 중간에 빈 행", state_gap_row),
    ("숫자가 글자로 들어간 열", state_numbers_as_text),
    ("수식 열이 있는 표", state_formula_column),
    ("병합된 제목 줄", state_merged_title),
    ("시트 두 장", state_two_sheets),
    ("엑셀 표(Table) 객체", state_excel_table),
    ("머리글에 배경색", state_colored),
    ("멀리 떨어진 메모 블록", state_far_island),
]


def probe() -> int:
    service = FileExcelLiveService()
    problems: list[str] = []

    for label, make in STATES:
        path, truth = make()
        invalidate_workbook_digest()
        service.select_workbook(str(path))
        digest = build_workbook_digest(service, workbook_id=str(path), use_cache=False)
        rendered = render_workbook_digest(digest)

        print("═" * 78)
        print(f"  {label}")
        print(f"  파일: {path.name}")
        print("  ── 모델이 보는 것 ──")
        for line in (rendered or "(비어 있음)").splitlines():
            print(f"    {line}")

        sheets = digest.get("sheets") or []
        names = [str(s.get("name") or "") for s in sheets]
        if truth.get("sheets") and names != truth["sheets"]:
            problems.append(f"{label}: 시트 목록 {names} ≠ {truth['sheets']}")

        first = sheets[0] if sheets else {}
        headers = [str(c.get("header") or "") for c in (first.get("columns") or [])]
        want = [h for h in (truth.get("headers") or [])]
        if want and headers != want:
            problems.append(f"{label}: 머리글 {headers} ≠ {want}")
        if not want and headers:
            problems.append(f"{label}: 빈 통합문서인데 머리글 {headers} 을 냈다")
        print(f"  ── 판정: 머리글 {'맞음' if (headers == want) else '어긋남'}")
        print()

    print("═" * 78)
    if problems:
        print(f"  어긋난 항목 {len(problems)}건:")
        for p in problems:
            print(f"   · {p}")
    else:
        print("  어긋난 항목 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(probe())
