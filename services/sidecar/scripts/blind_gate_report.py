"""블라인드 게이트 보고서 — 결과 JSON + chat_log.jsonl(판단 경로)을 합쳐 실패를 분류한다.

사용: PYTHONUTF8=1 python scripts/blind_gate_report.py ../../datasets/eval/blind_paraphrases_v1_report.json [--pairs out.jsonl]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CHAT_LOG = HERE.parent / "logs" / "chat_log.jsonl"


def _load_routes() -> dict[str, list[dict]]:
    """세션(test-blind-N) → command 레코드들(시간순)."""
    out: dict[str, list[dict]] = defaultdict(list)
    if not CHAT_LOG.exists():
        return out
    for line in CHAT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        sid = str(rec.get("session_id") or "")
        if sid.startswith("test-blind-") and rec.get("endpoint") == "excel-live/command":
            out[sid].append(rec)
    return out


def _route_summary(rec: dict | None) -> str:
    if not rec:
        return "(로그 없음)"
    hops = [str(r.get("at", "")) for r in rec.get("routes", [])]
    llm = [s for s in rec.get("stages", []) if s.get("stage") == "llm_call"]
    planner = next((s for s in rec.get("stages", []) if s.get("stage") == "planner"), {})
    actions = [str(p.get("action", "")) for p in (planner.get("action_plan") or []) if isinstance(p, dict)]
    return " → ".join(hops) + (f" | 모델 {len(llm)}회 → {actions}" if llm else "")


def main() -> None:
    report = Path(sys.argv[1])
    rows = json.loads(report.read_text(encoding="utf-8"))
    routes = _load_routes()
    n = len(rows)
    c = Counter(r["outcome"] for r in rows)
    silent = [r for r in rows if r["outcome"] == "WRONG" and not r.get("card")]
    print(f"# 블라인드 게이트 — {report.name} · 문장 {n}")
    print(f"정답 실행 {c['PASS_RULE'] + c['PASS_CARD']} ({(c['PASS_RULE'] + c['PASS_CARD']) / n:.1%}) = 규칙 {c['PASS_RULE']} + 카드 {c['PASS_CARD']}"
          f" · 되묻기 {c['ASK']} ({c['ASK'] / n:.1%}) · 오실행 {c['WRONG']} ({c['WRONG'] / n:.1%}, 조용한 {len(silent)} = {len(silent) / n:.1%}) · 오류 {c['ERROR']}")
    print("\n## 페르소나별 (정답/되묻기/오실행/오류)")
    per = defaultdict(Counter)
    for r in rows:
        per[r.get("persona", "?")][r["outcome"]] += 1
    for p, cc in per.items():
        tot = sum(cc.values())
        print(f"  {p:8s} {cc['PASS_RULE'] + cc['PASS_CARD']:3d} ({(cc['PASS_RULE'] + cc['PASS_CARD']) / tot:.0%}) / {cc['ASK']:3d} / {cc['WRONG']:3d} / {cc['ERROR']:3d}")
    print("\n## 과제별 (정답 규칙+카드 / 되묻기 / 오실행 / 오류) — 나쁜 순")
    pt = defaultdict(Counter)
    for r in rows:
        pt[r["task"]][r["outcome"]] += 1
    for t, cc in sorted(pt.items(), key=lambda kv: -(kv[1]["WRONG"] * 2 + kv[1]["ERROR"] * 2 + kv[1]["ASK"])):
        print(f"  {t:20s} {cc['PASS_RULE']:2d}+{cc['PASS_CARD']:<2d} / {cc['ASK']:2d} / {cc['WRONG']:2d} / {cc['ERROR']:2d}")
    print("\n## 오실행·오류 전수 (문장 → 판단 경로 → 무엇이 틀렸나)")
    for r in rows:
        if r["outcome"] not in {"WRONG", "ERROR"}:
            continue
        rec = (routes.get(f"test-blind-{r['idx']}") or [None])[-1]
        tag = "조용함" if (r["outcome"] == "WRONG" and not r.get("card")) else r["outcome"]
        print(f"- [{r['task']}] ({r.get('persona')}) “{r['text']}” → {tag}: {r['detail'][:90]}")
        print(f"    경로: {_route_summary(rec)}")
    print("\n## 되묻기 전수 (정당한 질문인지 이해 실패인지 사람이 봐야 함)")
    for r in rows:
        if r["outcome"] == "ASK":
            print(f"- [{r['task']}] “{r['text']}” → {r['detail'][:80]}")
    if "--pairs" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--pairs") + 1])
        tasks = {t["task"]: t for t in json.loads((HERE.parent / "datasets/eval/blind_tasks_v1.json").read_text(encoding="utf-8"))}
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                t = tasks.get(r["task"])
                if not t:
                    continue
                f.write(json.dumps({
                    "input": r["text"], "output": t["canonical"], "task": r["task"],
                    "persona": r.get("persona"), "outcome": r["outcome"], "source": "blind_v1",
                }, ensure_ascii=False) + "\n")
        print(f"\n정준 번역 쌍 {len(rows)}개 → {out}")


main()
