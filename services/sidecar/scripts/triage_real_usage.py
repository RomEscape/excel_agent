"""실사용(GUI) 기준 문제 트리아지 — "내가 채팅 치자마자 뭐가 깨졌나"를 한 명령으로.

chat_log.jsonl에는 실사용과 배터리·측정 트래픽이 섞여 있어 "실 사용 기준으로
어디가 문제인지"가 보이지 않았다(2026-08-18 사용자 지적). 앱의 세션 키는
`excel-live::ui::<uuid>` 형식이므로(WorkspacePage.excelSessionKey) 그걸로 가른다.

턴 분류:
  실패      ok=false 또는 failure_detail
  무변화    성공인데 no_matching_cells (정직 보고가 뜬 턴)
  되묻기    ask_follow_up
  안전정지  safety_stop
  채팅전환  not_excel_request
  승인대기  approval_required (1차 — 실행은 후속 턴)
  실행OK    나머지 성공

실행:
  & $PY scripts\\triage_real_usage.py             # 전체 요약 + 최근 문제 10건
  & $PY scripts\\triage_real_usage.py --day 2026-08-17
  & $PY scripts\\triage_real_usage.py --problems 20
  & $PY scripts\\triage_real_usage.py --all-traffic   # 배터리 포함(디버그용)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LOG = Path(__file__).resolve().parents[3] / "logs" / "chat_log.jsonl"
REAL_PREFIX = "excel-live::ui::"


def load_turns(path: Path) -> list[dict]:
    turns = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            turns.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return turns


def classify(turn: dict) -> str:
    out = turn.get("outcome") or {}
    result_ok = out.get("ok")
    action = str(out.get("action") or "")
    if result_ok is False or str(out.get("failure_detail") or "").strip():
        return "실패"
    if action.endswith("safety_stop"):
        return "안전정지"
    if action.endswith("not_excel_request"):
        return "채팅전환"
    if out.get("ask_follow_up"):
        return "되묻기"
    if out.get("approval_required"):
        return "승인대기"
    # 정직 보고: 성공인데 보이는 변화 0 — reason에 안내가 붙는다
    reason = str(out.get("reason") or "")
    if any(k in reason for k in ("없어", "찾지 못해", "지울 값이 없는")):
        return "무변화"
    return "실행OK"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default="", help="YYYY-MM-DD로 필터")
    parser.add_argument("--problems", type=int, default=10, help="문제 턴 상세 개수")
    parser.add_argument("--all-traffic", action="store_true", help="배터리·측정 트래픽 포함")
    args = parser.parse_args()

    turns = load_turns(LOG)
    rows = []
    for t in turns:
        sid = str(t.get("session_id") or "")
        origin = t.get("origin") or {}
        kind = str(origin.get("kind") or "") if isinstance(origin, dict) else ""
        is_real = sid.startswith(REAL_PREFIX) or (
            sid.startswith("excel-live::") and kind in {"user", "approval"}
        )
        if not args.all_traffic and not is_real:
            continue
        at = str(t.get("at") or "")
        if args.day and not at.startswith(args.day):
            continue
        rows.append(t)

    if not rows:
        print("해당 조건의 실사용 턴이 없습니다.")
        return

    print(f"실사용 턴 {len(rows)}건 (로그 전체 {len(turns)}건 중)\n")

    by_day: dict[str, Counter] = defaultdict(Counter)
    by_session_first: Counter = Counter()
    seen_sessions: set[str] = set()
    problems = []
    for t in rows:
        day = str(t.get("at") or "")[:10]
        cls = classify(t)
        by_day[day][cls] += 1
        sid = str(t.get("session_id") or "")
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            by_session_first[cls] += 1
        if cls in {"실패", "무변화"}:
            problems.append((str(t.get("at") or "")[:19], cls, t))

    print("── 일자별")
    for day in sorted(by_day):
        counts = by_day[day]
        total = sum(counts.values())
        bad = counts.get("실패", 0) + counts.get("무변화", 0)
        print(f"  {day}: {total:3d}턴 | {dict(counts)} | 문제율 {bad/total*100:.0f}%")

    print("\n── 세션 첫 턴 결과 (\"채팅 한 번 치자마자\" 지표)")
    first_total = sum(by_session_first.values())
    for cls, n in by_session_first.most_common():
        print(f"  {cls}: {n}/{first_total} ({n/first_total*100:.0f}%)")

    print(f"\n── 최근 문제 턴 {min(args.problems, len(problems))}건")
    for at, cls, t in problems[-args.problems :]:
        out = t.get("outcome") or {}
        msg = str(t.get("user_input") or "")[:44]
        routes = [str(r.get("at")) for r in (t.get("routes") or [])]
        print(f"  [{cls}] {at} | {msg}")
        print(f"      action={str(out.get('action'))[11:]} reason={str(out.get('reason'))[:64]}")
        print(f"      routes={routes}")


if __name__ == "__main__":
    main()
