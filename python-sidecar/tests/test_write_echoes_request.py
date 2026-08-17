"""시킨 말을 값으로 써서는 안 된다.

2026-08-17 실측(함수 선택 배터리 12건):

    "F2에 서울 지역 매출만 더한 값 넣어줘"  → F2에 "서울 지역 매출만 더한" (텍스트)
    "F7에 가장 큰 매출 값 넣어줘"          → F7에 "가장 큰 매출"          (텍스트)

같은 실패가 서식에서도 났다 — "천 단위 콤마 넣어줘"가 D5의 97000을 문자열
'천 단위 콤마'로 덮었다. **규칙이 못 잡으면 플래너가 시킨 말을 값으로 쓴다.**

가장 비싼 오탐은 반대 방향이다: "A1에 총매출 입력"은 사용자가 진짜로 '총매출'이라는
머리글을 쓰려는 정당한 요청이다. 그래서 명사가 아니라 **계산 동사**로 판정한다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _AMBIGUITY_SENSITIVE_SLOTS, PlanStep
from office_claw_sidecar.services.excel_param_binder import (
    bind_plan_steps,
    write_values_echo_the_request,
)


def _echo(value, message: str) -> bool:
    cells = value if isinstance(value, list) else [[value]]
    return write_values_echo_the_request({"values_2d": cells}, message)


class TestEchoedInstructionsAreCaught:
    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("서울 지역 매출만 더한", "F2에 서울 지역 매출만 더한 값 넣어줘"),
            ("가장 큰 매출", "F7에 가장 큰 매출 값 넣어줘"),
            ("매출 순위를 매기는", "F8에 매출 순위를 매기는 수식 넣어줘"),
            ("매출을 다 더한", "F9에 매출을 다 더한 값 넣어줘"),
            ("건수를 세는", "F10에 건수를 세는 수식 넣어줘"),
        ],
    )
    def test_a_fragment_of_the_request_is_not_data(self, value, message):
        assert _echo(value, message) is True


class TestLegitimateWritesSurvive:
    """여기서 오탐이 나면 정상 입력이 막힌다 — 가장 비싼 실패다."""

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("총매출", "A1에 총매출 입력"),
            ("합계", "A10에 합계 라고 입력해줘"),
            ("평균 매출", "A1에 평균 매출 입력해줘"),
            ("최대 할인율", "B1에 최대 할인율 입력"),
            ("서울", "A2에 서울 입력"),
        ],
    )
    def test_plain_header_writes_are_allowed(self, value, message):
        assert _echo(value, message) is False

    def test_a_formula_is_never_an_echo(self):
        assert _echo("=SUM(B2:B6)", "F2에 매출을 다 더한 수식 넣어줘") is False

    def test_multi_cell_data_writes_are_allowed(self):
        assert _echo([["서울", "경기", "부산"]], "A2:C2에 서울,경기,부산 입력") is False

    def test_short_values_are_ignored(self):
        # 한두 글자는 우연히 문장에 들어 있기 쉽다.
        assert _echo("큰", "가장 큰 값 넣어줘") is False

    def test_a_sentence_without_a_compute_verb_is_not_checked(self):
        # 계산을 시키지 않았으면 되뇜 판정 자체를 하지 않는다.
        assert _echo("서울 지역 매출", "A1에 서울 지역 매출 입력해줘") is False


class TestEmptyAndMalformed:
    @pytest.mark.parametrize("params", [{}, {"values_2d": []}, {"values_2d": None}])
    def test_missing_values_are_not_echoes(self, params):
        assert write_values_echo_the_request(params, "가장 큰 값 더한 거 넣어줘") is False

    def test_an_empty_message_is_not_checked(self):
        assert _echo("아무거나", "") is False


class TestTheNoteSurvivesToTheGate:
    """검사만 통하고 차단이 안 되면 아무 의미가 없다.

    2026-08-17에 이 사슬이 세 군데서 끊겨 있었다 — 전부 실측으로 하나씩 찾았다:
      1. 머리글을 모르면 `bind_plan_steps`가 `_bind_message_only_slots`로 조기
         반환하는데, 거기엔 검사가 없었다.
      2. `_is_stale_unresolved`가 "값이 채워졌으면 해결된 것"이라며 메모를 지웠다.
         값이 채워진 게 바로 문제인데도.
      3. 라우터 게이트가 `_AMBIGUITY_SENSITIVE_SLOTS`에 든 슬롯만 봤다.
    """

    STEP_PARAMS = {"start_cell": "F7", "values_2d": [["가장 큰 매출"]]}
    MESSAGE = "주문 시트 F7에 가장 큰 매출 값 넣어줘"

    WITH_HEADERS = {
        "active_sheet": "주문",
        "sheets": [{
            "name": "주문", "used_range": "A1:C3",
            "columns": [{"letter": "A", "header": "지역"}, {"letter": "B", "header": "매출"}],
        }],
    }
    WITHOUT_HEADERS = {"active_sheet": "주문", "sheets": [{"name": "주문"}]}

    def _pairs(self, digest):
        step = PlanStep(action="excel_live.write_range", params=dict(self.STEP_PARAMS), reason="")
        _bound, notes = bind_plan_steps(
            [step], digest=digest, message=self.MESSAGE, sheet_name=None
        )
        return {
            (n.get("action"), n.get("slot"))
            for n in notes
            if n.get("status") == "unresolved"
        }

    @pytest.mark.parametrize("which", ["WITH_HEADERS", "WITHOUT_HEADERS"])
    def test_the_note_reaches_the_gate_on_both_binder_paths(self, which):
        pairs = self._pairs(getattr(self, which))
        assert pairs & _AMBIGUITY_SENSITIVE_SLOTS, (
            f"{which}: 메모가 게이트까지 살아남지 못했다 — 그대로 실행된다"
        )

    def test_the_write_slot_is_registered_as_sensitive(self):
        assert ("excel_live.write_range", "values_2d") in _AMBIGUITY_SENSITIVE_SLOTS
