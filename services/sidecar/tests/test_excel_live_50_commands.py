"""Excel Live 실사용 50개 명령 회귀 테스트.

목표:
  - 실제 사용자 문장에 가까운 명령 50개를 /excel-live/command로 순차 실행
  - SAFE/CONFIRM 흐름(승인 필요 작업 포함) 검증
  - 파서 회귀를 빠르게 감지
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


def _idx_to_col(index: int) -> str:
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters or "A"


def _parse_cell(ref: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d{1,7})", str(ref or "").strip())
    if not match:
        return None
    col = 0
    for ch in match.group(1).upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return col, int(match.group(2))


def _parse_range(ref: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    text = str(ref or "").strip()
    if "!" in text:
        text = text.rsplit("!", 1)[1]
    head, _, tail = text.partition(":")
    start = _parse_cell(head)
    if start is None:
        return None
    end = _parse_cell(tail) if tail else start
    if end is None:
        return None
    return (min(start[0], end[0]), min(start[1], end[1])), (
        max(start[0], end[0]),
        max(start[1], end[1]),
    )


class _FakeExcelService:
    """호출을 기록하는 가짜 Excel.

    액션 이름만 대조하면 "write_range로 분류했다"까지만 검증되고, 값이 어느 셀에
    어떤 모양으로 들어가는지는 통과 여부에 영향을 주지 않는다. 실제로 여러 셀 쓰기가
    한 칸에 뭉치는 버그가 이 테스트를 통과한 채 남아 있었다. 그래서 서비스가 받은
    인자를 그대로 남겨 두고, 시나리오의 expect와 대조한다.
    """

    def __init__(self):
        self.calls: list[dict] = []
        # 쓴 값을 기억한다. 검증기가 write_range 뒤에 같은 범위를 다시 읽으므로,
        # 고정값만 돌려주면 정상적으로 쓴 명령까지 "값 불일치"로 되돌려진다.
        self._cells: dict[tuple[str, str], object] = {}
        self._selected = r"C:\work\sales.xlsx"
        self._workbooks = [
            {
                "workbook_id": r"C:\work\sales.xlsx",
                "name": "sales.xlsx",
                "full_path": r"C:\work\sales.xlsx",
                "active_sheet": "Sheet1",
            },
            {
                "workbook_id": r"C:\work\inventory.xlsx",
                "name": "inventory.xlsx",
                "full_path": r"C:\work\inventory.xlsx",
                "active_sheet": "Main",
            },
        ]

    def _record(self, method, **payload):
        self.calls.append({"method": method, **payload})

    def last_call(self, method):
        for call in reversed(self.calls):
            if call["method"] == method:
                return call
        return None

    def is_available(self):
        return True

    def list_workbooks(self):
        return self._workbooks

    def select_workbook(self, workbook_id):
        self._record("select_workbook", workbook_id=workbook_id)
        self._selected = workbook_id
        return {"selected": True, "workbook_id": workbook_id}

    def get_selected_workbook_id(self):
        return self._selected

    def list_sheets(self, workbook_id=None):
        return {"sheets": ["Sheet1", "Sheet2"], "count": 2, "active_sheet": "Sheet1"}

    def select_sheet(self, workbook_id, sheet_name):
        return {"selected": True, "sheet_name": sheet_name}

    def get_active_selection_ref(self, workbook_id, sheet_name):
        return "A1:D10"

    def get_used_range_ref(self, workbook_id, sheet_name):
        return "A1:D10"

    def read_range(self, workbook_id, sheet_name, range_ref):
        self._record("read_range", range_ref=range_ref)
        stored = self._stored_grid(sheet_name, range_ref)
        if stored is not None:
            return {
                "values": stored,
                "address": range_ref,
                "row_count": len(stored),
                "col_count": len(stored[0]) if stored else 0,
            }
        return {"values": [[1, 2]], "address": range_ref, "row_count": 1, "col_count": 2}

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        self._record("write_range", start_cell=start_cell, values_2d=values_2d)
        base = _parse_cell(start_cell)
        if base is not None:
            base_col, base_row = base
            for row_idx, row in enumerate(values_2d):
                cells = row if isinstance(row, list) else [row]
                for col_idx, value in enumerate(cells):
                    ref = f"{_idx_to_col(base_col + col_idx)}{base_row + row_idx}"
                    self._cells[(sheet_name or "", ref)] = value
        return {
            "written_cells": sum(len(r) if isinstance(r, list) else 1 for r in values_2d),
            "address": start_cell,
        }

    def _stored_grid(self, sheet_name, range_ref):
        """요청 범위의 모든 셀을 기록해 뒀을 때만 그 값을 돌려준다.

        아직 쓴 적 없는 범위(사용 범위 훑기, 다이제스트 등)는 예전처럼 고정값으로
        둔다. 안 그러면 이 가짜에 기대던 다른 시나리오가 함께 무너진다.
        """
        span = _parse_range(range_ref)
        if span is None:
            return None
        (start_col, start_row), (end_col, end_row) = span
        if (end_col - start_col + 1) * (end_row - start_row + 1) > 400:
            return None
        grid = []
        for row in range(start_row, end_row + 1):
            values = []
            for col in range(start_col, end_col + 1):
                key = (sheet_name or "", f"{_idx_to_col(col)}{row}")
                if key not in self._cells:
                    return None
                values.append(self._cells[key])
            grid.append(values)
        return grid

    def highlight_by_condition(
        self,
        workbook_id,
        sheet_name,
        target_range,
        operator,
        threshold,
        fill_color,
        compare_column=None,
        value=None,
    ):
        self._record(
            "highlight_by_condition",
            target_range=target_range,
            operator=operator,
            threshold=threshold,
        )
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        self._record("set_formula", range_ref=range_ref, formula_a1=formula_a1)
        return {"formula_applied_cells": 5, "address": range_ref}


CONFIRM_ACTIONS = {
    "excel_live.write_range",
    "excel_live.highlight_by_condition",
    "excel_live.set_formula",
}


Scenario = dict[str, Any]


SCENARIOS: list[Scenario] = [
    {"message": "열린 통합문서 목록 보여줘", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "workbook list please", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "지금 열려 있는 엑셀 파일 확인", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "워크북 sales.xlsx 선택", "action": "excel_live.select_workbook", "difficulty": "low"},
    {"message": "통합문서 inventory.xlsx로 전환", "action": "excel_live.select_workbook", "difficulty": "low"},
    {"message": "파일 report.xlsx 열기", "action": "excel_live.select_workbook", "difficulty": "low"},
    {
        "message": "B9 값만 읽어줘",
        "action": "excel_live.read_range",
        "difficulty": "low",
        "expect": {"range_ref": "B9"},
    },
    {
        "message": "A1:C10 조회해줘",
        "action": "excel_live.read_range",
        "difficulty": "low",
        "expect": {"range_ref": "A1:C10"},
    },
    {"message": "B열 보여줘", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "D:D 값 확인", "action": "excel_live.read_range", "difficulty": "low"},
    {
        "message": "C5 read",
        "action": "excel_live.read_range",
        "difficulty": "low",
        "expect": {"range_ref": "C5"},
    },
    {
        "message": "F2:F20 display",
        "action": "excel_live.read_range",
        "difficulty": "low",
        "expect": {"range_ref": "F2:F20"},
    },
    {
        "message": "H1:H5 범위 확인",
        "action": "excel_live.read_range",
        "difficulty": "low",
        "expect": {"range_ref": "H1:H5"},
    },
    {"message": "Z열 데이터 보여줘", "action": "excel_live.read_range", "difficulty": "low"},
    {
        "message": "AA1:AC3 읽어줘",
        "action": "excel_live.read_range",
        "difficulty": "mid",
        "expect": {"range_ref": "AA1:AC3"},
    },
    {
        "message": "C3에 120 입력해줘",
        "action": "excel_live.write_range",
        "difficulty": "low",
        "expect": {"start_cell": "C3", "cell_count": 1, "values": [[120]]},
    },
    {
        "message": "D4에 완료 써줘",
        "action": "excel_live.write_range",
        "difficulty": "low",
        "expect": {"start_cell": "D4", "cell_count": 1, "values": [["완료"]]},
    },
    {
        "message": "E5에 3.14 입력",
        "action": "excel_live.write_range",
        "difficulty": "low",
        "expect": {"start_cell": "E5", "cell_count": 1, "values": [[3.14]]},
    },
    {"message": "F6에 true 입력", "action": "excel_live.write_range", "difficulty": "low"},
    {
        "message": "G7에 보류 작성",
        "action": "excel_live.write_range",
        "difficulty": "low",
        "expect": {"start_cell": "G7", "cell_count": 1},
    },
    {
        "message": "H8에 999 set",
        "action": "excel_live.write_range",
        "difficulty": "low",
        "expect": {"start_cell": "H8", "cell_count": 1, "values": [[999]]},
    },
    {
        "message": "B2:D2에 이름,수량,금액 입력",
        "action": "excel_live.write_range",
        "difficulty": "mid",
        "expect": {"start_cell": "B2", "cell_count": 3},
    },
    {
        "message": "A10:C10에 사과,10,3000 입력",
        "action": "excel_live.write_range",
        "difficulty": "mid",
        "expect": {"start_cell": "A10", "cell_count": 3},
    },
    {
        "message": "E3:G3에 Y,N,Y set",
        "action": "excel_live.write_range",
        "difficulty": "mid",
        "expect": {"start_cell": "E3", "cell_count": 3},
    },
    {
        "message": "H4:J4에 1,2,3 작성",
        "action": "excel_live.write_range",
        "difficulty": "mid",
        "expect": {"start_cell": "H4", "cell_count": 3},
    },
    {
        "message": "K5:M5에 alpha,beta,gamma write",
        "action": "excel_live.write_range",
        "difficulty": "mid",
        "expect": {"start_cell": "K5", "cell_count": 3},
    },
    {"message": "B1:D1에 헤더 써줘", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "write header in E1:G1", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "A2:C2 header fill", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "A열에서 50 이상인 셀만 노란색 배경 적용", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "B열은 20 초과 조건에 맞는 항목만 빨강으로 채워줘", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "C1:C20 범위에서 100 이상 값만 highlight", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "D:D 컬럼에서 0 이하 숫자는 파란색 표시", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "E열은 10보다 작은 값 발견 시 초록 배경으로 바꿔줘", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "F:F 5 초과 칠해줘", "action": "excel_live.highlight_by_condition", "difficulty": "mid"},
    {"message": "G열 3 같지 않음 노란색으로 표시", "action": "excel_live.highlight_by_condition", "difficulty": "high"},
    {"message": "H:H >= 0 highlight", "action": "excel_live.highlight_by_condition", "difficulty": "high"},
    {"message": "C1에 B2:B20 합계 수식 넣어줘", "action": "excel_live.set_formula", "difficulty": "mid"},
    {"message": "D1에 B1:B10 평균 수식 적용", "action": "excel_live.set_formula", "difficulty": "mid"},
    {"message": "E1에 C1:C10 최대 수식 적용", "action": "excel_live.set_formula", "difficulty": "mid"},
    {"message": "F1에 D1:D10 최소 수식 적용", "action": "excel_live.set_formula", "difficulty": "mid"},
    {"message": "G1에 E1:E10 개수 수식 적용", "action": "excel_live.set_formula", "difficulty": "mid"},
    {"message": "H1에 F1:F10 sum formula set", "action": "excel_live.set_formula", "difficulty": "high"},
    {
        "message": "I1:I10에 수식 =A1*2 적용해줘",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "I1:I10", "formula": "=A1*2"},
    },
    {
        "message": "J1에 수식 =SUM(A1:A10) 적용",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "J1", "formula": "=SUM(A1:A10)"},
    },
    {
        "message": "K2:K20에 formula =IF(A2>0,\"Y\",\"N\") set",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "K2:K20", "formula": '=IF(A2>0,"Y","N")'},
    },
    {
        "message": "L3에 수식 =AVERAGE(B1:B10) 적용",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "L3", "formula": "=AVERAGE(B1:B10)"},
    },
    {
        "message": "M1:M10에 formula =COUNTIF(A1:A10,\">=5\") set",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "M1:M10", "formula": '=COUNTIF(A1:A10,">=5")'},
    },
    {
        "message": "N2:N20에 formula =IFERROR(VLOOKUP(A2,$P$2:$Q$20,2,FALSE),\"\") set",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {
            "range_ref": "N2:N20",
            "formula": '=IFERROR(VLOOKUP(A2,$P$2:$Q$20,2,FALSE),"")',
        },
    },
    {
        "message": "O2:O20에 formula =IF(AND(B2>0,C2>0),B2*C2,0) set",
        "action": "excel_live.set_formula",
        "difficulty": "high",
        "expect": {"range_ref": "O2:O20", "formula": "=IF(AND(B2>0,C2>0),B2*C2,0)"},
    },
]


def _assert_execution_matches(service, scenario, expect):
    """서비스가 실제로 받은 인자를 시나리오 기대와 대조한다.

    액션 이름이 맞아도 값이 엉뚱한 셀에 들어가면 사용자에게는 실패다.
    """
    message = scenario["message"]
    method = scenario["action"].split(".", 1)[1]
    call = service.last_call(method)
    assert call is not None, f"{message}: {method} 호출 기록 없음"

    if "start_cell" in expect:
        assert call["start_cell"] == expect["start_cell"], (
            f"{message}: 시작 셀이 {call['start_cell']} (기대 {expect['start_cell']})"
        )
    if "cell_count" in expect:
        written = sum(len(row) if isinstance(row, list) else 1 for row in call["values_2d"])
        assert written == expect["cell_count"], (
            f"{message}: {written}칸에 썼음 (기대 {expect['cell_count']}칸) — {call['values_2d']}"
        )
    if "values" in expect:
        assert call["values_2d"] == expect["values"], (
            f"{message}: 값이 {call['values_2d']} (기대 {expect['values']})"
        )
    if "range_ref" in expect:
        assert call["range_ref"] == expect["range_ref"], (
            f"{message}: 범위가 {call['range_ref']} (기대 {expect['range_ref']})"
        )
    if "formula" in expect:
        assert call["formula_a1"] == expect["formula"], (
            f"{message}: 수식이 {call['formula_a1']} (기대 {expect['formula']})"
        )


def _execute_command_and_assert(message: str, expected_action: str):
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
    assert resp.status_code == 200, message
    body = resp.json()
    assert body["action"] == expected_action, message

    if expected_action in CONFIRM_ACTIONS:
        assert body["approval_required"] is True, message
        approval_id = body["pending_approval"]["approval_id"]
        done = client.post(
            "/excel-live/approval",
            json={"approval_id": approval_id, "approved": True},
            headers=HEADERS,
        )
        assert done.status_code == 200, message
        done_body = done.json()
        assert done_body["ok"] is True, message
        assert isinstance(done_body.get("result"), dict), message
    else:
        assert body["approval_required"] is False, message
        assert body["ok"] is True, message


EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _load_sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover - 환경별 의존성 가드
        pytest.skip(f"sentence-transformers 미설치 또는 import 실패: {exc}")
    try:
        return SentenceTransformer(EMBEDDING_MODEL_NAME)
    except Exception as exc:  # pragma: no cover - 오프라인/캐시 미존재 환경 가드
        pytest.skip(f"임베딩 모델 로드 실패({EMBEDDING_MODEL_NAME}): {exc}")


def _max_embedding_cosine(messages: list[str]) -> float:
    try:
        from sentence_transformers import util  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover - 환경별 의존성 가드
        pytest.skip(f"sentence-transformers util import 실패: {exc}")

    model = _load_sentence_transformer()
    embeddings = model.encode(messages, convert_to_tensor=True, normalize_embeddings=True)
    sim_matrix = util.cos_sim(embeddings, embeddings).cpu().tolist()

    max_sim = 0.0
    for i in range(len(messages)):
        for j in range(i + 1, len(messages)):
            max_sim = max(max_sim, float(sim_matrix[i][j]))
    return max_sim


def test_excel_live_scenario_quality_gate():
    # 1) 시나리오 수 고정
    assert len(SCENARIOS) == 50

    # 2) 문장 중복 금지
    messages = [s["message"] for s in SCENARIOS]
    assert len(messages) == len(set(messages))

    # 3) 난이도 균형 (low/mid/high 모두 충분히 포함)
    levels = Counter(s["difficulty"] for s in SCENARIOS)
    assert levels["low"] >= 10
    assert levels["mid"] >= 20
    assert levels["high"] >= 10

    # 4) sentence-transformers 임베딩 코사인 유사도 상한
    max_sim = _max_embedding_cosine(messages)
    # 의미적으로 너무 유사한 프롬프트 쌍을 방지하기 위한 상한
    assert max_sim < 0.92


def test_excel_live_realworld_50_commands(monkeypatch):
    # 인스턴스를 하나만 두고 재사용한다. 호출마다 새로 만들면 기록이 남지 않는다.
    service = _FakeExcelService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: service)
    assert len(SCENARIOS) == 50

    for scenario in SCENARIOS:
        _execute_command_and_assert(scenario["message"], scenario["action"])
        expect = scenario.get("expect")
        if expect:
            _assert_execution_matches(service, scenario, expect)

