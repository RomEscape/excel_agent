from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_excel_distill_jsonl import parse_all_events

KST = timezone(timedelta(hours=9), name="KST")


@dataclass
class SplitStats:
    total: int = 0
    hard_cases: int = 0


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = str(line or "").strip()
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_hard_keyword(message: str) -> bool:
    lowered = message.lower()
    keywords = [
        "피벗",
        "pivot",
        "비교",
        "compare",
        "diff",
        "예측",
        "forecast",
        "시뮬레이션",
        "유효성",
        "validation",
        "입력 제한",
        "set_formula",
        "수식",
        "dedupe",
        "중복 제거",
        "consolidate",
        "통합",
    ]
    return any(token in lowered for token in keywords)


def score_hard_case(row: dict[str, Any]) -> int:
    score = 0
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    context = row.get("input", {}) if isinstance(row.get("input"), dict) else {}
    hints = context.get("context_hints") if isinstance(context.get("context_hints"), dict) else {}
    instruction = _text(context.get("instruction"))

    passed = quality.get("passed")
    if passed is False:
        score += 3

    status_code = hints.get("status_code")
    try:
        status = int(status_code)
    except Exception:
        status = 0
    if status >= 400:
        score += 2

    reason = _text(hints.get("reason")).lower()
    if any(token in reason for token in ["timeout", "timed out", "실패", "오류", "error"]):
        score += 2

    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    action_plan = target.get("action_plan") if isinstance(target.get("action_plan"), list) else []
    if not action_plan:
        score += 1

    if _is_hard_keyword(instruction):
        score += 1

    locale = _text(context.get("locale")).lower()
    if locale == "mixed":
        score += 1

    return score


def add_hard_case_note(row: dict[str, Any], score: int) -> dict[str, Any]:
    copied = json.loads(json.dumps(row, ensure_ascii=False))
    meta = copied.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        copied["metadata"] = meta
    notes = meta.get("notes")
    if not isinstance(notes, list):
        notes = []
        meta["notes"] = notes
    tag = f"hard_case_score:{score}"
    if tag not in notes:
        notes.append(tag)
    return copied


def stable_shuffle(rows: list[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    keyed: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        record_id = _text(row.get("record_id")) or hashlib.sha1(
            json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        digest = hashlib.sha1(f"{seed}|{record_id}".encode()).hexdigest()
        keyed.append((digest, row))
    keyed.sort(key=lambda item: item[0])
    return [row for _, row in keyed]


def split_rows(
    rows: list[dict[str, Any]],
    *,
    train_ratio: float,
    valid_ratio: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    total = len(rows)
    train_end = int(total * train_ratio)
    valid_end = train_end + int(total * valid_ratio)
    train_rows = rows[:train_end]
    valid_rows = rows[train_end:valid_end]
    test_rows = rows[valid_end:]
    return train_rows, valid_rows, test_rows


def build_from_all_events(path: Path, *, preferred_locale: str) -> list[dict[str, Any]]:
    rows, _ = parse_all_events(
        log_path=path,
        split="train",
        limit=0,
        root=Path.cwd(),
        preferred_locale=preferred_locale,
        drop_non_preferred_locale=False,
    )
    return rows


def update_split_tag(rows: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for row in rows:
        copied = json.loads(json.dumps(row, ensure_ascii=False))
        source = copied.get("source")
        if not isinstance(source, dict):
            source = {}
            copied["source"] = source
        source["split"] = split
        updated.append(copied)
    return updated


def build_manifest(
    *,
    output_dir: Path,
    total_rows: int,
    hard_rows: int,
    train_rows: int,
    valid_rows: int,
    test_rows: int,
    records: list[dict[str, Any]],
    source_paths: list[str],
) -> dict[str, Any]:
    locale_counter = Counter(
        _text((row.get("input") or {}).get("locale")).lower() or "unknown"
        for row in records
        if isinstance(row, dict)
    )
    label_counter = Counter(
        _text((row.get("target") or {}).get("label_status")).lower() or "unknown"
        for row in records
        if isinstance(row, dict)
    )
    return {
        "at": datetime.now(KST).isoformat(),
        "output_dir": str(output_dir),
        "sources": source_paths,
        "total_rows": total_rows,
        "hard_case_rows": hard_rows,
        "splits": {
            "train": train_rows,
            "valid": valid_rows,
            "test": test_rows,
        },
        "locale_distribution": dict(locale_counter),
        "label_status_distribution": dict(label_counter),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="distill hard-case 샘플링 + split 고정")
    parser.add_argument("--all-events", type=Path, default=None, help="all_events.jsonl 경로")
    parser.add_argument("--input-jsonl", type=Path, default=None, help="기존 distill JSONL 입력")
    parser.add_argument("--output-dir", type=Path, default=Path("../../datasets/distill"))
    parser.add_argument("--preferred-locale", type=str, default="ko")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hard-case-min-score", type=int, default=2)
    parser.add_argument("--hard-case-cap", type=int, default=5000)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--valid-ratio", type=float, default=0.1)
    parser.add_argument("--stats", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    source_paths: list[str] = []
    base_rows: list[dict[str, Any]] = []
    if args.input_jsonl:
        base_rows = iter_jsonl(args.input_jsonl)
        source_paths.append(str(args.input_jsonl))
    elif args.all_events:
        base_rows = build_from_all_events(
            args.all_events,
            preferred_locale=str(args.preferred_locale or "ko"),
        )
        source_paths.append(str(args.all_events))
    else:
        raise SystemExit("--input-jsonl 또는 --all-events 중 하나는 필요합니다.")

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in base_rows:
        score = score_hard_case(row)
        scored.append((score, add_hard_case_note(row, score)))

    hard_cases = [row for score, row in scored if score >= int(args.hard_case_min_score)]
    if int(args.hard_case_cap) > 0:
        hard_cases = sorted(
            hard_cases,
            key=lambda row: int(
                str(next((n for n in (row.get("metadata", {}).get("notes", [])) if str(n).startswith("hard_case_score:")), "hard_case_score:0")).split(":")[-1]
            ),
            reverse=True,
        )[: int(args.hard_case_cap)]

    # hard case가 앞에 오도록 가중치를 주고, 같은 점수대는 안정 셔플한다.
    hard_ids = {_text(row.get("record_id")) for row in hard_cases}
    weighted: list[dict[str, Any]] = []
    weighted.extend(hard_cases)
    weighted.extend([row for _, row in scored if _text(row.get("record_id")) not in hard_ids])
    shuffled = stable_shuffle(weighted, seed=int(args.seed))

    train_rows, valid_rows, test_rows = split_rows(
        shuffled,
        train_ratio=float(args.train_ratio),
        valid_ratio=float(args.valid_ratio),
    )
    train_rows = update_split_tag(train_rows, "train")
    valid_rows = update_split_tag(valid_rows, "valid")
    test_rows = update_split_tag(test_rows, "test")

    train_path = output_dir / "excel_distill_v1_train.jsonl"
    valid_path = output_dir / "excel_distill_v1_valid.jsonl"
    test_path = output_dir / "excel_distill_v1_test.jsonl"
    hard_path = output_dir / "excel_distill_v1_hard_cases.jsonl"
    manifest_path = output_dir / "freeze_manifest.json"

    write_jsonl(train_path, train_rows)
    write_jsonl(valid_path, valid_rows)
    write_jsonl(test_path, test_rows)
    write_jsonl(hard_path, hard_cases)

    manifest = build_manifest(
        output_dir=output_dir,
        total_rows=len(shuffled),
        hard_rows=len(hard_cases),
        train_rows=len(train_rows),
        valid_rows=len(valid_rows),
        test_rows=len(test_rows),
        records=shuffled,
        source_paths=source_paths,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.stats:
        print(f"[DONE] output_dir={output_dir}")
        print(
            f"[DONE] total={len(shuffled)} hard_cases={len(hard_cases)} "
            f"train={len(train_rows)} valid={len(valid_rows)} test={len(test_rows)}"
        )
        print(f"[DONE] manifest={manifest_path}")


if __name__ == "__main__":
    main()

