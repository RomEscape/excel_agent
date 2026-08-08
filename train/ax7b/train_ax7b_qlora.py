from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _require_training_deps() -> tuple[Any, ...]:
    try:
        import torch
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            Trainer,
            TrainingArguments,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "학습 의존성이 없습니다. `pip install torch transformers peft bitsandbytes` 후 다시 실행하세요."
        ) from exc
    return (
        torch,
        LoraConfig,
        get_peft_model,
        prepare_model_for_kbit_training,
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("YAML 파서를 찾을 수 없습니다. `pip install pyyaml` 후 재시도하세요.") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("YAML 설정이 dict 형식이 아닙니다.")
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class TrainConfig:
    run_name: str = "ax7b-planner-qlora-v1"
    seed: int = 42
    train_jsonl: str = "datasets/train/ax7b_planner_sft_train.jsonl"
    output_dir: str = "artifacts/ax7b-planner-lora"
    base_model: str = "skt/A.X-4.0-Light"
    max_seq_length: int = 2048
    load_in_4bit: bool = True
    bnb_4bit_quant_type: str = "nf4"
    bnb_4bit_compute_dtype: str = "bfloat16"
    bnb_4bit_use_double_quant: bool = True
    lora_r: int = 64
    lora_alpha: int = 128
    lora_dropout: float = 0.05
    target_modules: list[str] = field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]
    )
    num_train_epochs: float = 3.0
    learning_rate: float = 2.0e-4
    weight_decay: float = 0.01
    warmup_ratio: float = 0.03
    per_device_train_batch_size: int = 1
    gradient_accumulation_steps: int = 16
    logging_steps: int = 20
    save_steps: int = 200
    save_total_limit: int = 3
    gradient_checkpointing: bool = True
    bf16: bool = True
    fp16: bool = False
    optim: str = "paged_adamw_8bit"

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TrainConfig":
        cfg = TrainConfig()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


def _read_sft_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def _to_messages(row: dict[str, Any]) -> list[dict[str, Any]]:
    """레코드를 chat messages 목록으로 정규화한다."""
    messages = row.get("messages")
    if isinstance(messages, list) and messages:
        return [msg for msg in messages if isinstance(msg, dict)]

    instruction = _text(row.get("instruction"))
    output_json = row.get("output_json")
    if isinstance(output_json, dict):
        answer = json.dumps(output_json, ensure_ascii=False)
    else:
        answer = _text(row.get("output"))
    return [
        {"role": "user", "content": instruction},
        {"role": "assistant", "content": answer},
    ]


def _split_prompt_and_answer(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """
    마지막 assistant 턴을 정답으로, 그 앞을 프롬프트로 나눈다.

    프롬프트 구간은 손실에서 제외해야 한다. 플래너 프롬프트는 4천 자가 넘고
    정답은 250자 남짓이라, 마스킹하지 않으면 손실의 대부분이 '프롬프트를
    그대로 외우는' 데 쓰여 정작 계획 생성을 배우지 못한다.
    """
    for idx in range(len(messages) - 1, -1, -1):
        if _text(messages[idx].get("role")) == "assistant":
            return messages[:idx], _text(messages[idx].get("content"))
    return messages, ""


class JsonlSFTDataset:
    """
    모델의 실제 chat template으로 렌더링하고, 정답 구간에만 손실을 건다.

    이전 구현은 `<|system|>/<|user|>/<|assistant|>`라는 자체 템플릿을 썼는데
    추론(Ollama)은 모델 본래 템플릿을 쓴다. 학습·추론 형식이 달라 파인튜닝
    효과가 사라졌으므로 `tokenizer.apply_chat_template`으로 통일한다.
    """

    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, max_seq_length: int):
        self.items: list[dict[str, Any]] = []
        self.truncated = 0

        for row in rows:
            messages = _to_messages(row)
            prompt_messages, answer = _split_prompt_and_answer(messages)

            prompt_text = tokenizer.apply_chat_template(
                prompt_messages, tokenize=False, add_generation_prompt=True
            )
            full_text = prompt_text + answer + (tokenizer.eos_token or "")

            prompt_len = len(tokenizer(prompt_text, add_special_tokens=False)["input_ids"])
            encoded = tokenizer(
                full_text,
                truncation=True,
                max_length=max_seq_length,
                padding="max_length",
                add_special_tokens=False,
                return_tensors="pt",
            )

            input_ids = encoded["input_ids"][0]
            attention_mask = encoded["attention_mask"][0]
            labels = input_ids.clone()
            labels[attention_mask == 0] = -100
            labels[: min(prompt_len, labels.numel())] = -100

            if int(attention_mask.sum()) >= max_seq_length:
                self.truncated += 1
            if int((labels != -100).sum()) == 0:
                # 정답이 통째로 잘렸다면 학습 신호가 없어 오히려 해롭다.
                continue

            self.items.append(
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "labels": labels,
                }
            )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self.items[idx]


def _dtype_from_name(torch_mod: Any, name: str) -> Any:
    lowered = _text(name).lower()
    if lowered in {"bf16", "bfloat16"}:
        return torch_mod.bfloat16
    if lowered in {"fp16", "float16"}:
        return torch_mod.float16
    return torch_mod.float32


def run_train(config: TrainConfig) -> None:
    (
        torch,
        LoraConfig,
        get_peft_model,
        prepare_model_for_kbit_training,
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    ) = _require_training_deps()

    random.seed(int(config.seed))
    torch.manual_seed(int(config.seed))

    train_path = Path(config.train_jsonl)
    if not train_path.exists():
        raise FileNotFoundError(f"학습 데이터가 없습니다: {train_path}")

    rows = _read_sft_rows(train_path)
    if not rows:
        raise RuntimeError("학습 데이터가 비어 있습니다.")

    tokenizer = AutoTokenizer.from_pretrained(config.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quant_cfg = None
    if bool(config.load_in_4bit):
        quant_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type=_text(config.bnb_4bit_quant_type) or "nf4",
            bnb_4bit_compute_dtype=_dtype_from_name(torch, config.bnb_4bit_compute_dtype),
            bnb_4bit_use_double_quant=bool(config.bnb_4bit_use_double_quant),
        )

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        trust_remote_code=True,
        quantization_config=quant_cfg,
        device_map="auto",
    )
    if bool(config.gradient_checkpointing):
        model.gradient_checkpointing_enable()
    model = prepare_model_for_kbit_training(model)

    lora_cfg = LoraConfig(
        r=int(config.lora_r),
        lora_alpha=int(config.lora_alpha),
        lora_dropout=float(config.lora_dropout),
        target_modules=list(config.target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)

    dataset = JsonlSFTDataset(rows=rows, tokenizer=tokenizer, max_seq_length=int(config.max_seq_length))
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_args = TrainingArguments(
        output_dir=str(out_dir),
        run_name=config.run_name,
        num_train_epochs=float(config.num_train_epochs),
        learning_rate=float(config.learning_rate),
        weight_decay=float(config.weight_decay),
        warmup_ratio=float(config.warmup_ratio),
        per_device_train_batch_size=int(config.per_device_train_batch_size),
        gradient_accumulation_steps=int(config.gradient_accumulation_steps),
        logging_steps=int(config.logging_steps),
        save_steps=int(config.save_steps),
        save_total_limit=int(config.save_total_limit),
        bf16=bool(config.bf16),
        fp16=bool(config.fp16),
        optim=_text(config.optim) or "paged_adamw_8bit",
        remove_unused_columns=False,
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=dataset,  # type: ignore[arg-type]
    )
    trainer.train()
    trainer.save_model(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    print(f"[DONE] adapter saved: {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A.X 7B QLoRA 학습 스크립트")
    parser.add_argument("--config", type=Path, default=Path("train/ax7b/qlora_config.yaml"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = _load_yaml(args.config)
    config = TrainConfig.from_dict(payload)
    run_train(config)


if __name__ == "__main__":
    main()

