"""같은 명령을 여러 번 돌린 턴 로그를 케이스 단위로 접는다.

`trace_report`는 턴 **하나**를 펼쳐 보여 준다. 실패한 명령을 들여다볼 때는 그게 맞다.
하지만 "무엇이 왜 안 되는가"를 정하려면 한 번의 실행으로는 부족하다. 같은 문장이
어떤 날은 되고 어떤 날은 안 되면, 그 하나만 봐서는 원인을 코드에서 찾게 된다.
실제 원인은 모델의 변덕일 수 있다.

그래서 이 모듈은 **반복**을 전제로 읽는다. 케이스마다 판정을 모아 놓고 이렇게 가른다.

    항상 됨      — 손댈 것 없음
    항상 같게 깨짐 — 결정적 결함. 코드에서 찾으면 나온다
    들쭉날쭉      — 비결정적. 같은 입력에 다른 판정이 나온다

가운데와 아래를 구분하는 것이 핵심이다. 둘은 고치는 방법이 전혀 다르다. 결정적
결함은 재현해서 고치면 끝나지만, 들쭉날쭉한 것은 재현 자체가 운이라 몇 번을 돌려도
"고쳐진 것처럼" 보일 수 있다.

경로(`routes`)도 같이 접는다. 판정이 같아도 경로가 갈리면, 서로 다른 이유로 같은
결말에 도착했다는 뜻이라 하나로 묶어 고치면 안 된다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from office_claw_sidecar.services.trace_report import (
    BROKEN,
    DEFERRED,
    DONE,
    classify,
    goal_missed,
    outcome_class,
    route_path,
)


def case_key(turn: dict[str, Any]) -> str:
    """이 턴이 어느 케이스인가.

    진단 러너가 `source.case`를 붙인다. 없으면 사용자 문장으로 묶는다 — 실제 앱
    트래픽을 분석할 때 그렇게 된다.
    """
    origin = turn.get("source") or {}
    if isinstance(origin, dict) and origin.get("case"):
        return str(origin["case"])
    return str(turn.get("message", "")) or "(빈 명령)"


@dataclass
class CaseDigest:
    """한 케이스를 여러 번 돌린 결과."""

    case: str
    message: str = ""
    verdicts: Counter = field(default_factory=Counter)
    labels: dict[str, str] = field(default_factory=dict)
    details: Counter = field(default_factory=Counter)
    routes: Counter = field(default_factory=Counter)
    actions: Counter = field(default_factory=Counter)
    turn_ids: list[str] = field(default_factory=list)
    elapsed_ms: list[float] = field(default_factory=list)
    # 시스템은 성공이라 했지만 요청한 일을 안 한 횟수.
    missed: Counter = field(default_factory=Counter)

    @property
    def runs(self) -> int:
        return sum(self.verdicts.values())

    @property
    def goal_missed(self) -> bool:
        return bool(self.missed)

    @property
    def dominant(self) -> str:
        """가장 자주 나온 판정 코드."""
        return self.verdicts.most_common(1)[0][0] if self.verdicts else "unknown"

    @property
    def flaky(self) -> bool:
        """같은 입력에 판정이 갈렸는가."""
        return len(self.verdicts) > 1

    @property
    def route_flaky(self) -> bool:
        """판정은 같은데 지나간 경로가 갈렸는가."""
        return not self.flaky and len(self.routes) > 1

    @property
    def done_runs(self) -> int:
        return sum(n for code, n in self.verdicts.items() if outcome_class(code) == DONE)

    @property
    def state(self) -> str:
        """`stable_done` / `stable_deferred` / `stable_broken` / `flaky` / `silent_wrong`.

        `silent_wrong`이 가장 위험하다 — 시스템은 성공을 보고했는데 요청한 일을
        하지 않았다. 사용자도 테스트도 눈치채지 못하므로, 아무리 돌려도 지표는
        깨끗하게 나온다.
        """
        if self.flaky:
            return "flaky"
        klass = outcome_class(self.dominant)
        if klass == DONE and self.goal_missed:
            return "silent_wrong"
        return f"stable_{klass}"

    @property
    def median_ms(self) -> float:
        return round(statistics.median(self.elapsed_ms), 1) if self.elapsed_ms else 0.0

    @property
    def label(self) -> str:
        return self.labels.get(self.dominant, self.dominant)

    @property
    def top_detail(self) -> str:
        return self.details.most_common(1)[0][0] if self.details else ""


@dataclass
class BatteryDigest:
    """케이스 전체를 모은 것."""

    cases: list[CaseDigest] = field(default_factory=list)
    turns: int = 0

    @property
    def by_verdict(self) -> Counter:
        total: Counter = Counter()
        for case in self.cases:
            total.update(case.verdicts)
        return total

    @property
    def by_class(self) -> Counter:
        total: Counter = Counter()
        for code, n in self.by_verdict.items():
            total[outcome_class(code)] += n
        return total

    def in_state(self, state: str) -> list[CaseDigest]:
        return [c for c in self.cases if c.state == state]

    @property
    def flaky(self) -> list[CaseDigest]:
        return self.in_state("flaky")

    @property
    def done_rate(self) -> float:
        """실행된 턴 중 실제로 일이 된 비율. 시스템이 스스로 매긴 판정 기준이다."""
        total = sum(self.by_verdict.values())
        return round(self.by_class[DONE] / total, 4) if total else 0.0

    @property
    def silently_wrong(self) -> int:
        """성공으로 세었지만 요청한 액션을 실행하지 않은 턴 수."""
        return sum(sum(c.missed.values()) for c in self.cases)


def build(turns: list[dict[str, Any]]) -> BatteryDigest:
    """턴 목록을 케이스 단위로 접는다."""
    cases: dict[str, CaseDigest] = {}
    counted = 0
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        counted += 1
        key = case_key(turn)
        digest = cases.get(key)
        if digest is None:
            digest = CaseDigest(case=key, message=str(turn.get("message", "")))
            cases[key] = digest

        verdict = classify(turn)
        digest.verdicts[verdict.code] += 1
        digest.labels[verdict.code] = verdict.label
        if verdict.detail:
            digest.details[verdict.detail] += 1
        digest.routes[route_path(turn) or "(경로 없음)"] += 1
        action = str((turn.get("outcome") or {}).get("action", ""))
        if action:
            digest.actions[action] += 1
        missed = goal_missed(turn)
        if missed:
            digest.missed[missed] += 1
        digest.turn_ids.append(str(turn.get("turn_id", "")))
        try:
            digest.elapsed_ms.append(float(turn.get("elapsed_ms") or 0))
        except (TypeError, ValueError):
            pass

    order = {"silent_wrong": 0, "flaky": 1, f"stable_{BROKEN}": 2, f"stable_{DEFERRED}": 3}
    ordered = sorted(cases.values(), key=lambda c: (order.get(c.state, 9), c.case))
    return BatteryDigest(cases=ordered, turns=counted)


_STATE_LABEL = {
    "silent_wrong": "성공이라 했지만 요청한 일을 안 함",
    "flaky": "들쭉날쭉",
    f"stable_{DONE}": "항상 됨",
    f"stable_{DEFERRED}": "항상 되물음/승인대기",
    f"stable_{BROKEN}": "항상 깨짐",
}
_STATE_ORDER = (
    "silent_wrong",
    "flaky",
    f"stable_{BROKEN}",
    f"stable_{DEFERRED}",
    f"stable_{DONE}",
)


def render(digest: BatteryDigest) -> str:
    """진단 결과를 사람이 읽는 표로."""
    lines: list[str] = []
    total = sum(digest.by_verdict.values())
    by_class = digest.by_class
    lines.append(f"\n  턴 {digest.turns}건 · 케이스 {len(digest.cases)}개")
    lines.append("  " + "─" * 68)
    lines.append(
        f"  실제 이행 {by_class[DONE]}/{total} ({digest.done_rate:.0%})   "
        f"되물음·승인대기 {by_class[DEFERRED]}   깨짐 {by_class[BROKEN]}"
    )
    if digest.silently_wrong:
        lines.append(
            f"  이 중 {digest.silently_wrong}건은 성공으로 셌지만 요청한 액션을 실행하지 않았다"
        )

    for state in _STATE_ORDER:
        group = digest.in_state(state)
        if not group:
            continue
        lines.append("")
        lines.append(f"  {_STATE_LABEL[state]} — {len(group)}개")
        for case in group:
            head = f"    {case.case:<22} {case.runs:>2}회"
            if case.flaky:
                spread = ", ".join(
                    f"{case.labels.get(code, code)}×{n}" for code, n in case.verdicts.most_common()
                )
                lines.append(f"{head}  {spread}")
            else:
                lines.append(f"{head}  {case.label}")
            for action, n in case.missed.most_common():
                lines.append(f"      └ {action} 를 실행하지 않았다 ({n}회) — 검증기는 통과시켰다")
            if case.top_detail:
                lines.append(f"      └ {case.top_detail[:110]}")
            if case.route_flaky:
                lines.append(f"      └ 경로가 {len(case.routes)}가지로 갈림 (판정은 같음)")
    return "\n".join(lines)


def to_report(digest: BatteryDigest) -> dict[str, Any]:
    """JSON으로 보존할 형태. 실행 간 비교에 쓴다."""
    return {
        "turns": digest.turns,
        "cases": len(digest.cases),
        "done_rate": digest.done_rate,
        "silently_wrong": digest.silently_wrong,
        "by_class": dict(digest.by_class),
        "by_verdict": dict(digest.by_verdict),
        "case_detail": [
            {
                "case": c.case,
                "message": c.message,
                "runs": c.runs,
                "state": c.state,
                "verdicts": dict(c.verdicts),
                "detail": c.top_detail,
                "missed_actions": dict(c.missed),
                "routes": dict(c.routes),
                "actions": dict(c.actions),
                "median_ms": c.median_ms,
                "turn_ids": c.turn_ids,
            }
            for c in digest.cases
        ],
    }
