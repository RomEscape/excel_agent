# -*- coding: utf-8 -*-
"""말투 게이트 부분 실행 — BLIND_ONLY 과제만, RunLock을 쥐고.

전체 624는 5시간이라 수정 사이클마다 못 돌린다. 실패 과제만 골라 도는
통합 검출기 — 함수 핀이 못 잡는 부류(실행기 계약·가드 순서)를 잡는 용도.
사용: python scripts/run_blind_subset.py rename_sheet,cross_sheet_sum <라벨>
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _run_lock import RunLock  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "services" / "sidecar"
PY = Path(os.environ["LOCALAPPDATA"]) / "officeclaw/venvs/python-sidecar/Scripts/python.exe"
CASES = ROOT / "datasets/eval/blind_paraphrases_v1.jsonl"
REPORT = CASES.with_name(CASES.stem + "_report.json")


def main() -> int:
    only = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else "subset"
    out_dir = ROOT / "logs/measurements" / f"blind-subset-{dt.date.today():%m%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with RunLock(f"blind-subset-{label}") as lock:
        if not lock.acquired:
            print(f"락 획득 실패 — 쥔 쪽: {lock.held_by}", flush=True)
            return 3
        env = dict(os.environ)
        env.update(
            PYTHONUTF8="1",
            EXCEL_LIVE_ENGINE="file",
            BLIND_ONLY=only,
            BLIND_SESSION_TAG=label,
            OFFICECLAW_INTENT_FIRST="1",
        )
        log_path = out_dir / f"{label}.txt"
        with log_path.open("w", encoding="utf-8") as fh:
            code = subprocess.run(
                [str(PY), "-u", "scripts/run_blind_paraphrase_gate.py", str(CASES)],
                cwd=SIDECAR, env=env, stdout=fh, stderr=subprocess.STDOUT,
            ).returncode
        if code == 0 and REPORT.exists():
            shutil.copy2(REPORT, out_dir / f"{label}_report.json")
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-14:]:
            print(line, flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
