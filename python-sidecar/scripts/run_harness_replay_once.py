from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from office_claw_sidecar.routers.harness import HarnessReplayRequest, post_replay_failures
from office_claw_sidecar.services.llm_service import get_llm_service

KST = timezone(timedelta(hours=9), name="KST")


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="하네스 실패 리플레이 1회 실행")
    parser.add_argument("--user-id", type=str, default="complex_eval_bot")
    parser.add_argument("--route", type=str, default="/excel-live/command")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--parse-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--min-gate-cases", type=int, default=5)
    parser.add_argument("--min-gate-pass-rate", type=float, default=0.7)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "logs" / "harness_replay_once.json",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    req = HarnessReplayRequest(
        user_id=args.user_id,
        route=args.route,
        limit=max(1, int(args.limit)),
        parse_timeout_seconds=float(args.parse_timeout_seconds),
        min_gate_cases=max(1, int(args.min_gate_cases)),
        min_gate_pass_rate=float(args.min_gate_pass_rate),
    )
    llm = get_llm_service()
    result = await post_replay_failures(req, llm=llm)
    report = result.get("replay_report") if isinstance(result, dict) else {}
    quality_gate = result.get("quality_gate") if isinstance(result, dict) else {}

    payload = {
        "at": _now_iso(),
        "user_id": args.user_id,
        "route": args.route,
        "replay_total": _safe_int((report or {}).get("replay_total", 0)),
        "replay_success": _safe_int((report or {}).get("replay_success", 0)),
        "quality_gate": quality_gate or {},
        "result": result if isinstance(result, dict) else {},
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] replay_total={payload['replay_total']} replay_success={payload['replay_success']}")
    print(f"[DONE] quality_gate_passed={bool((quality_gate or {}).get('passed', False))}")
    print(f"[DONE] output={args.output_json}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
