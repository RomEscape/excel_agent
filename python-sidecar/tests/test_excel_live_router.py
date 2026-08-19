"""Excel Live 라우터 통합 테스트."""

from __future__ import annotations

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router
from office_claw_sidecar.services.excel_live_service import AmbiguousWorkbookError

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


def _range_address(start_cell: str, rows: int, cols: int) -> str:
    """"B2"에서 3열 1행을 쓰면 "B2:D2"."""
    text = str(start_cell).strip().upper()
    letters = "".join(c for c in text if c.isalpha()) or "A"
    row = int("".join(c for c in text if c.isdigit()) or 1)
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    if rows <= 1 and cols <= 1:
        return f"{letters}{row}"
    end_index, end = index + max(cols, 1) - 1, ""
    while end_index > 0:
        end_index, rem = divmod(end_index - 1, 26)
        end = chr(65 + rem) + end
    return f"{letters}{row}:{end}{row + max(rows, 1) - 1}"


class _FakeExcelService:
    def __init__(self):
        self._selected = r"C:\work\sales.xlsx"
        self._active_sheet = "Sheet1"
        self._sheet_names = ["Sheet1", "Sheet2"]
        self._workbooks = [
            {
                "workbook_id": r"C:\work\sales.xlsx",
                "name": "sales.xlsx",
                "full_path": r"C:\work\sales.xlsx",
                "active_sheet": self._active_sheet,
            }
        ]
        self._last_snapshot = {"row_count": 5, "col_count": 5}
        self._last_formula = {}
        self._last_border = {}
        # 검증기가 쓴 값을 다시 읽어 확인하므로, 더블도 변경을 기억해야 한다.
        # 항상 같은 값을 돌려주면 멀쩡한 쓰기가 전부 불일치로 잡힌다.
        self._written: dict[str, list[list]] = {}

    def is_available(self):
        return True

    def list_workbooks(self):
        self._workbooks[0]["active_sheet"] = self._active_sheet
        return self._workbooks

    def select_workbook(self, workbook_id):
        self._selected = workbook_id
        return {"selected": True, "workbook_id": workbook_id}

    def list_sheets(self, workbook_id):
        return {
            "sheets": list(self._sheet_names),
            "count": len(self._sheet_names),
            "active_sheet": self._active_sheet,
        }

    def select_sheet(self, workbook_id, sheet_name):
        if sheet_name not in self._sheet_names:
            self._sheet_names.append(sheet_name)
        self._active_sheet = sheet_name
        self._workbooks[0]["active_sheet"] = self._active_sheet
        return {"selected": True, "sheet_name": sheet_name, "active_sheet": self._active_sheet}

    def create_sheet(self, workbook_id, sheet_name, make_active=True):
        created = sheet_name not in self._sheet_names
        if created:
            self._sheet_names.append(sheet_name)
        if make_active:
            self._active_sheet = sheet_name
            self._workbooks[0]["active_sheet"] = self._active_sheet
        return {"created": created, "sheet_name": sheet_name, "active_sheet": self._active_sheet}

    def rename_sheet(self, workbook_id, sheet_name, new_name):
        if sheet_name in self._sheet_names:
            idx = self._sheet_names.index(sheet_name)
            self._sheet_names[idx] = new_name
        if self._active_sheet == sheet_name:
            self._active_sheet = new_name
            self._workbooks[0]["active_sheet"] = self._active_sheet
        return {
            "renamed": True,
            "old_name": sheet_name,
            "sheet_name": new_name,
            "sheets": list(self._sheet_names),
            "active_sheet": self._active_sheet,
        }

    def delete_sheet(self, workbook_id, sheet_name):
        if sheet_name in self._sheet_names and len(self._sheet_names) > 1:
            self._sheet_names.remove(sheet_name)
        if self._active_sheet == sheet_name:
            self._active_sheet = self._sheet_names[0]
            self._workbooks[0]["active_sheet"] = self._active_sheet
        return {
            "deleted": True,
            "sheet_name": sheet_name,
            "sheets": list(self._sheet_names),
            "active_sheet": self._active_sheet,
        }

    def get_selected_workbook_id(self):
        return self._selected

    def get_active_selection_ref(self, workbook_id, sheet_name):
        return "B2:C3"

    def get_used_range_ref(self, workbook_id, sheet_name):
        return "A1:C8"

    def read_range(self, workbook_id, sheet_name, range_ref):
        values = self._written.get(str(range_ref).strip().upper(), [[1, 2]])
        return {
            "values": [list(row) for row in values],
            "address": range_ref,
            "row_count": len(values),
            "col_count": max((len(row) for row in values), default=0),
        }

    def get_range_snapshot(self, workbook_id, sheet_name, range_ref):
        return {
            "address": range_ref,
            "row_count": self._last_snapshot["row_count"],
            "col_count": self._last_snapshot["col_count"],
            "filled_cells": 4,
        }

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        # 실제 서비스는 기록한 범위 전체를 주소로 돌려준다. 더블이 좁거나 엉뚱한
        # 주소를 주면 검증기가 그 범위만 다시 읽어서, 없는 버그가 보이거나
        # 있는 버그가 가려진다.
        rows = len(values_2d or [])
        cols = max((len(row) for row in values_2d or []), default=0)
        address = _range_address(start_cell, rows, cols)
        self._written[address.strip().upper()] = [list(row) for row in values_2d or []]
        return {"written_cells": rows * cols, "address": address}

    def create_table(self, workbook_id, sheet_name, start_cell, rows, cols, with_border):
        self._last_snapshot = {"row_count": int(rows), "col_count": int(cols)}
        return {"created": True, "address": "B2:F6", "rows": rows, "cols": cols}

    def highlight_by_condition(
        self, workbook_id, sheet_name, target_range, operator, threshold, fill_color, compare_column=None, value=None
    ):
        self._last_highlight = {
            "sheet_name": sheet_name,
            "target_range": target_range,
            "operator": operator,
            "threshold": threshold,
            "value": value,
        }
        return {"matched_cells": 3, "changed_cells": 3, "scanned_cells": 3, "address": target_range}

    def fill_range(self, workbook_id, sheet_name, target_range, fill_color):
        return {"changed_cells": 12, "address": target_range}

    def clear_range(self, workbook_id, sheet_name, target_range):
        self._written[str(target_range).strip().upper()] = [[None]]
        return {"cleared_cells": 12, "address": target_range}

    def delete_charts(self, workbook_id, sheet_name):
        return {"deleted": 2, "no_change": False, "sheet": sheet_name or "Sheet1"}

    def apply_border(self, workbook_id, sheet_name, target_range, line_style, weight, color):
        self._last_border = {
            "workbook_id": workbook_id,
            "sheet_name": sheet_name,
            "target_range": target_range,
            "line_style": line_style,
            "weight": weight,
            "color": color,
        }
        return {"changed_cells": 4, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        self._last_formula = {
            "workbook_id": workbook_id,
            "sheet_name": sheet_name,
            "range_ref": range_ref,
            "formula_a1": formula_a1,
        }
        return {"formula_applied_cells": 5, "address": range_ref}

    def verify_formula_result(self, workbook_id, sheet_name, range_ref):
        return {
            "address": range_ref,
            "non_empty_cells": 8,
            "numeric_cells": 8,
            "sum": 24500.0,
            "average": 3062.5,
            "sample_values": [1200, 3500],
        }

    def sort_range(self, workbook_id, sheet_name, target_range, key_column, order, has_header):
        return {
            "sorted_rows": 12,
            "address": target_range,
            "key_column_index": 1,
            "order": order,
        }

    def filter_rows(
        self, workbook_id, sheet_name, target_range, column, operator, value, has_header, mode="keep"
    ):
        return {
            "filtered_rows": 4,
            "address": target_range,
            "column_index": 1,
            "operator": operator,
            "value": value,
            "mode": mode,
        }

    def dedupe_rows(self, workbook_id, sheet_name, target_range, key_columns, has_header):
        return {
            "removed_rows": 3,
            "remaining_rows": 7,
            "address": target_range,
            "key_columns": key_columns or [1],
        }

    def pivot_table(
        self,
        workbook_id,
        sheet_name,
        source_range,
        row_field,
        value_field,
        agg,
        column_field,
        output_sheet,
        output_start,
        has_header,
    ):
        self._last_pivot = {
            "source_sheet": sheet_name,
            "output_sheet": output_sheet,
            "source_range": source_range,
        }
        return {
            "created": True,
            "address": "A1:C5",
            "rows": 5,
            "cols": 3,
            "sheet_name": output_sheet or sheet_name,
        }

    def create_chart(self, workbook_id, sheet_name, source_range, chart_type, title, output_sheet):
        return {
            "created": True,
            "chart_name": "chart_1",
            "chart_type": chart_type,
            "source_address": source_range,
            "sheet_name": output_sheet or sheet_name,
        }

    def validate_data(self, workbook_id, sheet_name, target_range, checks, has_header, date_min, date_max):
        return {
            "address": target_range,
            "issues": [{"type": "empty", "count": 2, "samples": ["B3", "C7"]}],
            "total_issues": 2,
        }

    def protect_sheet(self, workbook_id, sheet_name, password, lock_formula_cells, unlock_range):
        return {
            "protected": True,
            "sheet_name": sheet_name,
            "lock_formula_cells": bool(lock_formula_cells),
            "unlock_range": unlock_range,
        }

    def set_data_validation(
        self,
        workbook_id,
        sheet_name,
        target_range,
        validation_type,
        source,
        minimum,
        maximum,
        allow_blank,
        show_error,
        error_message,
    ):
        return {
            "applied": True,
            "address": target_range,
            "validation_type": validation_type,
        }

    def save_workbook(self, workbook_id):
        return {
            "saved": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
        }

    def list_workbook_backups(self, workbook_id, limit=20):
        return {
            "workbook_id": workbook_id,
            "source_path": workbook_id,
            "backup_dir": r"C:\work\officeclaw_backups",
            "backups": [
                {
                    "backup_path": r"C:\work\officeclaw_backups\sales.command.20260707_120000.xlsx",
                    "backup_name": "sales.command.20260707_120000.xlsx",
                    "size_bytes": 4096,
                    "modified_at": "2026-07-07T12:00:00",
                }
            ][: max(1, int(limit))],
        }

    def restore_workbook_from_backup(self, workbook_id, backup_path=None):
        return {
            "restored": True,
            "workbook_id": workbook_id,
            "name": "sales.xlsx",
            "full_path": workbook_id,
            "restored_from_backup_path": backup_path
            or r"C:\work\officeclaw_backups\sales.command.20260707_120000.xlsx",
            "pre_restore_backup_path": r"C:\work\officeclaw_backups\sales.pre_restore.20260707_120100.xlsx",
            "active_sheet": "Sheet1",
        }

    def set_font(self, workbook_id, sheet_name, target_range, *, bold=None, name=None, size=None, color=None):
        return {"changed_cells": 4, "address": target_range, "bold": bold}

    def convert_to_excel_table(self, workbook_id, sheet_name, target_range, table_name="", has_header=True):
        return {
            "created": True,
            "address": target_range,
            "table_name": table_name or "Sheet1Table",
            "has_header": bool(has_header),
        }

    def apply_formula_cf(
        self, workbook_id, sheet_name, target_range, formula, fill_color="#FFC7CE", font_color="#9C0006"
    ):
        return {"applied": True, "address": target_range, "rule": "formula", "formula": formula}

    def apply_data_bar(self, workbook_id, sheet_name, target_range, color="#638EC6"):
        return {"applied": True, "address": target_range, "rule": "data_bar"}

    def apply_color_scale(
        self,
        workbook_id,
        sheet_name,
        target_range,
        min_color="#F8696B",
        mid_color="#FFEB84",
        max_color="#63BE7B",
    ):
        return {"applied": True, "address": target_range, "rule": "color_scale"}


def _one_fake():
    """테스트 하나가 처음부터 끝까지 같은 가짜 Excel을 보게 한다.

    호출마다 새 인스턴스를 만들면 실행이 기록한 값을 검증 단계에서 다시 읽을 수
    없어, 정상 동작까지 검증 실패로 잡힌다.
    """
    service = _FakeExcelService()
    return lambda: service


def test_excel_live_status(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.get("/excel-live/status", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["workbooks"]) == 1


def test_approval_is_not_offered_for_a_sheet_that_does_not_exist(monkeypatch):
    """플래너가 지어낸 시트로 승인 카드를 띄우면, 승인한 뒤에야 404로 죽는다."""
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.add_column",
                    "params": {"sheet_name": "학과운영비", "name": "담당자"},
                    "reason": "담당자 열 추가",
                }
            ],
            "reason": "학습 데이터 정답 플랜",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "지금 학과운영비 관련 엑셀 작업을 진행",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": False,
            "session_id": "sess-missing-sheet",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_required"] is False
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True
    assert "학과운영비" in body["reason"]
    assert "Sheet1" in body["reason"]


def test_approval_on_a_missing_sheet_answers_instead_of_404(monkeypatch):
    """승인까지 누른 사용자에게 404만 던지면 무엇이 잘못됐는지 알 수 없다."""
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    pending = excel_live_router._build_approval(
        "excel_live.add_column", {"sheet_name": "없는시트", "name": "담당자"}
    )
    excel_live_router._pending_approvals[pending.approval_id] = excel_live_router.PendingExcelApproval(
        action="excel_live.add_column",
        params={"sheet_name": "없는시트", "name": "담당자"},
        workbook_id=r"C:\work\sales.xlsx",
        sheet_name="없는시트",
        created_at=pending.created_at,
    )

    def _raise_missing(**_kwargs):
        raise excel_live_router.WorksheetNotFoundError("시트를 찾을 수 없습니다: 없는시트")

    monkeypatch.setattr(excel_live_router, "_execute_action", _raise_missing)

    resp = client.post(
        "/excel-live/approval",
        json={"approval_id": pending.approval_id, "approved": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "없는시트" in body["reason"]
    assert body["result"]["ask_follow_up"] is True


def test_excel_live_backups_list(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.get("/excel-live/backups?limit=5", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["workbook_id"] == r"C:\work\sales.xlsx"
    assert len(body["backups"]) == 1
    assert body["backups"][0]["backup_name"].endswith(".xlsx")


def test_excel_live_restore_last(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/restore-last",
        json={
            "workbook_id": r"C:\work\sales.xlsx",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.restore_last_backup"
    assert body["result"]["restored"] is True


def test_action_confirm_required_then_approval_execute(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    first = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.highlight_by_condition",
            "params": {
                "target_range": "A:A",
                "operator": ">=",
                "threshold": 50,
                "fill_color": "#FFFF00",
            },
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["approval_required"] is True
    approval_id = body["pending_approval"]["approval_id"]

    second = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    done = second.json()
    assert done["ok"] is True
    assert done["result"]["changed_cells"] == 3


def test_action_without_workbook_id_uses_first_open_workbook(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.write_range",
            "params": {"start_cell": "B2", "values_2d": [["H1", "H2", "H3"]]},
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["written_cells"] == 3


def test_conditional_color_request_keeps_its_condition(monkeypatch):
    """"100만도 안 되는 건 빨갛게"는 조건을 잃고 통짜로 칠해지면 안 된다.

    2026-08-18에는 규칙이 조건을 표현하지 못해 **플래너를 거치는 것**이 유일한 보호였다.
    지금은 규칙이 `< 1000000`을 그대로 읽고 머리글('매출')로 열까지 좁힌다
    (2026-08-20). 그래서 이 테스트가 지키는 것은 경로가 아니라 **결과**다 —
    조건부 강조로, 조건이 살아 있고, 범위가 열 하나로 좁혀져 있어야 한다.
    """
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    calls: list[str] = []

    async def _plan_parse(message, llm_service, context):
        calls.append(message)
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {
                        "target_range": "L:L",
                        "operator": "<",
                        "threshold": 1000000,
                        "fill_color": "#FF0000",
                    },
                    "reason": "조건부 강조",
                }
            ],
            "reason": "highlight",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "매출 100만도 안 되는 건 빨갛게 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "excel_live.highlight_by_condition"
    applied = fake._last_highlight
    assert applied, "조건부 강조가 실행되지 않았다"
    assert applied["operator"] == "<", applied
    assert float(applied["threshold"]) == 1000000.0, applied
    # 통짜(A:Z·전체 선택)로 새면 조건이 있어도 엉뚱한 열이 칠해진다.
    assert applied["target_range"] not in {"A:Z", "__ACTIVE_SELECTION__", "__USED_RANGE__"}, applied


def test_command_rule_based_highlight(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.highlight_by_condition",
                    "params": {"target_range": "A:A", "operator": ">=", "threshold": 50, "fill_color": "#FFFF00"},
                    "reason": "조건부 강조",
                }
            ],
            "reason": "highlight",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "A열 데이터 중 50 이상인 셀을 노란색으로 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.highlight_by_condition"
    assert body["approval_required"] is True


def test_action_save_workbook_without_id_uses_selected(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.save_workbook",
            "params": {},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.save_workbook"
    assert body["result"]["saved"] is True


def test_action_apply_border_uses_active_selection_when_range_missing(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.apply_border",
            "params": {"target_range": "__ACTIVE_SELECTION__"},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"


def test_action_create_table_uses_active_cell_when_start_missing(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/action",
        json={
            "action": "excel_live.create_table",
            "params": {"rows": 5, "cols": 5},
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["rows"] == 5
    assert body["result"]["cols"] == 5


def test_command_rule_based_fill_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "표 색을 전반적으로 노랗게 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.fill_range"
    assert body["approval_required"] is True


def test_command_rule_based_list_sheets(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "현재 시트 목록 보여줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.list_sheets"
    assert body["ok"] is True
    assert body["result"]["sheets"] == ["Sheet1", "Sheet2"]


def test_command_rule_based_select_sheet(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "Sheet2 시트로 이동해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.select_sheet"
    assert body["ok"] is True
    assert body["result"]["active_sheet"] == "Sheet2"


def test_command_rule_based_create_sheet(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "요약 시트 만들어줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.create_sheet"
    assert body["ok"] is True
    assert body["result"]["sheet_name"] == "요약"


def test_command_rule_based_rename_sheet(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "Sheet1 시트 이름을 Dashboard로 바꿔줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.rename_sheet"
    assert body["ok"] is True
    assert body["result"]["sheet_name"] == "Dashboard"


def test_command_rule_based_delete_sheet(monkeypatch):
    fake = _FakeExcelService()
    fake._sheet_names = ["Sheet1", "임시"]
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "임시 시트 삭제해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.delete_sheet"
    assert body["ok"] is True
    assert "임시" not in body["result"]["sheets"]


def test_command_cross_sheet_link_formula(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "요약 시트 B2에 원본 시트 E2 값을 연결해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.set_formula"
    assert body["result"]["executed_steps"] == 2
    plan_actions = [step["action"] for step in body["result"]["plan"]]
    assert plan_actions == ["excel_live.select_sheet", "excel_live.set_formula"]
    assert fake._last_formula["sheet_name"] == "요약"
    assert fake._last_formula["range_ref"] == "B2"
    assert fake._last_formula["formula_a1"] == "='원본'!E2"


def test_quick_extract_colors_supports_white_korean_and_english():
    assert excel_live_router._quick_extract_colors("전체 셀을 흰색으로 바꿔줘") == ["#FFFFFF"]
    assert excel_live_router._quick_extract_colors("make all cells white") == ["#FFFFFF"]


def test_command_rule_based_fill_range_full_sheet_white_uses_used_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "전체 모든 셀의 색을 다 흰색으로 만들어줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-fill-full-white",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.fill_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_color_clear_with_border_reset_phrase_prefers_white_fill(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "다른 셀들이랑 동일하게 액셀 색을 없애고 모든 경계선을 기본값으로 돌려줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-color-clear-border-reset",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.fill_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_border_reset_phrase_uses_thin_gray_on_used_range(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "모든 경계선을 기본값으로 돌려줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-border-reset-default",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.apply_border"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"
    assert fake._last_border["target_range"] == "A1:C8"
    assert fake._last_border["line_style"] == "continuous"
    assert fake._last_border["weight"] == "thin"
    assert fake._last_border["color"] == "#000000"  # 2026-08-17 GUI 실측: 기본=회색은 흰 배경에서 안 보여 실패로 읽혔다. 기본은 Excel 모든 테두리와 같은 검정.


def test_command_rule_based_border_reset_phrase_handles_colloquial_boundary_word(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._recent_range_by_workbook.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "여기 경계들을 기본값으로 다 바꿔줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-border-reset-colloquial",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.apply_border"
    assert body["ok"] is True
    assert body["result"]["address"] == "B2:C3"
    assert fake._last_border["target_range"] == "B2:C3"
    assert fake._last_border["line_style"] == "continuous"
    assert fake._last_border["weight"] == "thin"
    assert fake._last_border["color"] == "#000000"  # 2026-08-17 GUI 실측: 기본=회색은 흰 배경에서 안 보여 실패로 읽혔다. 기본은 Excel 모든 테두리와 같은 검정.


def test_command_border_color_reset_phrase_prefers_apply_border_over_fill(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._recent_range_by_workbook.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "여기 경계선 색을 가장 기본 회식 얇은 색으로 바꿔줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-border-color-reset",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.apply_border"
    assert body["ok"] is True
    assert body["result"]["address"] == "B2:C3"
    assert fake._last_border["target_range"] == "B2:C3"
    assert fake._last_border["line_style"] == "continuous"
    assert fake._last_border["weight"] == "thin"
    assert fake._last_border["color"] == "#D9D9D9"


def test_command_rule_based_border_remove_phrase_maps_to_none_line_style(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "B2:C3 경계선 없애줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-border-remove",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.apply_border"
    assert body["ok"] is True
    assert body["result"]["address"] == "B2:C3"
    assert fake._last_border["line_style"] == "none"


def test_command_two_color_condition_executes_fill_then_highlight(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "A열 15 이상은 빨간색, 나머지는 노란색으로 칠해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.highlight_by_condition"
    assert body["result"]["executed_steps"] == 2
    plan_actions = [step["action"] for step in body["result"]["plan"]]
    assert plan_actions == ["excel_live.fill_range", "excel_live.highlight_by_condition"]


def test_detect_operation_intent_color_else_not_formula():
    intent = excel_live_router._detect_operation_intent("A열 15 이상은 빨간색 아니면 노란색으로 칠해줘")
    assert intent != "formula"


def test_detect_operation_intent_handles_paraphrases():
    assert excel_live_router._detect_operation_intent("수량하고 단가 곱해 금액 자동 산출되게 해줘") == "formula"
    assert excel_live_router._detect_operation_intent("금액 큰 순서대로 재배치해줘") == "sort"
    assert excel_live_router._detect_operation_intent("완료된 건만 남겨서 보여줘") == "filter"
    assert excel_live_router._detect_operation_intent("금액큰순서대로재배치해줘") == "sort"
    assert excel_live_router._detect_operation_intent("표를 보기 편하게 다듬어줘") == "general"
    assert excel_live_router._detect_operation_intent("시트 건들지 못하게 잠궈줘") == "protect"
    assert excel_live_router._detect_operation_intent("필터함수가안먹어") == "debug"
    assert excel_live_router._detect_operation_intent("수식 오류 좀 잡아줘") == "debug"


def test_detect_operation_intent_robust_phrase_dataset():
    cases = [
        ("수량하고 단가 곱해 금액 자동 산출되게 해줘", "formula"),
        ("코드 기준으로 가격 자동으로 찾아오게 해줘", "formula"),
        ("금액 큰 순서대로 재배치해줘", "sort"),
        ("날짜 기준으로 오름차순으로 줄세워줘", "sort"),
        ("완료된 건만 남겨서 보여줘", "filter"),
        ("상태가 지연인 행만 걸러줘", "filter"),
        ("겹친 값들 정리해서 중복 없애줘", "dedupe"),
        ("월별로 집계표 뽑아줘", "pivot"),
        ("월별 매출 그래프로 만들어줘", "chart"),
        ("추이 그래프 하나 그려줘", "chart"),
        ("이상한 값 있는지 진단해줘", "validate"),
        ("시트 건들지 못하게 잠궈줘", "protect"),
        ("파워쿼리 새로고침 돌려줘", "automation"),
        ("지난달이랑 이번달 차이 비교해줘", "compare"),
        ("다음달 매출 예측해줘", "forecast"),
        ("A4로 pdf 출력해줘", "print"),
        ("읽기전용이라편집안돼", "safety"),
        ("필터함수가안먹어", "debug"),
        ("수식 오류 좀 잡아줘", "debug"),
        ("느려서 버벅거려", "performance"),
        ("피벗이뭐야", "pivot"),
        ("표를 보기 편하게 다듬어줘", "general"),
    ]
    for message, expected in cases:
        assert excel_live_router._detect_operation_intent(message) == expected


def test_detect_operation_intent_group_aggregate_beats_trailing_verb():
    """ "-별 + 집계어"는 문장 끝 동사보다 세다. 같은 요청이 시트 생성·정렬로 새면 안 된다."""
    cases = [
        ("채널별 매출 합계를 Channel_Sum 시트에 만들어줘", "pivot"),
        ("영업담당자별 매출 합계를 Rep_Sum 시트에 정리해줘", "pivot"),
        ("제품군별 판매 건수 요약해줘", "pivot"),
        ("거래처별 평균 단가 뽑아줘", "pivot"),
    ]
    for message, expected in cases:
        assert excel_live_router._detect_operation_intent(message) == expected


def test_group_aggregate_needs_both_marker_and_aggregate_word():
    """둘 중 하나만 있으면 집계로 보지 않는다. 과잉 매칭이 다른 의도를 잡아먹으면 안 된다."""

    def fires(message: str) -> bool:
        lowered, compact = excel_live_router._normalized_message_views(message)
        return excel_live_router._looks_like_group_aggregate(lowered, compact)

    assert fires("채널별 매출 합계 내줘")
    # 묶는 기준만 있고 집계어가 없다.
    assert not fires("채널별 추이를 선 그래프로 그려줘")
    # 집계어만 있고 묶는 기준이 없다.
    assert not fires("매출 합계를 D열에 넣어줘")
    # "-별"로 끝나지만 묶는 기준이 아닌 낱말.
    assert not fires("특별히 중요한 건 합계를 따로 내줘")
    assert excel_live_router._detect_operation_intent("특별히 금액 큰 순서대로 정렬해줘") == "sort"


def test_pivot_never_aggregates_its_own_output_sheet(monkeypatch):
    """결과 시트를 원본으로 잡으면 방금 만든 빈 시트를 집계하다 실패한다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    excel_live_router._execute_action(
        action="excel_live.pivot_table",
        params={
            "source_sheet": "요약",
            "output_sheet": "요약",
            "row_field": "지역",
            "value_field": "매출",
        },
        workbook_id=r"C:\work\sales.xlsx",
        sheet_name="Sheet1",
    )

    assert fake._last_pivot["source_sheet"] == "Sheet1"
    assert fake._last_pivot["output_sheet"] == "요약"


def test_pivot_accepts_sheet_qualified_source_range(monkeypatch):
    """플래너가 'Sheet1!A1:C8'처럼 시트까지 붙여 말해도 읽을 수 있어야 한다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    excel_live_router._execute_action(
        action="excel_live.pivot_table",
        params={
            "source_range": "SHEET1!A1:C8",
            "output_sheet": "요약",
            "row_field": "지역",
            "value_field": "매출",
        },
        workbook_id=r"C:\work\sales.xlsx",
        sheet_name="Sheet2",
    )

    assert fake._last_pivot["source_sheet"] == "Sheet1"
    assert fake._last_pivot["source_range"] == "A1:C8"


def test_highlight_accepts_sheet_qualified_target_range(monkeypatch):
    """"진행률이 80% 미만인 업무" 처럼 대상이 다른 시트면 시트까지 붙어서 온다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    excel_live_router._execute_action(
        action="excel_live.highlight_by_condition",
        params={
            "target_range": "SHEET1!I2:I21",
            "operator": "<",
            "threshold": 0.8,
            "fill_color": "#FFFF00",
        },
        workbook_id=r"C:\work\sales.xlsx",
        sheet_name="Sheet2",
    )

    assert fake._last_highlight["sheet_name"] == "Sheet1"
    assert fake._last_highlight["target_range"] == "I2:I21"


def test_command_rule_based_clear_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "안에 내용 전부 지우고 깨끗하게 만들어줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    # "깨끗하게"는 2026-08-17부터 리셋 의미로 서식 제거 단계가 앞에 붙는다
    # (GUI 실측: 값만 지우면 서식만 남은 화면이 그대로라 "아무것도 안 됐다").
    # 여기서 지키는 것은 규칙 경로 + 승인 요구다.
    assert body["action"] in {
        "excel_live.apply_border",
        "excel_live.fill_range",
        "excel_live.clear_range",
    }
    assert body["approval_required"] is True


def test_command_rule_based_clear_range_full_reset_uses_used_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "엑셀을 전체 다 지워줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-clear-full-reset",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_clear_range_all_contents_phrase_uses_used_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "지금 엑셀 화면의 모든 내용 지워줄래?",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-clear-all-contents",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_clear_range_colloquial_cleanup_phrase_uses_used_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "지금 여기 엑셀을 깔끔하게 지워줄 수 있어?",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-clear-colloquial-cleanup",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_clear_range_restore_phrase_uses_used_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "지금 엑셀에 있는 내용을 다 지우고 원래 상태로 복구해줘 이름 수량 금액 21 22 23",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-clear-restore-phrase",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_rule_based_clear_range_restore_phrase_without_clear_verb(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()

    resp = client.post(
        "/excel-live/command",
        json={
            "message": "엑셀을 원래 상태로 복구해줘",
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
            "session_id": "sess-clear-restore-only",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clear_range"
    assert body["ok"] is True
    assert body["result"]["address"] == "A1:C8"


def test_command_sort_paraphrase_executes_without_fixed_phrase(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "금액 큰 순서대로 재배치해줘", "session_id": "sess-sort-paraphrase", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.sort_range"
    assert body["result"]["order"] == "desc"


def test_command_filter_paraphrase_executes_without_fixed_phrase(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "완료된 건만 남겨서 보여줘", "session_id": "sess-filter-paraphrase", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.filter_rows"
    assert body["result"]["operator"] == "=="
    assert body["result"]["value"] == "완료"


def test_command_protect_colloquial_phrase_prompts_or_executes_protect_flow(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "시트 건들지 못하게 잠궈줘", "session_id": "sess-protect-colloquial", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.protect", "excel_live.protect_sheet"}


def test_command_debug_filter_function_error_prefers_debug_intent(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "필터함수가안먹어", "session_id": "sess-debug-filter-fn", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.debug"
    assert body["result"]["ask_follow_up"] is True


def test_command_parse_failure_routes_to_chat_instead_of_list_fallback(monkeypatch):
    """파싱 실패는 목록 조회로 눙치지 않는다.

    2026-08-16에 전달 방식만 바꿨다: 400 → 200 + not_excel_request.
    Rust의 read_response(ipc.rs:29)가 4xx를 Err로 바꿔 버려서, 400으로는 프론트가
    "요청 실패"와 "엑셀 일이 아님"을 구분할 수 없었다. 구분이 돼야 일반 채팅으로
    넘겨 정상적으로 답할 수 있다. 금지 사항(list 폴백)은 그대로다.
    """
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭔가 이상한 명령", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.not_excel_request"
    assert body["result"]["route_to_chat"] is True
    # 원래 이 테스트가 막으려던 것 — 목록 조회로 새면 안 된다.
    assert body["action"] != "excel_live.list_workbooks"


def test_command_parse_failure_returns_clarify_for_excel_like_request(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "대시보드처럼 한눈에 정리해줘", "approve": False, "session_id": "clarify-test"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.chart"}
    assert body["result"]["ask_follow_up"] is True


def test_command_parse_timeout_returns_clarify_not_400(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _raise_timeout(_message, llm_service, context):
        raise TimeoutError()

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_timeout)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭔가 이상한 명령", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True
    assert body["result"]["parse_timeout"] is True


def test_command_parse_failure_problem_phrase_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "뭐가 문제인지 봐줘", "approve": False, "session_id": "clarify-test-2"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.validate_data"}


def test_command_empty_plan_excel_like_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _empty_plan(_message, llm_service, context):
        return {"action_plan": [], "reason": "불충분"}

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _empty_plan)

    resp = client.post(
        "/excel-live/command",
        json={"message": "보고용으로 정리해줘", "approve": False, "session_id": "clarify-test-3"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] in {"excel_live.clarify", "excel_live.general"}
    assert body["result"]["ask_follow_up"] is True


def test_command_general_followup_prefers_llm_then_fallback(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "보기 좋게 만들어줘", "session_id": "sess-general-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.general"
    assert body["result"]["ask_follow_up"] is True
    assert called["count"] == 1


def test_command_safety_followup_prefers_llm_then_fallback(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "파일이 읽기 전용이라 수정이 안 돼", "session_id": "sess-safety-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.safety"
    assert body["result"]["ask_follow_up"] is True
    assert called["count"] == 1


def test_command_validation_error_excel_like_returns_clarify(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _invalid_plan(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.write_range",
                    "params": {},
                    "reason": "잘못된 계획",
                }
            ],
            "reason": "invalid sort plan",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _invalid_plan)

    resp = client.post(
        "/excel-live/command",
        json={"message": "엑셀 alpha123", "approve": True, "session_id": "clarify-invalid-plan"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True


def test_ambiguous_workbook_asks_which_file_instead_of_erroring(monkeypatch):
    """대상 통합문서를 못 정하면 404가 아니라 후보를 들고 되묻는다."""

    class _AmbiguousService(_FakeExcelService):
        def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
            raise AmbiguousWorkbookError(
                "어떤 통합문서에 적용할까요?",
                candidates=["sales.xlsx", "inventory.xlsx"],
            )

    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _AmbiguousService())

    resp = client.post(
        "/excel-live/command",
        json={"message": "C3에 120 입력해줘", "approve": True, "session_id": "ambiguous-wb"},
        headers=HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True
    assert body["result"]["missing_slot"] == "workbook_id"
    assert body["result"]["candidates"] == ["sales.xlsx", "inventory.xlsx"]
    assert "sales.xlsx" in body["result"]["follow_up_question"]


def test_command_general_slot_upgrades_to_specific_intent(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    called = {"count": 0}

    async def _raise_parse(_message, llm_service, context):
        called["count"] += 1
        raise ValueError("LLM parse failed")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "보기 좋게 만들어줘", "session_id": "sess-upgrade-1", "approve": False},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["action"] == "excel_live.general"

    second = client.post(
        "/excel-live/command",
        json={"message": "매출 열 기준으로 내림차순", "session_id": "sess-upgrade-1", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.sort_range"
    # 첫 턴에서만 LLM 해석 시도, 이후는 pending 슬롯을 이어서 처리한다.
    assert called["count"] == 1


def test_command_executes_action_plan_sequentially(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {"action": "excel_live.read_range", "params": {"range_ref": "A1:B2"}, "reason": "현재 상태 확인"},
                {"action": "excel_live.read_range", "params": {"range_ref": "B2:C3"}, "reason": "후속 확인"},
            ],
            "reason": "2단계 계획 실행",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "alpha123", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.read_range"
    assert body["result"]["executed_steps"] == 2


def test_command_passes_context_range_to_parser(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        assert context["context_range"] == "C3:E9"
        return {
            "action_plan": [
                {"action": "excel_live.read_range", "params": {"range_ref": "C3:E9"}, "reason": "문맥 범위 사용"}
            ],
            "reason": "문맥 기반",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "beta123", "context_range": "C3:E9", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.read_range"


def test_command_uses_deep_reasoning_profile_for_complex_request(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    observed = {"modes": [], "score": 0}

    async def _plan_parse(_message, llm_service, context):
        observed["modes"].append(str(context.get("reasoning_mode", "")))
        observed["score"] = int(context.get("complexity_score", 0) or 0)
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.set_formula",
                    "params": {"range_ref": "D2:D20", "formula_a1": "=B2*C2"},
                    "reason": "복잡 계산식 적용",
                }
            ],
            "reason": "deep planning",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "여러 시트를 비교해서 수량과 단가 계산 수식까지 자동으로 만들어줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.set_formula"
    assert observed["modes"][0] == "deep"
    assert observed["score"] >= 3
    assert body["result"]["reasoning_profile"]["mode"] == "deep"


def test_command_runs_reflection_once_for_low_confidence_plan(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    seen_modes: list[str] = []

    async def _plan_parse(_message, llm_service, context):
        mode = str(context.get("reasoning_mode", "fast"))
        seen_modes.append(mode)
        if mode == "reflect":
            return {
                "intent": "edit",
                "action_plan": [
                    {
                        "action": "excel_live.set_formula",
                        "params": {"range_ref": "D2:D20", "formula_a1": "=B2*C2"},
                        "reason": "reflection corrected",
                    }
                ],
                "reason": "reflected",
            }
        return {
            "intent": "unknown",
            "action_plan": [
                {
                    "action": "excel_live.read_range",
                    "params": {"range_ref": "A1:C20"},
                    "reason": "저신뢰 계획",
                }
            ],
            "reason": "low confidence",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 단가 곱해서 금액 계산하고 결과 검증까지 해줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.set_formula"
    assert seen_modes == ["deep", "reflect"]
    assert body["result"]["reasoning_profile"]["reflection_attempted"] is True
    assert body["result"]["reasoning_profile"]["reflection_applied"] is True


def test_command_stabilizes_table_intent_when_llm_returns_invalid_write_range(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {"action": "excel_live.write_range", "params": {"start_cell": "__ACTIVE_CELL__"}, "reason": "표 생성"}
            ],
            "reason": "테이블 생성",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "5*5 표 만들어줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["rows"] == 5
    assert body["result"]["cols"] == 5


def test_command_applies_context_range_to_here_border(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "action_plan": [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": "__ACTIVE_SELECTION__"},
                    "reason": "문맥 범위 테두리",
                }
            ],
            "reason": "context",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "여기에 테두리 적용해줘", "context_range": "B2:D5", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"
    assert body["result"]["address"] == "B2:D5"


def test_command_executes_sort_range_action_plan(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.sort_range",
                    "params": {"target_range": "A1:E20", "key_column": "E", "order": "desc"},
                    "reason": "매출 내림차순 정렬",
                }
            ],
            "reason": "sort",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "매출 높은 순으로 보여줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.sort_range"
    assert body["result"]["order"] == "desc"


def test_command_executes_validate_data_action_plan(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "read",
            "action_plan": [
                {
                    "action": "excel_live.validate_data",
                    "params": {"target_range": "A1:E20", "checks": ["empty", "negative"]},
                    "reason": "데이터 오류 점검",
                }
            ],
            "reason": "validate",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "이상한 값 있는지 봐줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.validate_data"
    assert body["result"]["total_issues"] == 2


def test_command_create_chart_requires_confirm(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.create_chart",
                    "params": {"source_range": "A1:B12", "chart_type": "line", "title": "월별 매출"},
                    "reason": "차트 생성",
                }
            ],
            "reason": "chart",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "선 그래프로 만들어줘", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_required"] is True
    assert body["action"] == "excel_live.create_chart"


def test_command_create_chart_without_type_asks_first(monkeypatch):
    """차트 종류를 안 말했으면 기본값(선)으로 밀지 말고 물어본다."""
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.create_chart",
                    "params": {"source_range": "A1:B12", "chart_type": "line"},
                    "reason": "차트 생성",
                }
            ],
            "reason": "chart",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "그래프로 만들어줘", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ask_follow_up"] is True
    assert "차트 종류" in body["reason"]


def test_command_executes_verify_formula_result(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "read",
            "action_plan": [
                {
                    "action": "excel_live.verify_formula_result",
                    "params": {"range_ref": "D2:D20"},
                    "reason": "수식 결과 검증",
                }
            ],
            "reason": "formula verify",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "D2:D20 수식 값 확인해줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["numeric_cells"] == 8


def test_command_replans_once_when_execution_verify_fails(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {"action": "excel_live.fill_range", "params": {"target_range": "A:A"}, "reason": "1차 계획"}
            ],
            "reason": "first plan",
        }

    replanned_called = {"count": 0}

    async def _replan_parse(_message, llm_service, context, forbid_list_action=False, require_edit_action=False):
        replanned_called["count"] += 1
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": "B2:D5", "line_style": "continuous", "weight": "medium", "color": "#000000"},
                    "reason": "2차 계획",
                }
            ],
            "reason": "replanned",
        }

    verify_count = {"count": 0}

    def _verify_step_result(**kwargs):
        verify_count["count"] += 1
        # 첫 계획(fill_range)은 계속 실패, 재계획(apply_border)만 성공
        return kwargs.get("action") == "excel_live.apply_border"

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)
    monkeypatch.setattr(excel_live_router, "parse_command_plan_with_llm", _replan_parse)
    monkeypatch.setattr(excel_live_router, "_verify_step_result", _verify_step_result)

    resp = client.post(
        "/excel-live/command",
        json={"message": "전반적으로 색칠", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.apply_border"
    assert replanned_called["count"] == 1


def test_command_replan_formula_without_equal_is_normalized(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())

    async def _plan_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {"action": "excel_live.fill_range", "params": {"target_range": "A:A"}, "reason": "1차 계획"}
            ],
            "reason": "first plan",
        }

    async def _replan_parse(_message, llm_service, context, forbid_list_action=False, require_edit_action=False):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.set_formula",
                    "params": {"range_ref": "D2:D6", "formula_a1": "B2*C2"},
                    "reason": "2차 계획 수식",
                }
            ],
            "reason": "replanned formula",
        }

    def _verify_step_result(**kwargs):
        return kwargs.get("action") == "excel_live.set_formula"

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)
    monkeypatch.setattr(excel_live_router, "parse_command_plan_with_llm", _replan_parse)
    monkeypatch.setattr(excel_live_router, "_verify_step_result", _verify_step_result)

    resp = client.post(
        "/excel-live/command",
        json={"message": "금액 계산해줘", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.set_formula"
    assert body["result"]["formula_applied_cells"] >= 1


def test_command_create_table_multiturn_slot_fill_with_session(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "표 만들어줘",
            "session_id": "sess-table-1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["ok"] is True
    assert body1["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "5*5, 금액, 장소, 날짜, 요건, 비고",
            "session_id": "sess-table-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.write_range"
    assert body2["result"]["executed_steps"] == 2


def test_command_chart_slot_does_not_eat_later_fill(monkeypatch):
    """차트 종류를 묻던 세션에 색칠 명령이 오면 차트를 만들지 않는다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "그래프로 만들어줘", "session_id": "sess-slot-hijack-chart", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True
    assert "차트" in (body1["result"].get("follow_up_question") or "")

    second = client.post(
        "/excel-live/command",
        json={
            "message": "Inventory 시트에서 상태가 발주필요인 행을 노란색으로 칠해줘",
            "session_id": "sess-slot-hijack-chart",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["action"] != "excel_live.create_chart"
    assert body2["action"] in {
        "excel_live.fill_range",
        "excel_live.highlight_by_condition",
    }


def test_command_table_slot_does_not_eat_later_pivot(monkeypatch):
    """표 크기를 묻던 세션에 피벗 명령이 오면 표 질문을 반복하지 않는다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-slot-hijack-table", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "월별 매출 피벗 만들어줘",
            "session_id": "sess-slot-hijack-table",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["action"] != "excel_live.create_table"
    assert "표 크기" not in (body2.get("reason") or "")
    assert body2["action"] in {"excel_live.pivot_table", "excel_live.pivot"}


def test_command_chart_followup_still_accepts_bar(monkeypatch):
    """슬롯을 버릴 때 차트 종류 답변까지 새 명령으로 오인하면 안 된다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "그래프로 만들어줘", "session_id": "sess-chart-bar", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "막대 그래프", "session_id": "sess-chart-bar", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.create_chart"


def test_command_create_table_accepts_column_first_size(monkeypatch):
    """'4열*4행'처럼 열을 먼저 말해도 같은 질문으로 돌아오지 않고 표를 만든다."""
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-table-colfirst", "approve": False},
        headers=HEADERS,
    )
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "4열*4행", "session_id": "sess-table-colfirst", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["result"].get("ask_follow_up") is not True
    assert "sess-table-colfirst" not in excel_live_router._pending_create_table_slots


def test_command_create_table_stops_asking_and_uses_defaults(monkeypatch):
    """크기를 못 알아들어도 되묻기를 무한 반복하지 않고 기본값으로 만든다."""
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    def _post(message):
        return client.post(
            "/excel-live/command",
            json={"message": message, "session_id": "sess-table-loop", "approve": True},
            headers=HEADERS,
        ).json()

    assert _post("표 만들어줘")["result"]["ask_follow_up"] is True
    assert _post("음 잘 모르겠는데")["result"]["ask_follow_up"] is True

    final = _post("그냥 알아서 해줘")
    assert final["ok"] is True
    assert final["result"].get("ask_follow_up") is not True
    assert "기준으로 만들었습니다" in final["reason"]
    assert "sess-table-loop" not in excel_live_router._pending_create_table_slots


def test_command_table_vague_ignores_llm_1x1_guess_and_asks_follow_up(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _fake_parse(_message, llm_service, context):
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.create_table",
                    "params": {"start_cell": "A1", "rows": 1, "cols": 1},
                    "reason": "llm guessed tiny table",
                }
            ],
            "slot_fill": {"rows": 1, "cols": 1},
            "partial_params": {"rows": 1, "cols": 1},
            "reason": "guess",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _fake_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-table-vague", "approve": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.create_table"
    assert body["result"]["ask_follow_up"] is True


def test_command_table_intent_skips_llm_parse_for_faster_follow_up(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _must_not_run(_message, llm_service, context):
        raise AssertionError("LLM parse should be skipped for direct table intent")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _must_not_run)

    resp = client.post(
        "/excel-live/command",
        json={"message": "표 만들어줘", "session_id": "sess-table-fast", "approve": False},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["ask_follow_up"] is True


def test_command_create_table_slot_state_expires_by_ttl(monkeypatch):
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", _one_fake())
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("엑셀 명령을 해석하지 못했습니다.")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "표 만들어줘",
            "session_id": "sess-table-ttl",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    slot = excel_live_router._pending_create_table_slots.get("sess-table-ttl")
    assert slot is not None
    slot.updated_at_ts -= (excel_live_router._SLOT_TTL_SECONDS + 1)

    expired = client.post(
        "/excel-live/command",
        json={
            "message": "5*5, 금액, 장소",
            "session_id": "sess-table-ttl",
            "approve": True,
        },
        headers=HEADERS,
    )
    # 슬롯이 만료되면 "5*5, 금액, 장소"는 맥락 없는 문장이라 표를 만들지 않는다.
    # 2026-08-16에 전달 방식만 400 → 200 + not_excel_request로 바꿨다(ipc.rs가 4xx를
    # Err로 바꿔 프론트가 분기할 수 없었다). 표를 만들면 안 된다는 성질은 그대로다.
    assert expired.status_code == 200
    assert expired.json()["action"] == "excel_live.not_excel_request"


def test_command_template_follow_up_then_affirmative_executes(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={
            "message": "회의록 표 하나 만들어줘",
            "session_id": "sess-template-1",
            "approve": False,
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True
    assert "회의록" in body1["result"]["follow_up_question"]

    second = client.post(
        "/excel-live/command",
        json={
            "message": "응 그 정도면 돼",
            "session_id": "sess-template-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.write_range"
    assert body2["result"]["executed_steps"] == 2


def test_command_create_table_and_values_in_single_turn(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_create_table_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    msg = (
        "아래 내용으로 표 만들어줘\n"
        "날짜\t사용 목적\t금액\n"
        "26/02/24\t학기 초 회의\t320000\n"
        "26/03/09\t개강 회의\t200000"
    )
    resp = client.post(
        "/excel-live/command",
        json={
            "message": msg,
            "session_id": "sess-table-single-turn",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.write_range"
    assert body["result"]["executed_steps"] == 2
    plan_actions = [step["action"] for step in body["result"]["plan"]]
    assert plan_actions == ["excel_live.create_table", "excel_live.write_range"]
    assert body["result"]["plan"][0]["result"]["rows"] == 3
    assert body["result"]["plan"][0]["result"]["cols"] == 3


def test_command_sort_multiturn_followup_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "정렬해줘", "session_id": "sess-sort-1", "approve": False},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True
    assert "열 기준" in body1["result"]["follow_up_question"]

    second = client.post(
        "/excel-live/command",
        json={
            "message": "매출 열 기준으로 높은 순",
            "session_id": "sess-sort-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.sort_range"
    assert body2["result"]["order"] == "desc"


def test_command_filter_multiturn_followup_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "완료된 것만 따로 보고 싶어", "session_id": "sess-filter-1", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["ok"] is True
    assert body1["action"] == "excel_live.filter_rows"
    assert body1["result"]["filtered_rows"] == 4


def test_command_formula_multiturn_then_execute_and_verify(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-1", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "B열이 수량이고 C열이 단가야",
            "session_id": "sess-formula-1",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["ok"] is True
    assert body2["action"] == "excel_live.verify_formula_result"
    assert body2["result"]["numeric_cells"] == 8
    assert body2["result"]["executed_steps"] == 2


def test_command_formula_verify_zero_numeric_returns_follow_up(monkeypatch):
    class _ZeroFormulaFake(_FakeExcelService):
        def verify_formula_result(self, workbook_id, sheet_name, range_ref):
            return {
                "address": range_ref,
                "non_empty_cells": 4,
                "numeric_cells": 0,
                "sum": 0.0,
                "average": 0.0,
                "sample_values": ["N/A"],
            }

    fake = _ZeroFormulaFake()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-2", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "B열이 수량이고 C열이 단가야", "session_id": "sess-formula-2", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["ask_follow_up"] is True


def test_command_formula_countif_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "완료 건수 세어줘", "session_id": "sess-formula-countif", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    first_body = first.json()
    if first_body.get("result", {}).get("ask_follow_up"):
        second = client.post(
            "/excel-live/command",
            json={"message": "B열 상태에서 완료 개수", "session_id": "sess-formula-countif", "approve": True},
            headers=HEADERS,
        )
        assert second.status_code == 200
        body = second.json()
    else:
        body = first_body

    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    if "executed_steps" in body.get("result", {}):
        assert body["result"]["executed_steps"] == 2


def test_command_formula_auto_retry_once_when_numeric_zero(monkeypatch):
    class _RetryFormulaFake(_FakeExcelService):
        def __init__(self):
            super().__init__()
            self.formulas = []
            self.verify_calls = 0

        def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
            self.formulas.append(formula_a1)
            return {"formula_applied_cells": 5, "address": range_ref}

        def verify_formula_result(self, workbook_id, sheet_name, range_ref):
            self.verify_calls += 1
            if self.verify_calls == 1:
                return {
                    "address": range_ref,
                    "non_empty_cells": 4,
                    "numeric_cells": 0,
                    "sum": 0.0,
                    "average": 0.0,
                    "sample_values": ["ERR"],
                }
            return {
                "address": range_ref,
                "non_empty_cells": 4,
                "numeric_cells": 4,
                "sum": 100.0,
                "average": 25.0,
                "sample_values": [10, 20],
            }

    fake = _RetryFormulaFake()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "수량이랑 가격 곱해서 금액 나오게 해줘", "session_id": "sess-formula-retry", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={"message": "B열이 수량이고 C열이 단가야", "session_id": "sess-formula-retry", "approve": True},
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"
    assert body["result"]["auto_retry_applied"] is True
    assert len(fake.formulas) == 2
    assert fake.formulas[1].startswith("=IFERROR(")


def test_command_formula_vlookup_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "코드 기준으로 가격 찾아와", "session_id": "sess-formula-vlookup", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "조회값은 A열, 참조표는 F열부터 H열, 반환 2열",
            "session_id": "sess-formula-vlookup",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"


def test_command_formula_if_compare_multiturn_then_execute(monkeypatch):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    first = client.post(
        "/excel-live/command",
        json={"message": "점수 기준 조건식 넣어줘", "session_id": "sess-formula-if", "approve": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["result"]["ask_follow_up"] is True

    second = client.post(
        "/excel-live/command",
        json={
            "message": "C열이 70 미만이면 미달, 아니면 통과",
            "session_id": "sess-formula-if",
            "approve": True,
        },
        headers=HEADERS,
    )
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["action"] == "excel_live.verify_formula_result"


def _clarify_plan(question: str) -> dict:
    return {
        "intent": "clarify",
        "action_plan": [
            {
                "action": "excel_live.clarify",
                "params": {"question": question},
                "reason": "기준 열을 특정할 수 없음",
            }
        ],
        "reason": "되묻기",
        "follow_up_question": question,
    }


def test_command_planner_clarify_asks_instead_of_executing(monkeypatch):
    """플래너가 되묻기를 고르면 아무것도 실행하지 않고 질문만 돌려준다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_clarifications.clear()
    question = "'금액'과 '수량' 중 어느 열을 기준으로 정렬할까요?"

    async def _plan_parse(_message, llm_service, context):
        return _clarify_plan(question)

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    resp = client.post(
        "/excel-live/command",
        json={"message": "이거 정리해줘", "session_id": "sess-clarify", "approve": True},
        headers=HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "excel_live.clarify"
    assert body["result"]["ask_follow_up"] is True
    assert body["result"]["follow_up_question"] == question
    assert body["result"]["clarify_source"] == "planner"


def test_command_clarify_answer_turn_gets_previous_question(monkeypatch):
    """답변 턴 프롬프트에 원래 요청과 되물은 질문이 함께 들어간다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_clarifications.clear()
    question = "'금액'과 '수량' 중 어느 열을 기준으로 정렬할까요?"
    seen: dict[str, str] = {}

    async def _plan_parse(_message, llm_service, context):
        history = str(context.get("conversation_history_text") or "")
        if not history:
            return _clarify_plan(question)
        seen["history"] = history
        return {
            "intent": "edit",
            "action_plan": [
                {
                    "action": "excel_live.sort_range",
                    "params": {"target_range": "A1:E9", "key_column": "금액", "order": "desc"},
                    "reason": "답변대로 금액 기준 정렬",
                }
            ],
            "reason": "되묻기 답변 반영",
        }

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    client.post(
        "/excel-live/command",
        json={"message": "이거 정리해줘", "session_id": "sess-clarify-answer", "approve": True},
        headers=HEADERS,
    )
    second = client.post(
        "/excel-live/command",
        json={"message": "금액 기준으로", "session_id": "sess-clarify-answer", "approve": True},
        headers=HEADERS,
    )

    assert second.status_code == 200
    assert "이거 정리해줘" in seen["history"]
    assert question in seen["history"]
    # 답변을 받아 실행했으면 그 대화 줄기는 닫혀야 한다.
    assert "sess-clarify-answer" not in excel_live_router._pending_clarifications


def test_command_stops_clarifying_after_repeated_questions(monkeypatch):
    """되묻기가 반복되면 억제 플래그를 켜서 질문만 주고받는 상태를 끊는다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_clarifications.clear()
    observed: list[bool] = []

    async def _plan_parse(_message, llm_service, context):
        observed.append(bool(context.get("forbid_clarify")))
        return _clarify_plan("어느 열을 기준으로 할까요?")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _plan_parse)

    for _ in range(3):
        client.post(
            "/excel-live/command",
            json={"message": "정리해줘", "session_id": "sess-clarify-loop", "approve": True},
            headers=HEADERS,
        )

    assert observed[0] is False
    assert observed[1] is False
    assert observed[2] is True


def test_excel_example_md_core_routes_without_slot_bleed(monkeypatch):
    """엑셀 작업 예시.md 코어·멀티턴 첫 문장이 앞 세션 슬롯에 먹히지 않고 기대 액션 가족으로 간다."""
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)

    cases = [
        ("열린 통합문서 목록 보여줘", {"excel_live.list_workbooks"}),
        ("워크북 sales.xlsx 선택", {"excel_live.select_workbook"}),
        ("A1:C10 조회해줘", {"excel_live.read_range"}),
        ("B열 보여줘", {"excel_live.read_range"}),
        ("C3에 120 입력해줘", {"excel_live.write_range"}),
        ("B2:D2에 이름,수량,금액 입력", {"excel_live.write_range"}),
        ("A열에서 50 이상인 셀만 노란색 배경 적용", {"excel_live.highlight_by_condition"}),
        ("표 색을 전반적으로 노랗게 칠해줘", {"excel_live.fill_range"}),
        ("B2:D5 범위에 경계선 적용해줘", {"excel_live.apply_border"}),
        ("5 * 5 표를 하나 만들어줘", {"excel_live.create_table", "excel_live.write_range"}),
        ("매출 높은 순으로 정렬해줘", {"excel_live.sort_range", "excel_live.sort"}),
        ("완료된 것만 보고 싶어", {"excel_live.filter_rows"}),
        ("중복된 거 지워줘", {"excel_live.dedupe_rows", "excel_live.dedupe"}),
        ("부서별 비용 집계표 만들어줘", {"excel_live.pivot_table", "excel_live.pivot"}),
        ("월별 매출 그래프로 만들어줘", {"excel_live.create_chart", "excel_live.chart"}),
        ("C1에 B2:B20 합계 수식 넣어줘", {"excel_live.set_formula"}),
        ("D2:D50 수식 결과 값 확인해줘", {"excel_live.verify_formula_result"}),
        ("이상한 값 있는지 점검해줘", {"excel_live.validate_data"}),
        ("정렬해줘", {"excel_live.sort", "excel_live.sort_range"}),
        ("중복 없애줘", {"excel_live.dedupe_rows", "excel_live.dedupe"}),
        ("피벗으로 만들어줘", {"excel_live.pivot", "excel_live.pivot_table"}),
        ("그래프로 만들어줘", {"excel_live.chart", "excel_live.create_chart"}),
        ("엑셀을 전체 다 지워줘", {"excel_live.clear_range"}),
        ("안에 내용 전부 지우고 깨끗하게 만들어줘", {"excel_live.clear_range"}),
    ]
    for index, (message, allowed) in enumerate(cases):
        excel_live_router._pending_operation_slots.clear()
        excel_live_router._pending_create_table_slots.clear()
        excel_live_router._pending_clarifications.clear()
        resp = client.post(
            "/excel-live/command",
            json={
                "message": message,
                "session_id": f"example-md-{index}",
                "workbook_id": r"C:\work\sales.xlsx",
                "sheet_name": "Sheet1",
                "approve": True,
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200, (message, resp.status_code, resp.text[:200])
        action = resp.json().get("action")
        assert action in allowed, (message, action, allowed)


def _post_live_command(monkeypatch, message, *, session_id="demo-gap"):
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    async def _raise_parse(_message, llm_service, context):
        raise ValueError("LLM parse skipped")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _raise_parse)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._pending_create_table_slots.clear()
    excel_live_router._pending_clarifications.clear()
    return client.post(
        "/excel-live/command",
        json={
            "message": message,
            "session_id": session_id,
            "workbook_id": r"C:\work\sales.xlsx",
            "sheet_name": "Sheet1",
            "approve": True,
        },
        headers=HEADERS,
    )


def test_aggregate_plus_chart_does_not_ask_chart_kind(monkeypatch):
    """집계가 본 작업인데 차트 종류를 물으면 피벗까지 멈춘다 (23:12 chat_log)."""
    resp = _post_live_command(
        monkeypatch,
        "지역별 매출과 이익을 집계해서 새 시트와 차트를 만들어줘",
        session_id="agg-chart",
    )
    assert resp.status_code == 200
    body = resp.json()
    follow = str((body.get("result") or {}).get("follow_up_question") or body.get("reason") or "")
    assert "차트 종류" not in follow
    assert body.get("action") != "excel_live.chart"


def test_text_equals_highlight_is_not_fill(monkeypatch):
    resp = _post_live_command(
        monkeypatch,
        "Inventory 시트에서 상태가 발주필요인 행을 노란색으로 칠해줘",
        session_id="text-eq",
    )
    assert resp.status_code == 200
    assert resp.json().get("action") == "excel_live.highlight_by_condition"


def test_header_bold_uses_set_font(monkeypatch):
    resp = _post_live_command(monkeypatch, "머리글을 굵게 해줘", session_id="bold")
    assert resp.status_code == 200
    assert resp.json().get("action") == "excel_live.set_font"


def test_excel_table_convert_is_not_blank_grid_slot(monkeypatch):
    resp = _post_live_command(
        monkeypatch,
        "Sales_Data를 엑셀 표 테이블로 만들어줘",
        session_id="convert-table",
    )
    assert resp.status_code == 200
    body = resp.json()
    follow = str((body.get("result") or {}).get("follow_up_question") or "")
    assert "크기" not in follow
    assert body.get("action") == "excel_live.convert_to_excel_table"


def test_formula_cf_rule_beats_fill(monkeypatch):
    resp = _post_live_command(
        monkeypatch,
        "H열 발주필요면 빨간 조건부서식",
        session_id="formula-cf",
    )
    assert resp.status_code == 200
    assert resp.json().get("action") == "excel_live.apply_formula_cf"


def test_data_bar_is_not_write_range(monkeypatch):
    resp = _post_live_command(
        monkeypatch,
        "Sales_Data 시트 K2:K181에 데이터 막대 넣어줘",
        session_id="data-bar",
    )
    assert resp.status_code == 200
    assert resp.json().get("action") == "excel_live.apply_data_bar"


def test_color_scale_is_not_fill_range(monkeypatch):
    resp = _post_live_command(
        monkeypatch,
        "Sales_Data 시트 O2:O181에 색조 조건부서식 적용해줘",
        session_id="color-scale",
    )
    assert resp.status_code == 200
    assert resp.json().get("action") == "excel_live.apply_color_scale"



def test_selection_endpoint_is_fast_and_deterministic(monkeypatch):
    """붙여넣기 프로브 전용 경량 조회 — LLM·파이프라인을 타지 않는다.

    2026-08-17 실측: 프로브가 전체 명령 파이프라인을 타다 Ollama 혼잡·사이드카
    재시작 창과 겹쳐 통째로 실패했고, 붙여넣기가 조용히 죽었다.
    """
    fake = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)

    async def _no_llm(*args, **kwargs):
        raise AssertionError("선택 조회가 LLM을 불렀다")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _no_llm)
    resp = client.get("/excel-live/selection", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["address"], body
