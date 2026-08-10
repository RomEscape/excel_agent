"""LoRA 어댑터 학습 결과를 병합 전에 빠르게 점검한다.

베이스 모델 + 어댑터를 로드해서, 학습 데이터에 있던 형식의 명령 몇 개를 넣어보고
JSON 플랜이 스키마에 맞게 나오는지 확인한다. (병합/GGUF 변환 전 사전 점검용)
"""
from __future__ import annotations

import json

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

BASE_MODEL = "skt/A.X-4.0-Light"
ADAPTER_DIR = "artifacts/ax7b-planner-lora"

SYSTEM_PROMPT = (
    "너는 OfficeClaw Excel 플래너다.\n"
    "반드시 JSON만 출력한다.\n"
    "action_plan의 action은 excel_live.* 만 허용한다."
)

TEST_PROMPTS = [
    "C3에 777 입력해줘",
    "A1:A10 합계를 B1에 넣어줘",
    "D열에서 0 이하인 셀을 파란색으로 표시해줘",
    "시트를 저장해줘",
    "B2 셀에 굵게 표시하고 배경을 노란색으로 바꿔줘",
]


def _extract_first_json(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def main() -> None:
    print(f"[load] base model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(ADAPTER_DIR, trust_remote_code=True)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        trust_remote_code=True,
        device_map={"": 0},
    )
    print(f"[load] adapter: {ADAPTER_DIR}")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()

    results = []
    for prompt in TEST_PROMPTS:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        encoded = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        input_ids = encoded["input_ids"].to(model.device)

        with torch.no_grad():
            output = model.generate(
                input_ids,
                max_new_tokens=256,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated = tokenizer.decode(
            output[0][input_ids.shape[1] :], skip_special_tokens=True
        ).strip()

        is_valid_json = False
        has_excel_live_action = False
        first_json = _extract_first_json(generated)
        try:
            parsed = json.loads(first_json)
            is_valid_json = True
            plan = parsed.get("action_plan") or parsed.get("plan") or []
            if isinstance(plan, list):
                has_excel_live_action = all(
                    str(step.get("action", "")).startswith("excel_live.")
                    for step in plan
                    if isinstance(step, dict)
                )
        except json.JSONDecodeError:
            pass

        print("=" * 70)
        print(f"[prompt] {prompt}")
        print(f"[output] {generated}")
        print(f"[valid_json={is_valid_json}] [excel_live_only={has_excel_live_action}]")
        results.append(
            {
                "prompt": prompt,
                "output": generated,
                "valid_json": is_valid_json,
                "excel_live_only": has_excel_live_action,
            }
        )

    passed = sum(1 for r in results if r["valid_json"] and r["excel_live_only"])
    print("=" * 70)
    print(f"[SUMMARY] {passed}/{len(results)} passed (valid JSON + excel_live.* only)")

    with open("logs/quick_sanity_check.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("[DONE] saved: logs/quick_sanity_check.json")


if __name__ == "__main__":
    main()
