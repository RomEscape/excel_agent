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
    eval_jsonl: str = ""
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
    eval_steps: int = 0
    gradient_checkpointing: bool = True
    bf16: bool = True
    fp16: bool = False
    optim: str = "paged_adamw_8bit"
    # 옵티마이저 상태까지 살아 있는 체크포인트에서 그대로 이어 돌린다.
    resume_from_checkpoint: str = ""
    # 어댑터 가중치만 이어받아 새로 시작한다(옵티마이저·스케줄러는 초기화).
    # 하드 리셋으로 체크포인트가 저장 도중에 잘렸을 때 쓰는 경로다 —
    # 실측에서 adapter_model.safetensors는 온전한데 optimizer.pt가 잘려 있었다.
    init_adapter_from: str = ""

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
            # 패딩은 배치 단위로 최댓값까지만 채운다(PadToLongestCollator).
            # max_seq_length까지 미리 채우면 4060 Ti 기준 스텝당 10% 가까이를
            # 의미 없는 패딩 토큰의 forward/backward에 쓰게 된다.
            encoded = tokenizer(
                full_text,
                truncation=True,
                max_length=max_seq_length,
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


class PadToLongestCollator:
    """배치 안에서 가장 긴 샘플 길이까지만 오른쪽 패딩한다."""

    def __init__(self, pad_token_id: int):
        self.pad_token_id = int(pad_token_id)

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, Any]:
        import torch

        longest = max(int(f["input_ids"].numel()) for f in features)
        input_ids, attention_mask, labels = [], [], []
        for feature in features:
            gap = longest - int(feature["input_ids"].numel())
            pad = (0, gap)
            input_ids.append(
                torch.nn.functional.pad(feature["input_ids"], pad, value=self.pad_token_id)
            )
            attention_mask.append(torch.nn.functional.pad(feature["attention_mask"], pad, value=0))
            labels.append(torch.nn.functional.pad(feature["labels"], pad, value=-100))
        return {
            "input_ids": torch.stack(input_ids),
            "attention_mask": torch.stack(attention_mask),
            "labels": torch.stack(labels),
        }


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

    # device_map="auto"는 **로드 시점의 여유 VRAM**을 보고 배치를 정한다. 그때 다른
    # 프로세스(Ollama 등)가 VRAM을 쥐고 있으면 일부 레이어를 조용히 CPU로 내리고,
    # 그 배치는 상대 프로세스가 죽은 뒤에도 그대로 남는다. 실측에서 스텝당 55초가
    # 170초로 뛴 원인이 이것이었다 — 오류도 경고도 없이 3배 느려진다.
    # 전부 GPU에 올리고, 안 들어가면 조용히 느려지는 대신 OOM으로 시끄럽게 실패시킨다.
    device_map: Any = {"": 0} if torch.cuda.is_available() else "auto"
    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        trust_remote_code=True,
        quantization_config=quant_cfg,
        device_map=device_map,
    )
    offloaded = sorted(
        {
            str(dev)
            for dev in getattr(model, "hf_device_map", {}).values()
            if str(dev) in {"cpu", "disk"}
        }
    )
    if offloaded:
        raise RuntimeError(
            f"모델 일부가 {offloaded}로 내려갔습니다. VRAM을 쓰는 다른 프로세스를 종료한 뒤 다시 실행하세요."
        )
    # prepare_model_for_kbit_training은 기본값이 use_gradient_checkpointing=True라,
    # 넘겨주지 않으면 설정에서 껐어도 다시 켜진다.
    use_ckpt = bool(config.gradient_checkpointing)
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=use_ckpt)

    warm_start = _text(config.init_adapter_from)
    if warm_start:
        from peft import PeftModel

        warm_dir = Path(warm_start)
        if not (warm_dir / "adapter_model.safetensors").exists():
            raise FileNotFoundError(f"이어받을 어댑터가 없습니다: {warm_dir}")
        model = PeftModel.from_pretrained(model, str(warm_dir), is_trainable=True)
        print(f"[WARM-START] 어댑터 가중치를 이어받습니다: {warm_dir}")
    else:
        lora_cfg = LoraConfig(
            r=int(config.lora_r),
            lora_alpha=int(config.lora_alpha),
            lora_dropout=float(config.lora_dropout),
            target_modules=list(config.target_modules),
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_cfg)

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if trainable == 0:
        raise RuntimeError("학습 가능한 파라미터가 0개입니다 — 어댑터가 동결된 채 로드됐습니다.")
    print(f"[TRAINABLE] {trainable:,}")

    dataset = JsonlSFTDataset(rows=rows, tokenizer=tokenizer, max_seq_length=int(config.max_seq_length))
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 홀드아웃 손실이 없으면 과적합 시점을 알 수 없어 어느 체크포인트를 쓸지 감으로 정하게 된다.
    eval_dataset = None
    eval_steps = int(config.eval_steps)
    eval_path = Path(_text(config.eval_jsonl)) if _text(config.eval_jsonl) else None
    if eval_path is not None and eval_path.exists() and eval_steps > 0:
        eval_rows = _read_sft_rows(eval_path)
        if eval_rows:
            eval_dataset = JsonlSFTDataset(
                rows=eval_rows, tokenizer=tokenizer, max_seq_length=int(config.max_seq_length)
            )

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
        eval_strategy="steps" if eval_dataset is not None else "no",
        eval_steps=eval_steps if eval_dataset is not None else None,
        per_device_eval_batch_size=1,
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=dataset,  # type: ignore[arg-type]
        eval_dataset=eval_dataset,  # type: ignore[arg-type]
        data_collator=PadToLongestCollator(pad_token_id=tokenizer.pad_token_id),
    )
    resume = _text(config.resume_from_checkpoint)
    if resume and not (Path(resume) / "trainer_state.json").exists():
        raise FileNotFoundError(
            f"{resume}에 trainer_state.json이 없어 이어 돌릴 수 없습니다. "
            "저장 도중 잘린 체크포인트라면 init_adapter_from으로 가중치만 이어받으세요."
        )
    trainer.train(resume_from_checkpoint=resume or None)
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

