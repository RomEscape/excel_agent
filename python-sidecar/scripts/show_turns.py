"""턴 로그(`logs/chat_log.jsonl`)를 사람이 읽는 형태로 펼친다.

    python scripts/show_turns.py                  # 최근 5턴
    python scripts/show_turns.py -n 20            # 최근 20턴
    python scripts/show_turns.py --failed         # 실패한 턴만
    python scripts/show_turns.py --grep 정렬       # 사용자 문장으로 찾기
    python scripts/show_turns.py --turn a1b2c3    # 특정 턴 하나를 자세히
    python scripts/show_turns.py --summary        # 실패 유형 집계

새 턴이 들어올 때마다 바로 보려면 `--follow`를 쓴다. 앱을 켜 두고 옆에서 돌리면
명령 하나 칠 때마다 그 턴이 펼쳐진다.

    python scripts/show_turns.py --follow                      # 터미널에서 실시간
    python scripts/show_turns.py --follow --out ../logs/turns.txt  # 파일로 (에디터에서 열어 두기)

`--out`은 에디터에서 계속 열어 두는 용도다. jsonl을 직접 읽는 것보다 훨씬 편하고,
다른 거르기 옵션(`--failed`·`--human`·`--grep`)과 같이 쓸 수 있다.

PowerShell에서 한글이 깨지면 콘솔 인코딩 문제다. 로그 파일 자체는 UTF-8이다.

    $env:PYTHONIOENCODING="utf-8"; chcp 65001
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable
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


def _build_filter(args) -> Callable[[dict], bool]:
    """거르기 조건을 술어 하나로 묶는다. 한 번 보기와 실시간 보기가 같은 조건을 쓴다."""

    def keep(turn: dict) -> bool:
        if args.turn and not str(turn.get("turn_id", "")).startswith(args.turn):
            return False
        if args.endpoint and args.endpoint not in str(turn.get("endpoint", "")):
            return False
        if args.grep and args.grep not in str(turn.get("message", "")):
            return False
        if args.source and args.source not in source_label(turn):
            return False
        if args.human and (turn.get("source") or {}):
            return False
        return not (args.failed and outcome_class(classify(turn).code) != BROKEN)

    return keep


def _follow(path: Path, args, keep: Callable[[dict], bool]) -> int:
    """새 턴이 붙을 때마다 펼친다. Ctrl+C로 끝낸다.

    파일을 통째로 다시 읽고 이미 본 turn_id를 건너뛴다. 로그가 수백 KB라 이 편이
    오프셋을 들고 다니는 것보다 단순하고, 배터리가 파일을 갈아엎어도 깨지지 않는다.
    """
    out = Path(args.out) if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)

    def emit(text: str) -> None:
        print(text, flush=True)
        if out:
            with out.open("a", encoding="utf-8") as f:
                f.write(text + "\n")

    if out:
        out.write_text(f"  턴 로그 실시간 — {path}\n", encoding="utf-8")
    print(f"\n  실시간 보기 — {path}" + (f" → {out}" if out else ""))
    print("  Ctrl+C로 끝냅니다.\n")

    seen: set[str] = set()
    first_pass = True
    try:
        while True:
            for turn in read_turns(path):
                turn_id = str(turn.get("turn_id", ""))
                if turn_id in seen:
                    continue
                seen.add(turn_id)
                # 처음 한 바퀴는 이미 쌓인 것 중 끝의 몇 개만 보여 준다.
                if first_pass:
                    continue
                if keep(turn):
                    emit("\n" + render(turn, show_prompt=args.prompt))
            if first_pass:
                first_pass = False
                recent = [t for t in read_turns(path) if keep(t)][-max(args.count, 0) :]
                for turn in recent:
                    emit("\n" + render(turn, show_prompt=args.prompt))
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n  끝냅니다.")
    return 0


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
    parser.add_argument("--follow", action="store_true", help="새 턴이 들어올 때마다 이어서 보기")
    # 콘솔이 CP949인 환경에서는 한글이 깨져서 로그를 읽을 수 없다. 파일로 받으면
    # 항상 UTF-8이라 그 환경에서도 같은 내용을 본다.
    parser.add_argument("--out", default="", help="펼친 내용을 이 파일에 UTF-8로 쓴다")
    args = parser.parse_args()

    path = Path(args.log) if args.log else get_chat_log_path()
    keep = _build_filter(args)

    if args.follow:
        return _follow(path, args, keep)

    turns = [t for t in read_turns(path) if keep(t)]
    if not turns:
        print(f"  기록이 없습니다: {path}")
        return 0

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
    header = f"\n  {len(shown)}/{len(turns)}턴 — {path}"
    body = "\n\n".join(render(turn, show_prompt=args.prompt) for turn in shown)
    if args.out:
        Path(args.out).write_text(f"{header}\n\n{body}\n", encoding="utf-8")
        print(f"  {len(shown)}턴 → {args.out}")
        return 0
    print(header)
    for turn in shown:
        print()
        print(render(turn, show_prompt=args.prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
