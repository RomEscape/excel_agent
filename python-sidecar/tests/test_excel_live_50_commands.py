"""Excel Live 실사용 50개 명령 회귀 테스트.

목표:
  - 실제 사용자 문장에 가까운 명령 50개를 /excel-live/command로 순차 실행
  - SAFE/CONFIRM 흐름(승인 필요 작업 포함) 검증
  - 파서 회귀를 빠르게 감지
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router


HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


class _FakeExcelService:
    def __init__(self):
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

    def is_available(self):
        return True

    def list_workbooks(self):
        return self._workbooks

    def select_workbook(self, workbook_id):
        self._selected = workbook_id
        return {"selected": True, "workbook_id": workbook_id}

    def get_selected_workbook_id(self):
        return self._selected

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [[1, 2]], "address": range_ref, "row_count": 1, "col_count": 2}

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d):
        return {
            "written_cells": sum(len(r) if isinstance(r, list) else 1 for r in values_2d),
            "address": start_cell,
        }

    def highlight_by_condition(
        self,
        workbook_id,
        sheet_name,
        target_range,
        operator,
        threshold,
        fill_color,
    ):
        return {"matched_cells": 3, "changed_cells": 3, "address": target_range}

    def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
        return {"formula_applied_cells": 5, "address": range_ref}


CONFIRM_ACTIONS = {
    "excel_live.write_range",
    "excel_live.highlight_by_condition",
    "excel_live.set_formula",
}


Scenario = dict[str, str]


SCENARIOS: list[Scenario] = [
    {"message": "열린 통합문서 목록 보여줘", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "workbook list please", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "지금 열려 있는 엑셀 파일 확인", "action": "excel_live.list_workbooks", "difficulty": "low"},
    {"message": "워크북 sales.xlsx 선택", "action": "excel_live.select_workbook", "difficulty": "low"},
    {"message": "통합문서 inventory.xlsx로 전환", "action": "excel_live.select_workbook", "difficulty": "low"},
    {"message": "파일 report.xlsx 열기", "action": "excel_live.select_workbook", "difficulty": "low"},
    {"message": "B9 값만 읽어줘", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "A1:C10 조회해줘", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "B열 보여줘", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "D:D 값 확인", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "C5 read", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "F2:F20 display", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "H1:H5 범위 확인", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "Z열 데이터 보여줘", "action": "excel_live.read_range", "difficulty": "low"},
    {"message": "AA1:AC3 읽어줘", "action": "excel_live.read_range", "difficulty": "mid"},
    {"message": "C3에 120 입력해줘", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "D4에 완료 써줘", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "E5에 3.14 입력", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "F6에 true 입력", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "G7에 보류 작성", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "H8에 999 set", "action": "excel_live.write_range", "difficulty": "low"},
    {"message": "B2:D2에 이름,수량,금액 입력", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "A10:C10에 사과,10,3000 입력", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "E3:G3에 Y,N,Y set", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "H4:J4에 1,2,3 작성", "action": "excel_live.write_range", "difficulty": "mid"},
    {"message": "K5:M5에 alpha,beta,gamma write", "action": "excel_live.write_range", "difficulty": "mid"},
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
    {"message": "I1:I10에 수식 =A1*2 적용해줘", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "J1에 수식 =SUM(A1:A10) 적용", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "K2:K20에 formula =IF(A2>0,\"Y\",\"N\") set", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "L3에 수식 =AVERAGE(B1:B10) 적용", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "M1:M10에 formula =COUNTIF(A1:A10,\">=5\") set", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "N2:N20에 formula =IFERROR(VLOOKUP(A2,$P$2:$Q$20,2,FALSE),\"\") set", "action": "excel_live.set_formula", "difficulty": "high"},
    {"message": "O2:O20에 formula =IF(AND(B2>0,C2>0),B2*C2,0) set", "action": "excel_live.set_formula", "difficulty": "high"},
]


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
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: _FakeExcelService())
    assert len(SCENARIOS) == 50

    for scenario in SCENARIOS:
        _execute_command_and_assert(scenario["message"], scenario["action"])

