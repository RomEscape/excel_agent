"""
QLoRA 체크포인트 여러 개를 GGUF 변환 없이 바로 비교한다.

체크포인트마다 병합→GGUF→Ollama 등록을 돌리면 한 건에 30분·30GB가 든다.
어댑터 선택만 하려는 단계에서는 과하다. 이 스크립트는 베이스를 4bit로 한 번만
올리고 어댑터만 갈아 끼우며 생성해, 세 체크포인트를 몇 분 만에 줄 세운다.
최종 승자만 merge_lora_to_fp16.py → GGUF → Ollama로 보내면 된다.

사용:
    python train/ax7b/eval_checkpoints.py \
        --test-jsonl datasets/train/planner_sft_v3_test.jsonl \
        --adapter artifacts/ax7b-planner-lora-v3/checkpoint-125 \
        --adapter artifacts/ax7b-planner-lora-v3/checkpoint-250 \
        --adapter artifacts/ax7b-planner-lora-v3 \
        --output-json logs/eval_ax7b_v3_checkpoints.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


def _require_deps() -> tuple[Any, ...]:
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except Exception as exc:
        raise RuntimeError(
            "추론 의존성이 없습니다. `pip install torch transformers peft bitsandbytes` 후 재시도하세요."
        ) from exc
    return torch, PeftModel, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


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


def split_case(row: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """SFT 레코드에서 (프롬프트, 정답 JSON)을 뽑는다."""
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    prompt = ""
    answer_text = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", ""))
        content = str(msg.get("content", ""))
        if role == "user" and not prompt:
            prompt = content
        elif role == "assistant":
            answer_text = content
    if not prompt or not answer_text:
        return None
    try:
        answer = json.loads(answer_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(answer, dict):
        return None
    return prompt, answer


def action_seq(plan: Any) -> list[str]:
    if not isinstance(plan, list):
        return []
    out: list[str] = []
    for step in plan:
        if isinstance(step, dict):
            action = str(step.get("action", "")).strip()
            if action:
                out.append(action)
    return out


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_plan(text: str) -> dict[str, Any] | None:
    """생성문에서 계획 JSON을 뽑는다. 프로덕션 파서와 같은 관용도를 유지한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    for candidate in (stripped, None):
        if candidate is None:
            match = _JSON_BLOCK.search(stripped)
            if not match:
                return None
            candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def evaluate(
    *,
    label: str,
    model: Any,
    tokenizer: Any,
    cases: list[tuple[str, dict[str, Any]]],
    max_new_tokens: int,
    torch: Any,
) -> dict[str, Any]:
    parse_ok = 0
    first_match = 0
    seq_match = 0
    intent_match = 0
    latencies: list[int] = []
    details: list[dict[str, Any]] = []

    for prompt, answer in cases:
        expected = action_seq(answer.get("action_plan"))
        chat = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(chat, return_tensors="pt").to(model.device)
        started = time.perf_counter()
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed)

        completion = tokenizer.decode(
            generated[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )
        plan = extract_plan(completion)
        predicted = action_seq(plan.get("action_plan")) if plan else []

        if predicted:
            parse_ok += 1
        if predicted and expected and predicted[0] == expected[0]:
            first_match += 1
        if predicted and predicted == expected:
            seq_match += 1
        if plan and str(plan.get("intent", "")) == str(answer.get("intent", "")):
            intent_match += 1

        details.append(
            {
                "expected": expected,
                "predicted": predicted,
                "elapsed_ms": elapsed,
                # 틀린 건만 원문을 남긴다. 전부 남기면 리포트가 수십 MB가 된다.
                "raw": "" if predicted == expected else completion[:600],
            }
        )

    total = len(cases) or 1
    return {
        "label": label,
        "total": len(cases),
        "parse_ok_rate": round(parse_ok / total, 4),
        "first_action_match_rate": round(first_match / total, 4),
        "exact_action_seq_match_rate": round(seq_match / total, 4),
        "intent_match_rate": round(intent_match / total, 4),
        "latency_ms_avg": int(sum(latencies) / len(latencies)) if latencies else 0,
        "cases": details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default="skt/A.X-4.0-Light")
    parser.add_argument("--test-jsonl", type=Path, required=True)
    parser.add_argument(
        "--adapter",
        dest="adapters",
        action="append",
        default=[],
        help="비교할 어댑터 디렉터리. 여러 번 지정할 수 있다.",
    )
    parser.add_argument(
        "--include-base",
        action="store_true",
        help="어댑터를 끈 베이스 모델 성능도 같이 잰다.",
    )
    parser.add_argument("--max-new-tokens", type=int, default=320)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    torch, PeftModel, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig = _require_deps()
    args = parse_args()

    rows = read_jsonl(args.test_jsonl)
    cases: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        case = split_case(row)
        if case:
            cases.append(case)
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit(f"평가할 케이스가 없습니다: {args.test_jsonl}")
    print(f"[eval] 케이스 {len(cases)}건")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=quant,
        device_map="auto",
        trust_remote_code=True,
    )
    base.eval()

    reports: list[dict[str, Any]] = []

    if args.include_base:
        print("[eval] base (어댑터 없음)")
        reports.append(
            evaluate(
                label="base",
                model=base,
                tokenizer=tokenizer,
                cases=cases,
                max_new_tokens=args.max_new_tokens,
                torch=torch,
            )
        )

    # 어댑터마다 베이스를 다시 올리면 4bit 양자화만 매번 몇 분씩 든다.
    # PeftModel 하나에 이름을 달아 얹고 set_adapter로 갈아 끼운다.
    model = None
    for raw_dir in args.adapters:
        adapter_dir = Path(raw_dir)
        if not adapter_dir.exists():
            print(f"[skip] 없는 경로: {adapter_dir}")
            continue
        name = adapter_dir.name
        print(f"[eval] {name}")
        if model is None:
            model = PeftModel.from_pretrained(base, str(adapter_dir), adapter_name=name)
        else:
            model.load_adapter(str(adapter_dir), adapter_name=name)
        model.set_adapter(name)
        model.eval()
        reports.append(
            evaluate(
                label=name,
                model=model,
                tokenizer=tokenizer,
                cases=cases,
                max_new_tokens=args.max_new_tokens,
                torch=torch,
            )
        )

    payload = {
        "base_model": args.base_model,
        "test_jsonl": str(args.test_jsonl),
        "reports": reports,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    header = f"{'checkpoint':<20}{'parse':>8}{'first':>8}{'seq':>8}{'intent':>8}{'ms':>8}"
    print(header)
    print("-" * len(header))
    for report in reports:
        print(
            f"{report['label']:<20}"
            f"{report['parse_ok_rate']:>8.2f}"
            f"{report['first_action_match_rate']:>8.2f}"
            f"{report['exact_action_seq_match_rate']:>8.2f}"
            f"{report['intent_match_rate']:>8.2f}"
            f"{report['latency_ms_avg']:>8d}"
        )
    print(f"\n[DONE] {args.output_json}")


if __name__ == "__main__":
    main()
