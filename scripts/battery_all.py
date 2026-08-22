"""대화 배터리 전수 — 42개 각본을 순차로 돌리고 한 장짜리 요약을 남긴다.

    & $PY scripts/battery_all.py                 # 전부(약 9시간)
    & $PY scripts/battery_all.py --only ex14 ex20
    & $PY scripts/battery_all.py --repeat 2

야간 게이트와 **같은 자물쇠**를 쓴다 — 겹치면 결과가 뒤섞인다(CLAUDE.md §2).
9시간짜리라 03:00 게이트와 반드시 겹치는데, 자물쇠 덕에 게이트가 조용히 비켜 간다.

결과: `logs/nightly/battery-<날짜>.md` · `logs/nightly/BATTERY_LATEST.md`
종료코드: 0 전부 통과 · 1 실패 있음 · 2 실행 불가(자물쇠·인터프리터)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _run_lock import RunLock

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "python-sidecar"
SCENARIOS = SIDECAR / "scenarios/dialogue"
OUT_DIR = ROOT / "logs/nightly"
PY = Path(
    os.environ.get("OFFICECLAW_PY")
    or (Path(os.environ["LOCALAPPDATA"]) / "officeclaw/venvs/python-sidecar/Scripts/python.exe")
)
_SUCCESS = re.compile(r"^성공\s+(\d+)\s*/\s*(\d+)", re.MULTILINE)
_FAIL_LINE = re.compile(r"^\[\s*(\d+)\]\s+FAIL(.*)$", re.MULTILINE)


def scenario_names() -> list[str]:
    names = [
        p.stem.replace("dialogue_", "")
        for p in SCENARIOS.glob("dialogue_ex*.json")
        if not p.stem.endswith("_log")
    ]
    # ex2 < ex10 이 되도록 숫자 부분으로 정렬한다.
    def key(name: str) -> tuple[int, str]:
        m = re.match(r"ex(\d+)", name)
        return (int(m.group(1)) if m else 9999, name)

    return sorted(names, key=key)


def run_one(name: str, repeat: int, stamp: str) -> dict:
    path = SCENARIOS / f"dialogue_{name}.json"
    log = OUT_DIR / f"battery-{stamp}-{name}.txt"
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", EXCEL_LIVE_ENGINE="file", HUMAN_REPEAT=str(repeat))
    t0 = time.time()
    with log.open("w", encoding="utf-8") as fh:
        code = subprocess.run(
            [str(PY), "-u", "scripts/run_dialogue.py", str(path)],
            cwd=SIDECAR, env=env, stdout=fh, stderr=subprocess.STDOUT,
        ).returncode
    text = log.read_text(encoding="utf-8", errors="replace")
    m = _SUCCESS.search(text)
    ok, total = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    fails = [f"[{n}]{rest.strip()}" for n, rest in _FAIL_LINE.findall(text)]
    # 공통 불변식 위반(첫 시트 A1 오염)은 러너가 `!!`로 찍는다.
    dirty = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("!!")]
    return {
        "각본": name, "ok": ok, "total": total, "코드": code,
        "실패": fails, "오염": dirty, "초": round(time.time() - t0),
    }


def render(rows: list[dict], stamp: str, elapsed: float) -> str:
    ok = sum(r["ok"] for r in rows)
    total = sum(r["total"] for r in rows)
    bad = [r for r in rows if r["ok"] != r["total"] or r["오염"]]
    pct = 100 * ok / max(total, 1)
    lines = [f"# 대화 배터리 전수 {stamp}", ""]
    lines.append(
        f"## {'❌ 실패 있음 — **다음 작업의 첫 항목**' if bad else '✅ 전부 통과'}"
    )
    lines += ["", f"**{ok} / {total} ({pct:.2f}%)** · 각본 {len(rows)}개 · {elapsed / 3600:.1f}시간", ""]
    if bad:
        lines += ["### 실패한 각본", ""]
        for r in bad:
            lines.append(f"- **{r['각본']}** {r['ok']}/{r['total']}")
            for f in r["실패"][:6]:
                lines.append(f"  - `{f[:150]}`")
            for d in r["오염"]:
                lines.append(f"  - ⚠ 오염 `{d[:150]}`")
        lines.append("")
    lines += ["<details><summary>각본별 전체</summary>", "", "| 각본 | 결과 | 시간 |", "|---|---|---|"]
    for r in rows:
        mark = "" if r["ok"] == r["total"] else " ❌"
        lines.append(f"| {r['각본']} | {r['ok']}/{r['total']}{mark} | {r['초']}초 |")
    lines += ["", "</details>", "", f"로그: `logs/nightly/battery-{stamp}-*.txt`", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="각본 이름(예: ex14 ex20). 없으면 전부")
    ap.add_argument("--repeat", type=int, default=1, help="각본당 라운드 수")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PY.exists():
        print(f"파이썬을 찾을 수 없습니다: {PY}", file=sys.stderr)
        return 2

    with RunLock("battery") as lock:
        if not lock.acquired:
            print(f"이미 도는 중입니다({lock.held_by}). 동시 실행은 결과를 뒤섞습니다.", file=sys.stderr)
            return 2
        names = args.only or scenario_names()
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        t0 = time.time()
        rows: list[dict] = []
        for i, name in enumerate(names, 1):
            row = run_one(name, args.repeat, stamp)
            rows.append(row)
            print(f"[{i:2d}/{len(names)}] {name:10s} {row['ok']}/{row['total']} ({row['초']}초)", flush=True)
            # 한 각본이 끝날 때마다 요약을 갱신한다 — 9시간짜리가 중간에 죽어도 여기까지는 남는다.
            partial = render(rows, stamp, time.time() - t0)
            (OUT_DIR / f"battery-{stamp}.md").write_text(partial, encoding="utf-8")
            (OUT_DIR / "BATTERY_LATEST.md").write_text(partial, encoding="utf-8")
        text = render(rows, stamp, time.time() - t0)
        print(text)
        (OUT_DIR / f"battery-{stamp}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return 1 if any(r["ok"] != r["total"] or r["오염"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
