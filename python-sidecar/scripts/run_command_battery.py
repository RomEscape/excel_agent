"""실행 중인 사이드카에 실제 명령을 순서대로 던지고 결과를 표로 뽑는다.

chat_log.jsonl과 짝을 이뤄, 어떤 명령이 어디서 틀어지는지 실측하기 위한 진단 도구다.

두 가지 실행 경로를 같은 케이스 목록으로 돌린다.
- 기본: 닫힌 파일을 대상으로 한다. 사이드카는 file(openpyxl) 엔진을 고른다.
- --live: 대상 통합문서를 Excel에 실제로 열어 둔 채로 돌린다. 사용자의 실사용 흐름과
  같고, 이때 사이드카는 xlwings 엔진을 고른다. 열린 화면의 값이 실제로 바뀌었는지까지
  스냅샷으로 확인한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from openpyxl import Workbook

BASE = "http://127.0.0.1:19532"
HEADERS = {"Authorization": "Bearer dev-token"}

# (라벨, 메시지, 새 세션 시작 여부)
CASES: list[tuple[str, str, bool]] = [
    ("표-1턴", "표 만들어줘", True),
    ("표-2턴", "4열*4행", False),
    ("표-헤더만", "금액, 장소, 날짜 헤더로 표 만들어줘", True),
    ("값입력", "H3에 120 입력해줘", True),
    ("정렬-모호", "정렬해줘", True),
    ("정렬-명확", "금액 열 기준 내림차순으로 정렬해줘", True),
    ("필터", "금액이 300 이상인 행만 남겨줘", True),
    ("중복제거", "코드 열 기준으로 중복 제거해줘", True),
    ("수식", "H1에 금액 열 합계 수식 넣어줘", True),
    ("집계", "지역별 금액 합계를 집계해서 지역요약 시트에 만들어줘", True),
    ("조건서식", "금액이 300 미만인 건은 노란색으로 표시해줘", True),
    ("테두리", "A1:D1에 테두리 넣어줘", True),
    ("매크로", "매출 대시보드 만들어줘", True),
]


def build_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "매출"
    ws.append(["코드", "지역", "금액", "날짜"])
    rows = [
        ("A-001", "서울", 520, "2026-01-05"),
        ("A-002", "부산", 180, "2026-01-07"),
        ("A-003", "서울", 340, "2026-01-11"),
        ("A-002", "대구", 90, "2026-01-15"),
        ("A-004", "서울", 610, "2026-02-02"),
        ("A-005", "부산", 250, "2026-02-08"),
        ("A-006", "대구", 430, "2026-02-19"),
        ("A-003", "서울", 275, "2026-03-03"),
    ]
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def snapshot_live(book: Any) -> dict[str, Any]:
    """열린 통합문서의 눈에 보이는 상태를 찍는다. 명령 전후를 비교해 실제 반영을 확인한다."""
    state: dict[str, Any] = {"sheets": [sh.name for sh in book.sheets], "used": {}}
    for sheet in book.sheets:
        used = sheet.used_range
        state["used"][sheet.name] = {
            "address": getattr(used, "address", ""),
            "values": used.value,
        }
    return state


def send_command(message: str, workbook: str, session: str) -> tuple[int, dict[str, Any], str]:
    resp = requests.post(
        f"{BASE}/excel-live/command",
        headers=HEADERS,
        json={
            "message": message,
            "workbook_id": workbook,
            "sheet_name": "매출",
            "session_id": session,
            "approve": True,
        },
        timeout=180,
    )
    body = resp.json() if resp.status_code == 200 else {}
    detail = "" if resp.status_code == 200 else str(resp.text)[:160]
    return resp.status_code, body, detail


def run_case(
    label: str,
    message: str,
    workbook: Path,
    session: str,
    app: Any | None,
) -> dict[str, Any]:
    """한 케이스를 실행한다. app이 주어지면 Excel에 열어 둔 채로 돌리고 화면 변화를 검증한다."""
    row: dict[str, Any] = {"label": label, "message": message}
    t0 = time.time()
    book = None
    before = None
    try:
        if app is not None:
            book = app.books.open(str(workbook))
            before = snapshot_live(book)

        status, body, detail = send_command(message, str(workbook), session)
        result = body.get("result") or {}
        row.update(
            {
                "status": status,
                "ok": body.get("ok"),
                "action": body.get("action", ""),
                "ask": bool(result.get("ask_follow_up")),
                "reason": str(body.get("reason", ""))[:110],
                "detail": detail,
            }
        )

        if before is not None and book is not None:
            after = snapshot_live(book)
            row["changed"] = before != after
            row["new_sheets"] = [s for s in after["sheets"] if s not in before["sheets"]]
    except Exception as exc:  # noqa: BLE001 - 진단 도구라 어떤 실패든 표에 남겨야 한다
        row.update(
            {
                "status": 0,
                "ok": False,
                "action": "",
                "ask": False,
                "reason": f"{type(exc).__name__}: {exc}"[:110],
                "detail": "",
            }
        )
    finally:
        if book is not None:
            try:
                book.close()
            except Exception as exc:  # noqa: BLE001 - 정리 실패가 결과를 가리면 안 된다
                print(f"[정리] 통합문서 닫기 실패: {exc}")
    row["ms"] = int((time.time() - t0) * 1000)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="대상 통합문서를 Excel에 열어 둔 채로 실행한다(xlwings 경로).",
    )
    args = parser.parse_args()

    app = None
    if args.live:
        import xlwings as xw

        app = xw.App(visible=True, add_book=False)
        app.display_alerts = False
        app.screen_updating = True

    root = Path(tempfile.mkdtemp(prefix="officeclaw_battery_"))
    template = root / "battery_template.xlsx"
    build_workbook(template)

    results = []
    session = ""
    workbook = template
    try:
        for label, message, new_session in CASES:
            if new_session:
                session = f"battery-{uuid.uuid4().hex[:8]}"
                workbook = root / f"{label}.xlsx"
                shutil.copyfile(template, workbook)
            results.append(run_case(label, message, workbook, session, app))
            print(json.dumps(results[-1], ensure_ascii=False))
    finally:
        if app is not None:
            try:
                app.quit()
            except Exception as exc:  # noqa: BLE001 - Excel 종료 실패가 결과를 가리면 안 된다
                print(f"[정리] Excel 종료 실패: {exc}")

    out = root / "battery_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RESULTS={out}")


if __name__ == "__main__":
    main()
