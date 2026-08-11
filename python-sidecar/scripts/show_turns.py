"""턴 로그(`logs/chat_log.jsonl`)를 사람이 읽는 형태로 펼친다.

    python scripts/show_turns.py                  # 최근 5턴
    python scripts/show_turns.py -n 20            # 최근 20턴
    python scripts/show_turns.py --failed         # 실패한 턴만
    python scripts/show_turns.py --grep 정렬       # 사용자 문장으로 찾기
    python scripts/show_turns.py --turn a1b2c3    # 특정 턴 하나를 자세히
    python scripts/show_turns.py --summary        # 실패 유형 집계

PowerShell에서 한글이 깨지면 콘솔 인코딩 문제다. 로그 파일 자체는 UTF-8이다.

    $env:PYTHONIOENCODING="utf-8"; chcp 65001
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.config import get_chat_log_path
from office_claw_sidecar.services.trace_report import (
    BROKEN,
    classify,
    outcome_class,
    read_turns,
    render,
    source_label,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="턴 로그 조회")
    parser.add_argument("-n", "--count", type=int, default=5, help="최근 몇 턴 (기본 5)")
    parser.add_argument("--failed", action="store_true", help="실패한 턴만")
    parser.add_argument("--grep", default="", help="사용자 문장에 이 말이 든 턴만")
    parser.add_argument("--turn", default="", help="turn_id로 하나만")
    parser.add_argument("--endpoint", default="", help="엔드포인트로 거르기")
    parser.add_argument("--source", default="", help="출처로 거르기 (테스트 이름 등)")
    parser.add_argument("--human", action="store_true", help="사람이 친 명령만 (테스트 제외)")
    parser.add_argument("--summary", action="store_true", help="본문 대신 실패 유형 집계")
    parser.add_argument("--prompt", action="store_true", help="LLM 원본 응답까지 표시")
    parser.add_argument("--log", default="", help="다른 로그 파일 경로")
    args = parser.parse_args()

    path = Path(args.log) if args.log else get_chat_log_path()
    turns = list(read_turns(path))
    if not turns:
        print(f"  기록이 없습니다: {path}")
        return 0

    if args.turn:
        turns = [t for t in turns if str(t.get("turn_id", "")).startswith(args.turn)]
    if args.endpoint:
        turns = [t for t in turns if args.endpoint in str(t.get("endpoint", ""))]
    if args.grep:
        turns = [t for t in turns if args.grep in str(t.get("message", ""))]
    if args.source:
        turns = [t for t in turns if args.source in source_label(t)]
    if args.human:
        turns = [t for t in turns if not (t.get("source") or {})]
    if args.failed:
        turns = [t for t in turns if outcome_class(classify(t).code) == BROKEN]

    if args.summary:
        counts = Counter(classify(t).code for t in turns)
        labels = {classify(t).code: classify(t).label for t in turns}
        print(f"\n  턴 {len(turns)}건 — {path}")
        print("  " + "─" * 52)
        for code, n in counts.most_common():
            bar = "█" * min(n, 30)
            print(f"  {labels.get(code, code):<24} {n:>4}  {bar}")
        return 0

    shown = turns[-args.count :] if args.count > 0 else turns
    print(f"\n  {len(shown)}/{len(turns)}턴 — {path}")
    for turn in shown:
        print()
        print(render(turn, show_prompt=args.prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
