"""검증된 (문장, 정답 계획) 레코드에 규칙 기반 패러프레이즈를 덧붙여 학습셋을 늘린다.

LLM으로 패러프레이즈를 만들면 품질은 더 좋을 수 있지만 대상 문장마다 호출 비용이 들고,
가장 중요한 건 "같은 의도를 다른 말투로 표현"하는 표면적 다양성이라 규칙 기반으로도
충분하다. action_plan은 원본과 동일하게 복제한다 — 의미는 그대로, 표현만 바뀐다.

동일 record_id 충돌을 피하려고 원본 record_id를 base로 접미사를 붙인다.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9), name="KST")

# (정규식, 대체 후보들) 순서. 문장 끝부분의 종결 어미를 다른 말투로 바꾼다.
_ENDING_RULES: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"해줘\??$"), ["해주세요", "해줄래?", "해줄 수 있어?", "해주면 좋겠어"]),
    (re.compile(r"줘\??$"), ["주세요", "줄래?", "줄 수 있어?"]),
    (re.compile(r"해줄래\?$"), ["해줘", "해주세요", "해줄 수 있어?"]),
    (re.compile(r"보여줘\??$"), ["보여주세요", "보여줄래?", "표시해줘"]),
    (re.compile(r"바꿔줘\??$"), ["바꿔주세요", "바꿔줄래?", "바꿔주면 좋겠어"]),
    (re.compile(r"넣어줘\??$"), ["넣어주세요", "넣어줄래?", "넣어주면 좋겠어"]),
    (re.compile(r"해\??$"), ["해줘", "해주세요"]),
]

# 문장 앞에 붙이는 캐주얼한 군말. 과하게 붙이면 부자연스러워지니 1개만 무작위로 사용.
_PREFIX_OPTIONS = ["혹시 ", "가능하면 ", "이제 ", ""]
# 동사 앞에 끼워 넣는 강조/완화 부사.
_FILLER_OPTIONS = ["좀 ", "한번 ", ""]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _apply_ending_rule(text: str) -> list[str]:
    out: list[str] = []
    for pattern, replacements in _ENDING_RULES:
        if pattern.search(text):
            for repl in replacements:
                out.append(pattern.sub(repl, text))
            break
    return out


def _apply_filler(text: str) -> list[str]:
    """마지막 동사 어절 바로 앞에 '좀'/'한번'을 끼워 넣는다."""
    out: list[str] = []
    tokens = text.rsplit(" ", 1)
    if len(tokens) != 2:
        return out
    head, tail = tokens
    if tail.startswith(("좀", "한번")):
        return out
    for filler in ("좀 ", "한번 "):
        out.append(f"{head} {filler}{tail}")
    return out


def generate_paraphrases(instruction: str, *, max_variants: int) -> list[str]:
    text = _text(instruction)
    if not text:
        return []

    candidates: list[str] = []
    candidates.extend(_apply_ending_rule(text))
    candidates.extend(_apply_filler(text))
    for prefix in _PREFIX_OPTIONS:
        if prefix and not text.startswith(prefix):
            candidates.append(f"{prefix}{text}")

    seen = {text}
    unique: list[str] = []
    for cand in candidates:
        cand = cand.strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        unique.append(cand)
        if len(unique) >= max_variants:
            break
    return unique


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def augment_rows(rows: list[dict[str, Any]], *, max_variants_per_row: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = datetime.now(KST).isoformat()
    for row in rows:
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        if _text(target.get("label_status")).lower() != "verified":
            continue
        input_obj = row.get("input") if isinstance(row.get("input"), dict) else {}
        instruction = _text(input_obj.get("instruction"))
        if not instruction:
            continue

        base_record_id = _text(row.get("record_id")) or uuid.uuid4().hex
        variants = generate_paraphrases(instruction, max_variants=max_variants_per_row)
        for variant in variants:
            augmented = copy.deepcopy(row)
            augmented["record_id"] = f"{base_record_id}::augment:{uuid.uuid4().hex[:8]}"
            augmented.setdefault("input", {})["instruction"] = variant
            metadata = augmented.setdefault("metadata", {})
            metadata["generator"] = "python-sidecar/scripts/augment_paraphrases.py"
            metadata["created_at"] = now
            notes = list(metadata.get("notes") or [])
            notes.append(f"paraphrase_of:{base_record_id}")
            metadata["notes"] = notes
            out.append(augmented)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="규칙 기반 패러프레이즈 증강")
    parser.add_argument("--input-jsonl", action="append", required=True, help="여러 번 지정 가능")
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--max-variants-per-row", type=int, default=3)
    parser.add_argument("--include-originals", action="store_true", default=True)
    args = parser.parse_args()

    all_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for input_path in args.input_jsonl:
        for row in iter_jsonl(Path(input_path)):
            rid = _text(row.get("record_id"))
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            all_rows.append(row)

    augmented = augment_rows(all_rows, max_variants_per_row=args.max_variants_per_row)

    out_rows = list(all_rows) if args.include_originals else []
    out_rows.extend(augmented)

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"[DONE] input_rows={len(all_rows)} augmented={len(augmented)} "
        f"total_output={len(out_rows)} output={args.output_jsonl}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
