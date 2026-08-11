"""`json_only`를 켠 것이 플래너 계획을 바꾸는지 같은 모델로 A/B 한다.

디코딩을 JSON 문법으로 묶으면 토큰 분포가 달라질 수 있다. 파싱이 안전해지는 대신
계획이 나빠지면 손해다. 같은 모델·같은 프롬프트·temperature 0으로 두 번 돌려
**고른 액션이 달라지는지**를 본다.

평가셋의 정답(`target.action_plan`)이 있으므로 단순 일치율이 아니라 정확도로 비교한다.
플래그를 켜서 틀린 답이 늘면 켜지 않는 편이 낫다.

    uv run python scripts/ab_json_only.py --limit 40
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from office_claw_sidecar.services.excel_live_agent import parse_command_plan_with_llm
from office_claw_sidecar.services.llm_service import LLMService, OllamaProvider

ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_SET = ROOT / "datasets" / "eval" / "planner_eval_v1.jsonl"


class _PinnedLLM:
    """모델과 json_only를 여기서 고정한다.

    플래너는 항상 json_only=True를 넘기므로, 끈 상태를 재려면 이 자리에서 덮어써야
    한다. 모델도 플래너가 설정에서 읽어 오므로 마찬가지로 여기서 못 박는다.
    """

    def __init__(self, inner: LLMService, *, model: str, json_only: bool) -> None:
        self._inner = inner
        self._model = model
        self._json_only = json_only

    async def chat(self, messages, model=None, temperature=None, json_only=False):
        return await self._inner.chat(
            messages, model=self._model, temperature=temperature, json_only=self._json_only
        )


def _actions(plan: Any) -> list[str]:
    if not isinstance(plan, dict):
        return []
    steps = plan.get("action_plan")
    if not isinstance(steps, list):
        return []
    return [str((s or {}).get("action", "")) for s in steps if isinstance(s, dict)]


async def _run(rows: list[dict], model: str, *, json_only: bool) -> list[dict]:
    llm = _PinnedLLM(LLMService(OllamaProvider()), model=model, json_only=json_only)
    out: list[dict] = []
    for row in rows:
        payload = row["input"]
        try:
            plan = await parse_command_plan_with_llm(
                payload["instruction"],
                llm,
                context={
                    **(payload.get("context_hints") or {}),
                    "workbook_digest_text": payload.get("workbook_digest_text", ""),
                },
            )
            got = _actions(plan)
            error = ""
        except Exception as exc:  # noqa: BLE001 - 실패도 결과의 일부다
            got = []
            error = f"{type(exc).__name__}: {exc}"
        out.append({"record_id": row["record_id"], "actions": got, "error": error})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="ax7bplanner-v5r:latest")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--output-json", default=str(ROOT / "logs" / "ab_json_only.json"))
    args = parser.parse_args()

    rows = []
    with EVAL_SET.open(encoding="utf-8") as handle:
        for line in handle:
            if len(rows) >= args.limit:
                break
            rows.append(json.loads(line))

    off = asyncio.run(_run(rows, args.model, json_only=False))
    on = asyncio.run(_run(rows, args.model, json_only=True))

    def correct(results: list[dict]) -> int:
        hits = 0
        for row, got in zip(rows, results, strict=True):
            want = [str(s.get("action", "")) for s in row["target"]["action_plan"]]
            if got["actions"][: len(want)] == want:
                hits += 1
        return hits

    off_hits, on_hits = correct(off), correct(on)
    diverged = [
        {"record_id": a["record_id"], "off": a["actions"], "on": b["actions"]}
        for a, b in zip(off, on, strict=True)
        if a["actions"] != b["actions"]
    ]
    errors = [r for r in off + on if r["error"]]

    total = len(rows)
    print(f"모델: {args.model}   케이스: {total}")
    print(f"  json_only 끔 : 정확 {off_hits}/{total} ({off_hits / total:.1%})")
    print(f"  json_only 켬 : 정확 {on_hits}/{total} ({on_hits / total:.1%})")
    print(f"  계획이 갈린 케이스: {len(diverged)}")
    print(f"  파싱/실행 오류: {len(errors)}")
    for item in diverged[:10]:
        print(f"    {item['record_id']}: 끔={item['off']} / 켬={item['on']}")

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "model": args.model,
                "cases": total,
                "accuracy_json_only_off": off_hits / total,
                "accuracy_json_only_on": on_hits / total,
                "diverged": diverged,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {out_path}")


if __name__ == "__main__":
    main()
