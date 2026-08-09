"""
배포된 플래너 모델이 학습과 같은 포맷으로 응답하는지 확인하는 스모크 체크.

Ollama에 등록한 직후 이걸 먼저 돌린다. v1은 Modelfile TEMPLATE이 학습 포맷과
달라서 계획 JSON이 통째로 안 나왔는데, 섀도우 평가까지 가서야 그걸 알았다.
여기서 parse 실패가 보이면 평가를 돌릴 필요가 없다.

사용:
  python train/ax7b/smoke_check_planner.py --model ax7bplanner-v2 --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

DEFAULT_JSONL = Path("datasets/distill/planner_sft_v2_test.jsonl")
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/v1/chat/completions"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 모델 스모크 체크")
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def call_ollama(model: str, prompt: str, timeout: float) -> tuple[str, int]:
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_CHAT_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return body["choices"][0]["message"]["content"], elapsed_ms


def first_actions(raw: str) -> list[str]:
    """excel_live_agent와 같은 방식으로 계획 JSON을 뽑는다."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    steps = parsed.get("action_plan")
    if not isinstance(steps, list):
        return []
    return [str(s.get("action", "")) for s in steps if isinstance(s, dict)]


def main() -> None:
    args = parse_args()
    rows = [
        json.loads(line)
        for line in args.jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][: args.limit]

    ok = 0
    exact = 0
    for idx, row in enumerate(rows, start=1):
        messages = row["messages"]
        prompt = messages[0]["content"]
        expected = first_actions(messages[-1]["content"])

        raw, elapsed_ms = call_ollama(args.model, prompt, args.timeout)
        predicted = first_actions(raw)

        parsed_ok = bool(predicted)
        seq_match = predicted == expected
        ok += int(parsed_ok)
        exact += int(seq_match)

        print(f"[{idx}] {row.get('record_id', '')} {elapsed_ms}ms")
        print(f"    expected : {expected}")
        print(f"    predicted: {predicted if parsed_ok else '(파싱 실패)'}")
        if not parsed_ok:
            print(f"    raw[:300]: {raw[:300]!r}")
        print(f"    parse={parsed_ok} exact={seq_match}")

    total = len(rows)
    print(f"\n[SUMMARY] model={args.model} total={total} parse_ok={ok} exact_seq={exact}")


if __name__ == "__main__":
    main()
