"""플래너 SFT 데이터를 학습/검증으로 가른다.

v5의 분할은 출처별로 갈려 있었다. 학습은 합성 템플릿 1,574건, 검증은 실행 로그
34건이었다. 그래서 검증 손실은 held-out 성능이 아니라 분포 차이를 재고 있었고,
학습 손실이 0.009까지 내려가는 동안 0.84에서 움직이지 않았다. 게다가 그 34건
중 21건은 pytest 세션이 만든 트래픽이었고 14건은 중복이었다.

여기서는 세 가지를 지킨다.

1. 오염 제거 — 사람이 만들었다고 확인되지 않은 출처는 통째로 뺀다.
2. 중복 제거 — 같은 (지시, 정답) 쌍은 한 번만 남긴다. 검증셋에 중복이 있으면
   그 항목만 가중치를 더 받는다.
3. 층화 분할 — 출처와 첫 액션을 함께 층으로 삼아, 검증셋이 학습셋과 같은
   분포에서 뽑히게 한다.

    uv run python scripts/split_planner_sft.py \\
        --input ../datasets/train/planner_sft_v5_train.jsonl \\
                ../datasets/train/planner_sft_v5_test.jsonl \\
        --train-out ../datasets/train/planner_sft_v6_train.jsonl \\
        --test-out ../datasets/train/planner_sft_v6_test.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# 사람이 친 명령이라고 확인되지 않은 출처. `traffic_origin` 기준으로 다시 세어 보니
# `logs/all_events.jsonl` 10,827건 중 사람으로 확인된 것이 0건이었다.
UNVERIFIED_DATASETS = frozenset({"officeclaw_all_events"})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def source_of(row: dict[str, Any]) -> str:
    return str(row.get("record_id", "")).split(":", 1)[0] or "unknown"


def _message(row: dict[str, Any], role: str) -> str:
    for entry in reversed(row.get("messages") or []):
        if entry.get("role") == role:
            return str(entry.get("content", ""))
    return ""


def action_of(row: dict[str, Any]) -> str:
    """정답 계획의 첫 액션. 층화 기준이자 커버리지 확인용."""
    try:
        plan = json.loads(_message(row, "assistant")).get("action_plan") or []
    except json.JSONDecodeError:
        return "unparsable"
    if not plan:
        return "empty_plan"
    return str(plan[0].get("action", "")) or "unnamed"


def dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for row in rows:
        key = (_message(row, "user"), _message(row, "assistant"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept, len(rows) - len(kept)


def stratified_split(
    rows: list[dict[str, Any]], *, test_ratio: float, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """출처×액션을 층으로 삼아 나눈다.

    작은 층을 통째로 학습에 몰아주면 그 출처가 검증셋에서 사라진다. 실제로
    내림만 했을 때 `hard_scenario_report_v1` 54건이 검증에 한 건도 안 들어갔다 —
    가장 어려운 케이스가 측정에서 빠지는 셈이다. 그래서 소수점 부분은 확률로
    반올림해 기대값이 비율과 맞게 하고, 층마다 최소 한 건은 학습에 남긴다.
    """
    rng = random.Random(seed)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strata[(source_of(row), action_of(row))].append(row)

    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for _key, bucket in sorted(strata.items()):
        rng.shuffle(bucket)
        expected = len(bucket) * test_ratio
        take = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
        take = min(take, len(bucket) - 1)
        test.extend(bucket[:take])
        train.extend(bucket[take:])

    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


def _distribution(rows: list[dict[str, Any]], key) -> Counter[str]:
    return Counter(key(row) for row in rows)


def report(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> None:
    print(f"\n학습 {len(train)}건 / 검증 {len(test)}건 (검증 비중 {len(test) / max(len(train) + len(test), 1) * 100:.1f}%)")

    train_sources = _distribution(train, source_of)
    test_sources = _distribution(test, source_of)
    print("\n출처별 (학습 / 검증)")
    for name in sorted(set(train_sources) | set(test_sources)):
        print(f"  {name:<28} {train_sources[name]:>5} / {test_sources[name]:>4}")

    train_actions = _distribution(train, action_of)
    test_actions = _distribution(test, action_of)
    missing = sorted(set(test_actions) - set(train_actions))
    print(f"\n액션 종류: 학습 {len(train_actions)}종 / 검증 {len(test_actions)}종")
    print(f"  검증에만 있고 학습에 없는 액션: {missing or '없음'}")

    train_prompts = {_message(row, "user") for row in train}
    leaked = [row for row in test if _message(row, "user") in train_prompts]
    print(f"  학습과 지시문이 겹치는 검증 항목: {len(leaked)}건")

    unique_test = {(_message(r, "user"), _message(r, "assistant")) for r in test}
    print(f"  검증셋 내 중복: {len(test) - len(unique_test)}건")


def main() -> None:
    parser = argparse.ArgumentParser(description="플래너 SFT 학습/검증 분할")
    parser.add_argument("--input", nargs="+", type=Path, required=True)
    parser.add_argument("--train-out", type=Path, required=True)
    parser.add_argument("--test-out", type=Path, required=True)
    parser.add_argument("--test-ratio", type=float, default=0.08)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument(
        "--keep-unverified",
        action="store_true",
        help="사람 트래픽으로 확인되지 않은 출처도 포함한다 (기본은 제외)",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for path in args.input:
        loaded = read_jsonl(path)
        print(f"[읽음] {path} — {len(loaded)}건")
        rows.extend(loaded)

    if not args.keep_unverified:
        dropped = _distribution([r for r in rows if source_of(r) in UNVERIFIED_DATASETS], source_of)
        rows = [row for row in rows if source_of(row) not in UNVERIFIED_DATASETS]
        for name, count in dropped.most_common():
            print(f"[제외] 출처가 확인되지 않음: {name} — {count}건")

    rows, duplicates = dedupe(rows)
    print(f"[중복 제거] {duplicates}건")

    train, test = stratified_split(rows, test_ratio=args.test_ratio, seed=args.seed)
    write_jsonl(args.train_out, train)
    write_jsonl(args.test_out, test)
    report(train, test)
    print(f"\n[저장] {args.train_out}")
    print(f"[저장] {args.test_out}")


if __name__ == "__main__":
    main()
