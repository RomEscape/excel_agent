# -*- coding: utf-8 -*-
"""야간 드라이버 v3 — 03:00 게이트(A팔)가 끝나면 말투 624를 B팔로 재측정한다.

v2까지의 사고에서 배운 것:
- 락 **존재**가 아니라 **내용**으로 판정한다(2026-08-30 14:43 실측: 러너의
  dialogue-battery 락을 게이트 시작으로 오인해 B팔이 낮에 돌았다).
- 파이프·grep 없이 전량 로그로 남긴다(v1 유령 러너 + 사인 은폐 재발 방지).
- 게이트가 안 오면(05:30까지) 포기하고 그 사실을 로그에 남긴다 — A팔 없이 B만
  돌면 비교 기준이 없다.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _run_lock import LOCK_PATH, RunLock  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "services" / "sidecar"
PY = Path(os.environ["LOCALAPPDATA"]) / "officeclaw/venvs/python-sidecar/Scripts/python.exe"
CASES = ROOT / "datasets/eval/blind_paraphrases_v1.jsonl"
REPORT = CASES.with_name(CASES.stem + "_report.json")
OUT = ROOT / "logs/measurements/intent-first-gate-0831"
DEADLINE_START = dt.datetime.now().replace(hour=5, minute=30, second=0, microsecond=0)
if DEADLINE_START < dt.datetime.now():
    DEADLINE_START += dt.timedelta(days=1)


def say(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def lock_owner() -> str:
    try:
        return LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    say(f"대기 시작 — 게이트 락({LOCK_PATH})의 내용이 'nightly-gates'로 시작할 때만 트리거")

    # 1) 게이트 시작 대기(내용 검사 — 배터리 락엔 무반응)
    while True:
        owner = lock_owner()
        if owner.startswith("nightly-gates"):
            say(f"게이트 시작 감지: {owner}")
            break
        if dt.datetime.now() > DEADLINE_START:
            say("05:30까지 게이트가 시작되지 않았다 — A팔 기준이 없으므로 포기한다")
            return 2
        time.sleep(60)

    # 2) 게이트 종료 대기(락 소멸)
    while lock_owner().startswith("nightly-gates"):
        time.sleep(60)
    say("게이트 종료 감지 — 60초 진정 후 A팔 보고서 보존")
    time.sleep(60)

    # 3) 오늘 새벽 게이트가 남긴 A팔(기본 모드) 보고서를 보존
    if REPORT.exists():
        shutil.copy2(REPORT, OUT / "armA_blind_report.json")
        say(f"A팔 보고서 보존 → {OUT / 'armA_blind_report.json'}")
    else:
        say("경고: A팔 보고서가 없다 — 게이트가 blind 단계 전에 죽었을 수 있다")

    # 4) 우리 락을 쥐고 B팔 실행 (게이트·배터리와 절대 안 겹치게)
    with RunLock("blind-b-remeasure") as lock:
        if not lock.acquired:
            say(f"락 획득 실패 — 쥔 쪽: {lock.held_by}. 포기한다")
            return 3
        env = dict(os.environ)
        env.update(
            PYTHONUTF8="1",
            EXCEL_LIVE_ENGINE="file",
            BLIND_SESSION_TAG="armB5-remeasure-0831",
            OFFICECLAW_INTENT_FIRST="1",
        )
        log_path = OUT / "armB5_blind.txt"
        say(f"B팔(INTENT_FIRST=1) 624 시작 — 로그 {log_path}")
        with log_path.open("w", encoding="utf-8") as fh:
            code = subprocess.run(
                [str(PY), "-u", "scripts/run_blind_paraphrase_gate.py", str(CASES)],
                cwd=SIDECAR, env=env, stdout=fh, stderr=subprocess.STDOUT,
            ).returncode
        if code != 0:
            say(f"B팔 러너 종료코드 {code} — 로그를 봐야 한다")
            return code
        shutil.copy2(REPORT, OUT / "armB5_blind_report.json")
        say(f"B팔 보고서 보존 → {OUT / 'armB5_blind_report.json'}")

    # 5) 요약 두 줄(전문은 로그 꼬리)
    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in tail[-25:]:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
