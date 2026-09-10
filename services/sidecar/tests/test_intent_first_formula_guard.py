"""의도층이 수식 연결을 값 쓰기로 낮추면 규칙의 수식 계획을 지킨다.

2026-09-11 실측: `OFFICECLAW_INTENT_FIRST` 기본이 켜진 뒤 의도 정규화(ax4-light)가
"요약 시트 B2에 원본 시트 E2 값을 연결해줘" 를 task=write_value 로 냈다. 그대로면
`set_formula ='원본'!E2` 가 `write_range` 리터럴로 바뀐다 — 성공으로 보고되는
미검출 오실행. 이 핀은 그 상황을 목으로 재현해 규칙 계획이 지켜지는지 본다.
"""

from __future__ import annotations

import asyncio

from office_claw_sidecar.services import excel_live_agent as agent

_LINK_PLAN = [
    {"action": "excel_live.select_sheet", "params": {"sheet_name": "요약"}},
    {
        "action": "excel_live.set_formula",
        "params": {"range_ref": "B2", "formula_a1": "='원본'!E2", "sheet_name": "요약"},
    },
]
_LITERAL = {
    "action_plan": [{"action": "excel_live.write_range", "params": {"start_cell": "B2", "values_2d": [["E2"]]}}],
    "action": "excel_live.write_range",
    "params": {"start_cell": "B2", "values_2d": [["E2"]]},
    "reason": "정규화",
    "intent": "edit",
    "plan_source": "intent",
}


def test_literal_write_over_formula_link_restores_the_rule_plan():
    out = agent._keep_formula_over_literal(_LITERAL, _LINK_PLAN)
    assert out is not None
    assert [s["action"] for s in out["action_plan"]] == ["excel_live.select_sheet", "excel_live.set_formula"]
    assert out["action_plan"][1]["params"]["formula_a1"] == "='원본'!E2"
    assert out["plan_source"] == "rule"


def test_guard_stays_out_when_the_rule_plan_is_not_a_formula():
    sort_plan = [{"action": "excel_live.sort_range", "params": {"target_range": "A1:C9"}}]
    assert agent._keep_formula_over_literal(_LITERAL, sort_plan) is None
    assert agent._keep_formula_over_literal(_LITERAL, None) is None


def test_guard_stays_out_when_the_intent_layer_agrees_on_a_formula():
    formula_intent = dict(_LITERAL, action="excel_live.set_formula")
    assert agent._keep_formula_over_literal(formula_intent, _LINK_PLAN) is None


def test_parse_path_returns_the_formula_plan_when_normalizer_downgrades(monkeypatch):
    """실제 경로: 정규화가 write_value 를 내고 매핑이 write_range 계획을 만들어도 수식이 남는다."""

    async def _fake_normalize(message, digest, llm):
        return {"task": "write_value", "range": "B2", "column": "원본 시트", "option": "E2"}

    monkeypatch.setattr(agent, "normalize_intent", _fake_normalize)
    monkeypatch.setattr(agent, "intent_to_plan", lambda intent, **kw: dict(_LITERAL))
    seen: list[dict] = []
    monkeypatch.setattr(agent, "trace_note", lambda name, **fields: seen.append({"name": name, **fields}))

    out = asyncio.run(
        agent.parse_excel_live_command(
            "요약 시트 B2에 원본 시트 E2 값을 연결해줘",
            llm_service=None,
            context={"intent_first_rule_plan": _LINK_PLAN, "workbook_digest": {}},
        )
    )
    assert [s["action"] for s in out["action_plan"]] == ["excel_live.select_sheet", "excel_live.set_formula"]
    assert any(n.get("purpose") == "intent_first_formula_guard" for n in seen)
