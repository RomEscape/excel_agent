"""야간 게이트 — pytest · 파괴 게이트 72 · 말투 게이트 624를 순차로 돌리고 기준선과 견준다.

    & $PY scripts/nightly_gates.py            # 전부
    & $PY scripts/nightly_gates.py --only guard
    & $PY scripts/nightly_gates.py --update-baseline   # 좋아진 값을 기준선으로 승격

왜 필요한가(2026-08-22 결정 5): 게이트는 8/19~20에 다 만들었는데 **사람이 손으로 돌린다.**
그 탓에 8/22에는 환경이 나빴던 시간대의 값(83.3%)을 회귀로 오진해 세 시간을 썼다.
기준선이 매일 자동으로 갱신되면 그런 낭비가 없다.

판정: 기준선보다 **나빠지면** 종료코드 1. 좋아지는 건 막지 않는다(기준선 승격은 사람이 한다).
결과는 `logs/nightly/<날짜>.md`와 `logs/nightly/LATEST.md`에 남는다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _run_lock import RunLock

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "python-sidecar"
PY = Path(
    os.environ.get("OFFICECLAW_PY")
    or (Path(os.environ["LOCALAPPDATA"]) / "officeclaw/venvs/python-sidecar/Scripts/python.exe")
)
BASELINE = ROOT / "config/gate_baseline.json"
OUT_DIR = ROOT / "logs/nightly"

GATES = {
    "guard": {"이름": "파괴 게이트", "cases": "datasets/eval/guard_cases_v1.jsonl"},
    "blind": {"이름": "말투 게이트", "cases": "datasets/eval/blind_paraphrases_v1.jsonl"},
}
PASSED = {"PASS_RULE", "PASS_CARD"}


def _env(tag: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", EXCEL_LIVE_ENGINE="file", BLIND_SESSION_TAG=tag)
    return env


def _run(cmd: list[str], *, env: dict[str, str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(cmd, cwd=SIDECAR, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode


def _read_report(cases: Path) -> dict[str, int]:
    """게이트 보고서(JSON)에서 결과를 센다. stdout 정규식보다 이쪽이 흔들리지 않는다."""
    report = cases.with_name(cases.stem + "_report.json")
    rows = json.loads(report.read_text(encoding="utf-8"))
    outcomes = Counter(str(r.get("outcome") or "") for r in rows)
    return {
        "총문장": len(rows),
        "pass": outcomes["PASS_RULE"] + outcomes["PASS_CARD"],
        "ask": outcomes["ASK"],
        "wrong": outcomes["WRONG"],
        "error": outcomes["ERROR"],
        "silent": sum(1 for r in rows if r.get("outcome") == "WRONG" and not r.get("card")),
    }


def _failures_of(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        if r.get("outcome") in {"WRONG", "ERROR"}:
            out.append(f"[{r.get('task')}] {str(r.get('text'))[:46]} → {str(r.get('detail'))[:64]}")
    return out


def run_pytest(stamp: str) -> dict:
    log = OUT_DIR / f"{stamp}-pytest.txt"
    t0 = time.time()
    code = _run([str(PY), "-m", "pytest", "-q"], env=_env("nightly"), log=log)
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
    return {"코드": code, "요약": (tail[0] if tail else "").strip(), "초": round(time.time() - t0)}


def run_gate(key: str, stamp: str) -> dict:
    cases = ROOT / GATES[key]["cases"]
    log = OUT_DIR / f"{stamp}-{key}.txt"
    t0 = time.time()
    code = _run(
        [str(PY), "-u", "scripts/run_blind_paraphrase_gate.py", str(cases)],
        env=_env(f"nightly-{key}-{stamp}"),
        log=log,
    )
    try:
        counts = _read_report(cases)
        rows = json.loads(cases.with_name(cases.stem + "_report.json").read_text(encoding="utf-8"))
        counts["실패목록"] = _failures_of(rows)
    except Exception as exc:  # 보고서를 못 읽으면 그것 자체가 실패다
        counts = {"오류": f"{type(exc).__name__}: {exc}"}
    counts["코드"] = code
    counts["초"] = round(time.time() - t0)
    return counts


def judge(results: dict, baseline: dict) -> list[str]:
    """기준선보다 나빠진 것만 돌려준다. 좋아진 건 막지 않는다."""
    bad: list[str] = []
    if "pytest" in results:
        want = int(baseline["pytest"]["failures_max"])
        if results["pytest"]["코드"] != 0:
            bad.append(f"pytest 실패 (기준: 실패 {want}건 이하) — {results['pytest']['요약']}")
    for key in ("guard", "blind"):
        got, base = results.get(key), baseline.get(key)
        if not got or not base:
            continue
        if "오류" in got:
            bad.append(f"{GATES[key]['이름']} 보고서를 못 읽음 — {got['오류']}")
            continue
        if got["pass"] < base["pass_min"]:
            bad.append(f"{GATES[key]['이름']} 정답 {got['pass']} < 기준 {base['pass_min']}")
        if got["wrong"] > base["wrong_max"]:
            bad.append(f"{GATES[key]['이름']} 오실행 {got['wrong']} > 기준 {base['wrong_max']}")
        if got["silent"] > base["silent_max"]:
            bad.append(f"{GATES[key]['이름']} **조용한 오실행 {got['silent']}건** (기준 0)")
    return bad


def render(results: dict, baseline: dict, bad: list[str], stamp: str) -> str:
    lines = [f"# 야간 게이트 {stamp}", ""]
    if bad:
        lines += ["## ❌ 기준선보다 나빠졌다 — **다음 작업의 첫 항목**", ""]
        lines += [f"- {b}" for b in bad]
    else:
        lines += ["## ✅ 기준선 유지", ""]
    lines += ["", "| 항목 | 결과 | 기준선 | 걸린 시간 |", "|---|---|---|---|"]
    if "pytest" in results:
        p = results["pytest"]
        lines.append(f"| pytest | {p['요약'] or '(요약 없음)'} | 실패 0 | {p['초']}초 |")
    for key in ("guard", "blind"):
        got, base = results.get(key), baseline.get(key)
        if not got or "오류" in got:
            if got:
                lines.append(f"| {GATES[key]['이름']} | 오류: {got['오류']} | — | {got.get('초', 0)}초 |")
            continue
        pct = 100 * got["pass"] / max(got["총문장"], 1)
        lines.append(
            f"| {GATES[key]['이름']} {got['총문장']}문장 | {got['pass']} ({pct:.1f}%) · "
            f"되묻기 {got['ask']} · 오실행 {got['wrong']} · 조용한 오실행 **{got['silent']}** · 오류 {got['error']} "
            f"| 정답 {base['pass_min']}↑ · 오실행 {base['wrong_max']}↓ · 조용한 오실행 0 "
            f"| {got['초']}초 |"
        )
    for key in ("guard", "blind"):
        got = results.get(key) or {}
        fails = got.get("실패목록") or []
        if fails:
            lines += ["", f"### {GATES[key]['이름']} 실패 {len(fails)}건", ""]
            lines += [f"- `{f}`" for f in fails[:20]]
            if len(fails) > 20:
                lines.append(f"- … 그 외 {len(fails) - 20}건 (로그 참조)")
    lines += ["", f"로그: `logs/nightly/{stamp}-*.txt`", ""]
    return "\n".join(lines)


def _last_real_report(*, exclude: str) -> Path | None:
    """가장 최근의 **실제로 돈** 보고서. 건너뜀 쪽지와 LATEST는 뺀다."""
    reports = [
        p
        for p in OUT_DIR.glob("20*.md")
        if not p.stem.endswith("-skipped") and not p.stem.startswith(exclude)
    ]
    return max(reports, key=lambda p: p.stat().st_mtime, default=None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["pytest", "guard", "blind"], action="append")
    ap.add_argument("--update-baseline", action="store_true", help="좋아진 값을 기준선으로 승격")
    args = ap.parse_args()
    want = set(args.only or ["pytest", "guard", "blind"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RunLock("nightly-gates") as lock:
        if not lock.acquired:
            # 조용히 사라지면 아침에 낡은 보고서를 새 것으로 착각한다. 비켰다는 사실을 남긴다.
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
            note = "\n".join(
                [
                    f"# 야간 게이트 {stamp}",
                    "",
                    "## ⏭ 건너뜀 — 다른 긴 실행이 돌고 있었다",
                    "",
                    f"- 자물쇠를 쥔 쪽: `{lock.held_by}`",
                    "- 게이트와 배터리는 동시에 돌면 결과가 뒤섞인다(CLAUDE.md §2).",
                    r"- 그쪽이 끝난 뒤 손으로 돌리려면: `.\scripts\nightly-gates.ps1`",
                    "",
                ]
            )
            (OUT_DIR / f"{stamp}-skipped.md").write_text(note, encoding="utf-8")
            # **직전 실제 결과를 아래에 붙인다.** 건너뜀 쪽지로 덮어 버리면 아침에
            # "게이트가 통과했다"는 사실을 못 본다(2026-08-23 실측: 01:19에 통과한
            # 보고서를 03:00 건너뜀이 덮었다 — 내가 만든 것에서 바로 나온 결함이다).
            previous = _last_real_report(exclude=stamp)
            merged = note if previous is None else (
                note + "\n---\n\n## 직전 실제 실행\n\n" + previous.read_text(encoding="utf-8")
            )
            (OUT_DIR / "LATEST.md").write_text(merged, encoding="utf-8")
            print(merged)
            return 2
        if not PY.exists():
            print(f"파이썬을 찾을 수 없습니다: {PY}", file=sys.stderr)
            return 2
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        results: dict = {}
        if "pytest" in want:
            results["pytest"] = run_pytest(stamp)
        for key in ("guard", "blind"):
            if key in want:
                results[key] = run_gate(key, stamp)

        bad = judge(results, baseline)
        text = render(results, baseline, bad, stamp)
        (OUT_DIR / f"{stamp}.md").write_text(text, encoding="utf-8")
        (OUT_DIR / "LATEST.md").write_text(text, encoding="utf-8")
        print(text)

        if args.update_baseline and not bad:
            for key in ("guard", "blind"):
                got = results.get(key)
                if got and "오류" not in got:
                    baseline[key]["pass_min"] = max(baseline[key]["pass_min"], got["pass"])
                    baseline[key]["wrong_max"] = min(baseline[key]["wrong_max"], got["wrong"])
                    baseline[key]["_실측"] = f"{got['pass']}/{got['총문장']} · 오실행 {got['wrong']}"
            baseline["_기준일"] = stamp[:10]
            BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("기준선을 갱신했습니다.")
        return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
