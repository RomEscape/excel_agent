"""대화 러너 로그(_log.json) + chat_log.jsonl 을 합쳐 실패 턴을 경로와 함께 보여 준다.

사용: PYTHONUTF8=1 python scripts/dialogue_failures.py <dialogue_exN_log.json> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CHAT_LOG = Path(__file__).resolve().parents[2] / "logs" / "chat_log.jsonl"


def _records(session_prefix: str) -> list[dict]:
    out = []
    if not CHAT_LOG.exists():
        return out
    for line in CHAT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if str(rec.get("session_id") or "").startswith(session_prefix) and rec.get("endpoint") == "excel-live/command":
            out.append(rec)
    return out


def main() -> None:
    for arg in sys.argv[1:]:
        p = Path(arg)
        log = json.loads(p.read_text(encoding="utf-8"))
        stem = p.stem.replace("_log", "")
        recs = _records(f"test-dialogue-{stem}")
        by_msg: dict[str, list[dict]] = {}
        for r in recs:
            by_msg.setdefault(str(r.get("message") or ""), []).append(r)
        fails = [t for t in log if not t.get("ok")]
        print(f"# {p.name}: {sum(1 for t in log if t.get('ok'))}/{len(log)} · 실패 {len(fails)}")
        for t in fails:
            text = str(t.get("text") or "")
            last_line = text.split("\n")[-1]
            cands = by_msg.get(last_line) or [r for r in recs if last_line[:12] and last_line[:12] in str(r.get("message") or "")]
            rec = cands[-1] if cands else None
            print(f"- [{t['turn']}] ({t.get('zone')}) “{last_line[:90]}” → {t.get('why')} · {t.get('action')} · ctx={t.get('context_range')}")
            print(f"    답변: {str(t.get('reply') or '')[:160]}")
            if rec:
                hops = " → ".join(str(x.get("at")) for x in rec.get("routes", []))
                rules = next((s for s in rec.get("stages", []) if s.get("stage") == "rules"), {})
                planner = next((s for s in rec.get("stages", []) if s.get("stage") == "planner"), {})
                acts = [str(x.get("action", "")) for x in (planner.get("action_plan") or []) if isinstance(x, dict)]
                qr = next((x for x in rec.get("routes", []) if str(x.get("at", "")).startswith("quick_rule")), {})
                print(f"    경로: {hops} | hook={rules.get('hook')} reason={qr.get('reason')} | 모델계획={acts}")
                binder = next((s for s in rec.get("stages", []) if s.get("stage") == "binder"), {})
                if binder.get("validation_error"):
                    print(f"    검증오류: {str(binder.get('validation_error'))[:140]}")
        print()


main()
