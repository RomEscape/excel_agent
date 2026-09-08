# -*- coding: utf-8 -*-
"""Excel 에이전트 스킬을 프롬프트 슬롯에 심거나 걷어낸다 — A/B 측정용.

`docs/excel-agent-skill.md` 의 `## 주입 본문` 절만 떼어, 각 세션 키의
`learning_state.json` 의 `active_prompt` 로 넣는다. 그러면 플래너 프롬프트의
`추가 지침(Persona memory)` 슬롯에 실린다(`excel_planner_prompt.build_planner_prompt`).

**학습 없이 스킬만으로 성능이 오르는가**를 재려면 같은 코퍼스를 심은 채/걷은 채
두 번 돌려야 한다. 그래서 심기(apply)와 걷기(clear)를 둘 다 제공한다.

사용:
    python scripts/apply_agent_skill.py --apply  seedA seedB seedC seedD seedE
    python scripts/apply_agent_skill.py --clear  seedA seedB seedC seedD seedE
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _console import force_utf8

from office_claw_sidecar.services.user_harness_service import (  # noqa: E402
    _learning_state_path,
    resolve_user_key,
)

force_utf8()

SKILL_DOC = Path(__file__).resolve().parents[3] / "docs" / "excel-agent-skill.md"
SECTION = "## 주입 본문"

#: 제목은 **줄 맨 앞**에서만 인정한다. 문서 서두가 자기 절 이름을 백틱으로 인용하는데,
#: 단순 `find` 는 그 인용을 먼저 집어 **규칙 대신 소개 산문**을 주입한다
#: (2026-09-08 실측: 뽑힌 본문 163자 — 규칙이 한 줄도 없었다). 그러면 두 팔이 사실상
#: 같은 조건이 돼 A/B 가 아무것도 재지 못한다.
_HEADING = re.compile(r"^" + re.escape(SECTION) + r"[ 	]*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^## ", re.MULTILINE)


def skill_body() -> str:
    """스킬 문서에서 주입할 절만 떼어 온다."""
    text = SKILL_DOC.read_text(encoding="utf-8")
    found = _HEADING.search(text)
    if found is None:
        raise SystemExit(f"'{SECTION}' 절을 찾지 못했습니다: {SKILL_DOC}")
    body = text[found.end() :]
    nxt = _NEXT_HEADING.search(body)
    if nxt is not None:
        body = body[: nxt.start()]
    body = body.strip()
    if not body:
        raise SystemExit(f"'{SECTION}' 절이 비어 있습니다: {SKILL_DOC}")
    return body


def session_keys(scenario: str) -> list[str]:
    """러너가 쓰는 세션 id 형식과 같게 만든다(run_dialogue.py: test-dialogue-<stem>-r<N>)."""
    return [
        resolve_user_key({"session_id": f"test-dialogue-dialogue_{scenario}-r{round_no}"})
        for round_no in (1, 2, 3)
    ]


def write_prompt(user_key: str, prompt: str) -> Path:
    path = _learning_state_path(user_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state["active_prompt"] = prompt
    # 품질 게이트를 통과한 것처럼 보이게 하지 않는다 — 이건 실험용 주입이다.
    state.setdefault("quality_gate", {"passed": False, "reason": "experiment", "checked_at": ""})
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="스킬을 심는다")
    mode.add_argument("--clear", action="store_true", help="스킬을 걷어낸다")
    ap.add_argument("scenarios", nargs="+", help="각본 이름(seedA seedB …)")
    args = ap.parse_args()

    body = skill_body() if args.apply else ""
    if args.apply:
        print(f"주입 본문 {len(body)}자 (슬롯 상한 1200자)")
        if len(body) > 1200:
            print("  주의: 1200자를 넘어 뒷부분이 잘립니다(build_personalization_prompt).")

    count = 0
    for scenario in args.scenarios:
        for key in session_keys(scenario):
            write_prompt(key, body)
            count += 1
    print(f"{'심음' if args.apply else '걷어냄'}: 세션 키 {count}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
