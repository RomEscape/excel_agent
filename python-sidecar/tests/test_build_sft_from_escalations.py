"""에스컬레이션 수확기 검증."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_sft_from_escalations",
    Path(__file__).resolve().parents[1] / "scripts" / "build_sft_from_escalations.py",
)
harvester = importlib.util.module_from_spec(_SPEC)
sys.modules["build_sft_from_escalations"] = harvester
_SPEC.loader.exec_module(harvester)


def _row(tier: str, action: str, instruction: str = "금액 정렬"):
    return {
        "instruction": instruction,
        "final_tier": tier,
        "output_json": {
            "intent": "edit",
            "action_plan": [{"action": action, "params": {"column": "금액"}, "reason": "정렬"}],
        },
        "digest": {"active_sheet": "매출", "sheets": []},
    }


def test_harvests_plans_solved_by_upper_tiers():
    solved, _unsolved, stats = harvester.harvest(
        [_row("local_repair", "excel_live.sort_rows"), _row("strong", "excel_live.drop_column", "열 지워")]
    )
    assert stats["harvested"] == 2
    assert {r["output_json"]["action_plan"][0]["action"] for r in solved} == {
        "excel_live.sort_rows",
        "excel_live.drop_column",
    }
    assert all(r["digest"]["active_sheet"] == "매출" for r in solved)


def test_clarify_endings_are_not_taught():
    """되묻기로 끝난 턴을 정답으로 넣으면 '어려우면 물어봐라'를 강화한다."""
    solved, _unsolved, stats = harvester.harvest([_row("strong", "excel_live.clarify")])
    assert solved == []
    assert stats["skipped_not_teachable"] == 1


def test_unsolved_goes_to_review_list_not_training():
    solved, unsolved, _stats = harvester.harvest([_row("failed", "excel_live.sort_rows")])
    assert solved == []
    assert len(unsolved) == 1


def test_local_success_rows_are_never_in_queue_but_are_ignored_if_present():
    solved, unsolved, _stats = harvester.harvest([_row("local", "excel_live.sort_rows")])
    assert solved == []
    assert len(unsolved) == 1


def test_repeated_failures_are_deduplicated():
    rows = [_row("strong", "excel_live.sort_rows") for _ in range(4)]
    solved, _unsolved, stats = harvester.harvest(rows)
    assert len(solved) == 1
    assert stats["skipped_duplicate"] == 3


def test_unknown_action_is_rejected():
    solved, _unsolved, stats = harvester.harvest([_row("strong", "excel_live.make_coffee")])
    assert solved == []
    assert stats["skipped_not_teachable"] == 1
