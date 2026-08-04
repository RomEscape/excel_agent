from __future__ import annotations

import argparse
from pathlib import Path


def _require_deps():
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "병합 의존성이 없습니다. `pip install torch transformers peft` 후 다시 실행하세요."
        ) from exc
    return torch, PeftModel, AutoModelForCausalLM, AutoTokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LoRA 어댑터를 FP16 모델로 병합")
    parser.add_argument("--base-model", type=str, required=True, help="예: skt/A.X-4.0-Light")
    parser.add_argument("--adapter-dir", type=Path, required=True, help="QLoRA 산출물 디렉터리")
    parser.add_argument("--output-dir", type=Path, required=True, help="병합 모델 출력 디렉터리")
    return parser.parse_args()


def main() -> None:
    torch, PeftModel, AutoModelForCausalLM, AutoTokenizer = _require_deps()
    args = parse_args()

    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.float16,
        trust_remote_code=True,
        device_map="auto",
    )
    peft_model = PeftModel.from_pretrained(base_model, str(args.adapter_dir))
    merged = peft_model.merge_and_unload()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(out_dir), safe_serialization=True)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    tokenizer.save_pretrained(str(out_dir))

    print(f"[DONE] merged model saved: {out_dir}")


if __name__ == "__main__":
    main()

