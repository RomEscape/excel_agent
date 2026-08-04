from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from augment_paraphrases import augment_rows, generate_paraphrases  # noqa: E402


def test_generate_paraphrases_produces_unique_non_empty_variants():
    variants = generate_paraphrases("경기를 경기도로 바꿔줘", max_variants=5)
    assert variants
    assert len(variants) == len(set(variants))
    assert "경기를 경기도로 바꿔줘" not in variants


def test_generate_paraphrases_handles_empty_text():
    assert generate_paraphrases("", max_variants=3) == []


def test_augment_rows_only_uses_verified_records_and_preserves_action_plan():
    rows = [
        {
            "record_id": "r1",
            "input": {"instruction": "매출 열에 색조 넣어줘"},
            "target": {"label_status": "verified", "action_plan": [{"action": "excel_live.apply_color_scale"}]},
            "metadata": {},
        },
        {
            "record_id": "r2",
            "input": {"instruction": "이건 로그일 뿐이야"},
            "target": {"label_status": "log_observed", "action_plan": [{"action": "excel_live.clear_range"}]},
            "metadata": {},
        },
    ]
    augmented = augment_rows(rows, max_variants_per_row=3)
    assert augmented
    assert all(a["target"]["action_plan"] == [{"action": "excel_live.apply_color_scale"}] for a in augmented)
    assert all(a["record_id"].startswith("r1::augment:") for a in augmented)
    assert all("paraphrase_of:r1" in a["metadata"]["notes"] for a in augmented)


def test_augment_rows_skips_rows_without_instruction():
    rows = [
        {
            "record_id": "r3",
            "input": {"instruction": ""},
            "target": {"label_status": "verified", "action_plan": [{"action": "excel_live.merge_cells"}]},
            "metadata": {},
        }
    ]
    assert augment_rows(rows, max_variants_per_row=3) == []


def test_paraphrase_cli_json_roundtrip(tmp_path):
    input_path = tmp_path / "in.jsonl"
    output_path = tmp_path / "out.jsonl"
    row = {
        "record_id": "cli1",
        "input": {"instruction": "틀 고정 해줘"},
        "target": {"label_status": "verified", "action_plan": [{"action": "excel_live.freeze_panes"}]},
        "metadata": {},
    }
    input_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "augment_paraphrases.py"),
            "--input-jsonl",
            str(input_path),
            "--output-jsonl",
            str(output_path),
            "--max-variants-per-row",
            "2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "DONE" in result.stdout
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 3  # 원본 1 + 증강 최소 2
