"""학습/검증 분할이 지켜야 할 성질 — v5에서 깨졌던 것들이다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from split_planner_sft import (
    UNVERIFIED_DATASETS,
    action_of,
    dedupe,
    source_of,
    stratified_split,
)


def _record(record_id: str, instruction: str, action: str) -> dict:
    plan = {"intent": "edit", "action_plan": [{"action": action, "params": {}}]}
    return {
        "record_id": record_id,
        "messages": [
            {"role": "user", "content": instruction},
            {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
        ],
    }


def _corpus() -> list[dict]:
    rows = []
    for source in ("cover_v5", "manual_new_tool", "hard_scenario_report_v1"):
        for action in ("excel_live.write_range", "excel_live.sort_range", "excel_live.clear_range"):
            for i in range(12):
                rows.append(_record(f"{source}:{action}:{i}", f"{source} {action} {i}번 명령", action))
    return rows


def test_source_and_action_are_read_from_the_record():
    row = _record("cover_v5:x:1", "A1에 120 입력해줘", "excel_live.write_range")
    assert source_of(row) == "cover_v5"
    assert action_of(row) == "excel_live.write_range"


def test_unparsable_answers_do_not_crash_the_split():
    row = {"record_id": "x:1", "messages": [{"role": "assistant", "content": "정답 아님"}]}
    assert action_of(row) == "unparsable"


def test_identical_pairs_collapse_to_one():
    """v5 검증셋 34건 중 14건이 같은 항목의 반복이었다."""
    rows = [_record("a:1", "표 만들어줘", "excel_live.create_table")] * 3
    kept, removed = dedupe(rows)
    assert len(kept) == 1
    assert removed == 2


def test_split_never_leaks_a_prompt_into_both_sides():
    train, test = stratified_split(_corpus(), test_ratio=0.1, seed=7)
    train_prompts = {row["messages"][0]["content"] for row in train}
    test_prompts = {row["messages"][0]["content"] for row in test}
    assert not (train_prompts & test_prompts)


def test_every_source_reaches_the_validation_set():
    """출처별로 갈린 분할이 v5 검증 손실을 못 쓰게 만들었다."""
    train, test = stratified_split(_corpus(), test_ratio=0.1, seed=7)
    assert {source_of(row) for row in test} == {source_of(row) for row in train}


def test_validation_never_asks_about_an_action_absent_from_training():
    train, test = stratified_split(_corpus(), test_ratio=0.1, seed=7)
    assert {action_of(row) for row in test} <= {action_of(row) for row in train}


def test_small_strata_keep_at_least_one_training_example():
    rows = [_record("rare:1", "드문 명령", "excel_live.export_pdf")]
    train, test = stratified_split(rows, test_ratio=0.9, seed=7)
    assert len(train) == 1
    assert not test


def test_split_is_reproducible_for_a_given_seed():
    first = stratified_split(_corpus(), test_ratio=0.1, seed=11)[1]
    second = stratified_split(_corpus(), test_ratio=0.1, seed=11)[1]
    assert [row["record_id"] for row in first] == [row["record_id"] for row in second]


def test_log_harvested_traffic_is_treated_as_unverified():
    assert "officeclaw_all_events" in UNVERIFIED_DATASETS


def test_shipped_v6_split_holds_the_invariants():
    """실제로 만들어 둔 v6 파일이 위 성질을 지키는지."""
    root = Path(__file__).resolve().parents[2] / "datasets" / "train"
    train_path, test_path = root / "planner_sft_v6_train.jsonl", root / "planner_sft_v6_test.jsonl"
    if not train_path.exists() or not test_path.exists():
        pytest.skip("v6 분할 파일이 아직 없다")

    def load(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    train, test = load(train_path), load(test_path)
    train_prompts = {row["messages"][0]["content"] for row in train}
    test_prompts = [row["messages"][0]["content"] for row in test]

    assert not (train_prompts & set(test_prompts))
    assert len(test_prompts) == len(set(test_prompts))
    assert not {source_of(row) for row in test} & UNVERIFIED_DATASETS
    assert not {source_of(row) for row in train} & UNVERIFIED_DATASETS
    assert {action_of(row) for row in test} <= {action_of(row) for row in train}
