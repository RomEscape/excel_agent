"""SFT 학습셋 인코딩 오염 진단.

2026-08-18 실측: v3 학습셋(planner_sft_v3_train.jsonl) 1000건 중 174건(17%)의
명령 문장이 CP949 이중 인코딩으로 한자 범벅("梨꾨꼸蹂?留ㅼ텧…")이었다. 한국어
명령에 CJK 한자가 나올 일이 없으므로 한자 포함 = 오염으로 판정한다.

v3의 낮은 일반화(훈련체 67%)와 v5r의 라벨 충돌을 논할 때, 학습 입력의 1/6이
쓰레기였다는 사실이 전제가 되어야 한다. v6를 만들 일이 있으면 이 스크립트가
0건을 보고할 때까지 정제가 선행 조건이다.

실행:  & $PY scripts\\audit_sft_encoding.py [경로.jsonl ...]
      (인자 없으면 datasets/train/*.jsonl 전부)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HANJA = re.compile(r"[一-鿿]")
CMD = re.compile(r"사용자\s*메시지\s*:\s*(.+?)(?:\n|$)")


def audit(path: Path) -> None:
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            rows.append(None)  # JSON 자체가 깨진 줄도 오염이다
    total = len(rows)
    broken_json = sum(1 for r in rows if r is None)
    bad_cmd = 0
    bad_any = 0
    by_source: Counter[str] = Counter()
    src_total: Counter[str] = Counter()
    samples: list[str] = []
    for r in rows:
        if r is None:
            continue
        record_id = str(r.get("record_id") or "")
        source = record_id.split(":", 1)[0] or "(무표기)"
        src_total[source] += 1
        text_all = json.dumps(r, ensure_ascii=False)
        user = next((m.get("content", "") for m in (r.get("messages") or []) if m.get("role") == "user"), "")
        hit = CMD.search(user)
        cmd = (hit.group(1) if hit else "").strip()
        if HANJA.search(cmd):
            bad_cmd += 1
            by_source[source] += 1
            if len(samples) < 5:
                samples.append(f"[{record_id[:44]}] {cmd[:56]}")
        if HANJA.search(text_all):
            bad_any += 1

    print(f"\n== {path}")
    print(f"  총 {total}건 | JSON 깨짐 {broken_json} | 명령 오염 {bad_cmd} ({bad_cmd/total*100:.1f}%) | 레코드 어딘가 오염 {bad_any}")
    if by_source:
        print("  출처별 오염:")
        for src, n in by_source.most_common():
            print(f"    {src}: {n}/{src_total[src]} ({n/src_total[src]*100:.0f}%)")
    for s in samples:
        print(f"    예: {s}")


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]
    if not args:
        args = sorted(Path("../datasets/train").glob("*.jsonl")) or sorted(Path("datasets/train").glob("*.jsonl"))
    if not args:
        print("입력 jsonl이 없습니다.")
        return
    for path in args:
        audit(path)


if __name__ == "__main__":
    main()
