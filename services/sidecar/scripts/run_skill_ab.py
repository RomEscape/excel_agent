# -*- coding: utf-8 -*-
"""스킬 A/B — **학습 없이 스킬 텍스트만 넣었을 때 성능이 오르는가**를 잰다.

두 팔은 코드·각본·모델이 전부 같고 **주입 여부만** 다르다.

    A팔(off): `active_prompt` 를 비운다 → 플래너 프롬프트에 `추가 지침` 절이 없다
    B팔(on) : `docs/excel-agent-skill.md` 의 `## 주입 본문` 을 넣는다

## 왜 팔마다 조건을 다시 확인하나

CLAUDE.md §3.6 — 팔을 비교하기 전에 세 팔이 같은 조건인지 본다. 이 실험은 특히
조용히 무의미해지기 쉽다: 주입이 안 닿으면 두 팔이 같은 프롬프트를 받는데
결과만 흔들려 "효과 없음"으로 보인다. 그래서 팔마다 **실제 프롬프트에 절이
있는지/없는지**를 먼저 단언하고, 아니면 즉시 멈춘다.

사용:
    python scripts/run_skill_ab.py seedA seedB seedC seedD seedE macro
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _console import force_utf8  # noqa: E402

force_utf8()

HERE = Path(__file__).resolve().parent
SIDECAR = HERE.parent
SCENARIO_DIR = SIDECAR / "scenarios" / "dialogue"
OUT_DIR = Path(os.environ.get("SKILL_AB_OUT", str(SIDECAR.parents[1] / "logs" / "skill_ab")))

SUCCESS = re.compile(r"^성공 (\d+) / (\d+)$", re.MULTILINE)


def _apply(mode: str, scenarios: list[str]) -> None:
    cmd = [sys.executable, str(HERE / "apply_agent_skill.py"), f"--{mode}", *scenarios]
    subprocess.run(cmd, check=True, cwd=str(SIDECAR))


def _arm_hint(scenario: str) -> str:
    """그 각본의 세션 키가 실제로 받는 개인화 힌트."""
    from office_claw_sidecar.services.user_harness_service import (
        build_personalization_prompt,
        resolve_user_key,
    )

    key = resolve_user_key({"session_id": f"test-dialogue-dialogue_{scenario}-r1"})
    return build_personalization_prompt(key)


def _skill_injected(scenario: str) -> bool:
    """스킬 본문이 실제로 프롬프트에 실렸는가.

    절 제목(`추가 지침(Persona memory)`)의 유무로 보면 안 된다 — `active_prompt` 가
    비면 시스템이 **기본 후보 힌트**(35자, "모호하면 잘못 실행보다 후속 질문을
    우선한다")로 채우기 때문에 A팔에도 절은 남는다(2026-09-08 실측). 그러니
    A팔의 기준선은 '힌트 없음'이 아니라 **그 기본 힌트**이고, 두 팔을 가르는 것은
    스킬 본문 자체의 유무다."""
    from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt

    from apply_agent_skill import skill_body

    hint = _arm_hint(scenario)
    prompt = build_planner_prompt("나머지도 채워줘", context={"personalization_hint": hint})
    # 본문 앞 60자면 다른 문구와 섞일 일이 없다(짧게 봐야 1200자 절단에 안 걸린다).
    return skill_body()[:60] in prompt


def _run_scenario(scenario: str, arm: str) -> dict:
    path = SCENARIO_DIR / f"dialogue_{scenario}.json"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 게이트·야간 배터리와 같은 엔진으로 고정한다 — 안 맞추면 xlwings(COM) 경로를
    # 타서 기준선과 비교가 무효가 된다(2026-09-08 실측: 3시간 멈춤의 유력 원인).
    env["EXCEL_LIVE_ENGINE"] = "file"

    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(HERE / "run_dialogue.py"), str(path)],
        cwd=str(SIDECAR), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - started

    found = SUCCESS.search(proc.stdout or "")
    ok, total = (int(found.group(1)), int(found.group(2))) if found else (0, 0)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{arm}_{scenario}.stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    log = SCENARIO_DIR / f"dialogue_{scenario}_log.json"
    if log.exists():
        shutil.copy2(log, OUT_DIR / f"{arm}_{scenario}_log.json")

    return {
        "scenario": scenario, "arm": arm, "ok": ok, "total": total,
        "returncode": proc.returncode, "seconds": round(elapsed, 1),
        "parsed": found is not None,
    }


def run_arm(arm: str, scenarios: list[str]) -> dict:
    _apply("clear" if arm == "off" else "apply", scenarios)

    # 팔이 실제로 달라졌는지 단언한다 — 아니면 재 봐야 아무 의미가 없다.
    want = arm == "on"
    for scenario in scenarios:
        got = _skill_injected(scenario)
        if got != want:
            raise SystemExit(
                f"!! {arm}팔 조건 불일치: {scenario} 의 프롬프트에 스킬 본문이 "
                f"{'있어야' if want else '없어야'} 하는데 {'있음' if got else '없음'}"
            )
    sample = _arm_hint(scenarios[0])
    print(
        f"[{arm}] 조건 확인 완료 — 스킬 본문 {'있음' if want else '없음'} "
        f"· 이 팔이 받는 힌트 {len(sample)}자",
        flush=True,
    )
    return_hint = sample

    rows = []
    for scenario in scenarios:
        row = _run_scenario(scenario, arm)
        rows.append(row)
        print(
            f"[{arm}] {scenario:8} {row['ok']:3d}/{row['total']:<3d} "
            f"({row['seconds']}s, rc={row['returncode']})",
            flush=True,
        )
    return {
        "arm": arm,
        "rows": rows,
        "ok": sum(r["ok"] for r in rows),
        "total": sum(r["total"] for r in rows),
        # 기준선이 무엇이었는지 기록에 남긴다 — 나중에 "A팔은 힌트가 없었다"고
        # 잘못 읽지 않도록.
        "hint_sample": return_hint,
    }


def main() -> int:
    scenarios = sys.argv[1:] or ["seedA", "seedB", "seedC", "seedD", "seedE", "macro"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    result = {"scenarios": scenarios, "arms": {}}
    for arm in ("off", "on"):
        result["arms"][arm] = run_arm(arm, scenarios)

    off, on = result["arms"]["off"], result["arms"]["on"]
    print()
    print(f"{'각본':10} {'A(스킬없음)':>12} {'B(스킬있음)':>12}   차이")
    for a, b in zip(off["rows"], on["rows"]):
        delta = b["ok"] - a["ok"]
        print(f"{a['scenario']:10} {a['ok']:>7}/{a['total']:<4} {b['ok']:>7}/{b['total']:<4}   {delta:+d}")
    print(f"{'합계':10} {off['ok']:>7}/{off['total']:<4} {on['ok']:>7}/{on['total']:<4}   {on['ok'] - off['ok']:+d}")

    # 실험 뒤에는 반드시 걷어 낸다 — 심어 둔 채로 두면 다음 측정이 전부 오염된다.
    _apply("clear", scenarios)
    print("\n주입 걷어냄(다음 측정 오염 방지)")

    (OUT_DIR / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"요약: {OUT_DIR / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
