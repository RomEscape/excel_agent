# -*- coding: utf-8 -*-
"""배터리가 **실제로 플래너에 보낸 프롬프트**를 그대로 받아 적는다.

## 왜 필요한가

`run_skill_ab.py` 는 주입 여부를 드라이버 프로세스에서 확인하는데, 배터리는 **별도
프로세스**로 돈다. 두 팔의 결과가 바이트 단위로 같게 나왔을 때
"플래너가 스킬을 무시했다"와 "스킬이 런타임엔 안 실렸다"를 가르지 못하면
어느 쪽으로도 결론을 적을 수 없다(2026-09-08).

`excel_live_agent` 가 자기 이름공간으로 들여온 `build_planner_prompt` 를 감싸서,
그 함수가 **돌려준 문자열**을 파일로 흘린다. 실제로 모델에 가는 그 문자열이다.

사용:
    python scripts/probe_planner_prompt.py scenarios/dialogue/dialogue_seedB.json out.jsonl
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _console import force_utf8  # noqa: E402

force_utf8()

SCENARIO = sys.argv[1]
OUT = Path(sys.argv[2])
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("", encoding="utf-8")

from office_claw_sidecar.services import excel_live_agent  # noqa: E402

_original = excel_live_agent.build_planner_prompt


def _recording(message, **kwargs):
    prompt = _original(message, **kwargs)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"message": str(message), "prompt": prompt, "chars": len(prompt)},
                ensure_ascii=False,
            )
            + "\n"
        )
    return prompt


excel_live_agent.build_planner_prompt = _recording

# 러너를 그대로 돌린다 — 인자 모양도 러너가 기대하는 그대로 맞춘다.
sys.argv = ["run_dialogue.py", SCENARIO]
runpy.run_path(str(Path(__file__).resolve().parent / "run_dialogue.py"), run_name="__main__")
