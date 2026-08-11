"""all_events.jsonl에 쌓인 트래픽이 누구 것인지 집계한다.

학습 데이터를 다시 만들기 전에 "이 로그에 사람이 친 명령이 실제로 몇 건인가"를
먼저 확인하는 용도다. `traffic_origin.classify`를 그대로 쓰므로 수확기가 거를
기준과 항상 같은 답이 나온다.

    uv run python scripts/report_traffic_origin.py ../logs/all_events.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from office_claw_sidecar.services.traffic_origin import classify

ROUTE = "/excel-live/command"


def iter_command_payloads(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("event_type") != "harness":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if str(payload.get("route", "")).strip() != ROUTE:
                continue
            yield payload


def main() -> None:
    parser = argparse.ArgumentParser(description="harness 트래픽 출처 집계")
    parser.add_argument("log_path", type=Path)
    parser.add_argument("--samples", type=int, default=10, help="사람 트래픽 예시 출력 개수")
    args = parser.parse_args()

    by_label: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    user_rows: list[dict[str, Any]] = []
    total = 0

    for payload in iter_command_payloads(args.log_path):
        total += 1
        origin = classify(payload)
        by_label[origin.label] += 1
        by_kind[origin.kind] += 1
        if origin.is_user:
            user_rows.append(payload)

    print(f"{ROUTE} harness 이벤트: {total}건")
    print()
    for kind, count in by_kind.most_common():
        share = (count / total * 100) if total else 0.0
        print(f"  {kind:<6} {count:>6}건  {share:5.1f}%")
    print()
    print("판정 규칙별")
    for label, count in by_label.most_common():
        print(f"  {count:>6}  {label}")

    unique_messages = {str(p.get("message", "")).strip() for p in user_rows}
    unique_messages.discard("")
    print()
    print(f"사람 트래픽 {len(user_rows)}건 / 서로 다른 명령 {len(unique_messages)}개")
    for message in sorted(unique_messages)[: args.samples]:
        print(f"  - {message[:80]}")


if __name__ == "__main__":
    main()
