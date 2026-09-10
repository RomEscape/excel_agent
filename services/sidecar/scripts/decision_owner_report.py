"""판단 주체 분포 — chat_log 의 판단 턴마다 최종 결정을 누가 했는지 센다.

"AI 가 메인인가"를 재는 표다(2026-09-10 사용자 질문 "AI가 메인이 되는 작업인지에 대해서도
점검 중인거야?"). 규칙만(모델 호출 0) / 의도층 해석 / 플래너 계획·되묻기 로 가른다.
2026-09-10 실측(파괴 게이트 72 판단 턴): 2단계 꺼짐 = 규칙만 52.8%, 켜짐 = 30.6%.
3단계(도구 호출 루프)가 붙으면 "모델이 도구 호출" 줄이 생겨야 한다.

    python scripts/decision_owner_report.py nightly-guard-2026-09-10_2324 ui:2026-09-10
    python scripts/decision_owner_report.py --log <다른 chat_log.jsonl> <세션 태그> ...

실행 턴(approval:resumed)·프론트 사건(client:*)은 판단이 아니라 뺀다.
`ui:YYYY-MM-DD` 는 그날의 GUI 실사용 세션(`excel-live::ui::…`)을 뜻한다.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

_EXECUTION_PREFIXES = ("approval:resumed",)


def decider(turn: dict) -> str | None:
    routes = [(r.get("at", "") if isinstance(r, dict) else str(r)) for r in (turn.get("routes") or [])]
    if routes and routes[0].startswith(_EXECUTION_PREFIXES):
        return None
    if any(r.startswith("client:") for r in routes):
        return None
    calls = [
        str(s.get("purpose") or "?")
        for s in (turn.get("stages") or [])
        if isinstance(s, dict) and (s.get("stage") or s.get("name")) == "llm_call"
    ]
    joined = " ".join(routes)
    if "quick_rule:hit" in joined:
        return "규칙만(빠른 규칙 적중, 모델 호출 0)"
    if not calls:
        return "규칙만(모델 호출 0, 규칙 폴백)"
    if any("planner" in c for c in calls):
        return "플래너 → 되묻기" if "asked_back" in joined else "플래너가 계획"
    if any("intent" in c for c in calls):
        if "intent_first_rule_plan" in joined or "intent_first_fallback" in joined:
            return "의도층 해석 → 규칙이 계획"
        return "의도층 해석 → 계획 확정"
    return "모델 호출 있음(" + ",".join(calls) + ")"


def _matches(tag: str, session_id: str, at: str) -> bool:
    if tag.startswith("ui:"):
        return session_id.startswith("excel-live::ui::") and at.startswith(tag[3:])
    return tag in session_id


def main() -> None:
    argv = sys.argv[1:]
    if argv[:1] == ["--log"]:
        path, tags = argv[1], argv[2:]
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from office_claw_sidecar.config import get_chat_log_path

        path, tags = str(get_chat_log_path()), argv
    if not tags:
        print("세션 태그를 하나 이상 적는다 (예: nightly-guard-2026-09-10_2324, ui:2026-09-10)")
        return
    counts: dict[str, collections.Counter] = {tag: collections.Counter() for tag in tags}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                turn = json.loads(line)
            except Exception:
                continue
            if not isinstance(turn, dict) or "turn_id" not in turn:
                continue
            session_id, at = str(turn.get("session_id", "")), str(turn.get("at", ""))
            for tag in tags:
                if not _matches(tag, session_id, at):
                    continue
                owner = decider(turn)
                if owner:
                    counts[tag][owner] += 1
    for tag, counter in counts.items():
        total = sum(counter.values())
        print(f"\n== {tag} — 판단 턴 {total}")
        for owner, n in counter.most_common():
            print(f"  {n:4d} ({n / (total or 1) * 100:5.1f}%)  {owner}")


if __name__ == "__main__":
    main()
