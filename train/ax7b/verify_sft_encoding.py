"""
학습 데이터가 모델의 실제 chat template으로 올바르게 인코딩되는지 검사한다.

확인 항목:
  - 프롬프트가 max_seq_length에 잘리지 않는지 (잘리면 정답 학습 신호가 사라진다)
  - 손실이 걸리는 토큰이 정답 구간뿐인지
  - 디코딩 결과가 추론 시 Ollama가 만드는 형식과 같은지

GPU 없이 토크나이저만 로드하므로 몇 초면 끝난다.

사용:
    python train/ax7b/verify_sft_encoding.py <jsonl> [--max-seq-length 3072]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from train_ax7b_qlora import JsonlSFTDataset, _read_sft_rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--base-model", default="skt/A.X-4.0-Light")
    parser.add_argument("--max-seq-length", type=int, default=3072)
    parser.add_argument("--show", type=int, default=1, help="본문을 출력할 샘플 수")
    args = parser.parse_args()

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    rows = _read_sft_rows(args.jsonl)
    dataset = JsonlSFTDataset(rows=rows, tokenizer=tokenizer, max_seq_length=args.max_seq_length)

    print(f"레코드            : {len(rows)}")
    print(f"인코딩 성공       : {len(dataset)}")
    print(f"길이 초과로 잘림  : {dataset.truncated}")

    label_counts = []
    total_lengths = []
    for item in dataset.items:
        label_counts.append(int((item["labels"] != -100).sum()))
        total_lengths.append(int(item["attention_mask"].sum()))

    if not label_counts:
        print("학습 가능한 샘플이 없습니다.")
        raise SystemExit(1)

    print(f"실토큰 길이       : 최소 {min(total_lengths)} / 평균 {sum(total_lengths)//len(total_lengths)} / 최대 {max(total_lengths)}")
    print(f"손실 대상 토큰    : 최소 {min(label_counts)} / 평균 {sum(label_counts)//len(label_counts)} / 최대 {max(label_counts)}")

    for idx in range(min(args.show, len(dataset))):
        item = dataset.items[idx]
        mask = item["labels"] != -100
        answer = tokenizer.decode(item["input_ids"][mask], skip_special_tokens=False)
        head = tokenizer.decode(item["input_ids"][:60], skip_special_tokens=False)
        print("\n" + "─" * 70)
        print(f"[{idx}] 템플릿 앞부분:\n{head}")
        print(f"\n[{idx}] 손실이 걸린 구간(정답):\n{answer}")


if __name__ == "__main__":
    main()
