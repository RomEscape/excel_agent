"""트래픽을 만든 주체를 가른다 — 사람 / 테스트 / 프로브.

수확기가 사람이 친 명령과 자동화 트래픽을 섞으면 두 가지가 동시에 망가진다.
모델은 픽스처 문자열(`alpha123`)을 배우고, 평가셋은 자기 테스트를 채점한다.
실제로 `planner_sft_v5_test.jsonl` 34건 중 21건이 pytest 세션이었고, 그 위에서
잰 eval loss는 학습 판단 근거로 쓸 수 없는 값이었다.

두 층으로 막는다.

1. 기록 시점 — `record_user_harness_event`가 `origin`을 함께 남긴다.
   `decision_trace.source(test=...)`가 열려 있으면 테스트로 찍힌다. 앞으로
   쌓이는 이벤트는 스스로 출처를 밝히므로 추정이 필요 없다.
2. 읽는 시점 — `origin`이 없는 과거 이벤트는 `classify()`가 무엇이 만든
   트래픽인지 추정한다. 세션 id와 통합문서 경로가 근거다.

**태그가 없으면 사람으로 치지 않는다.** 처음에는 "자동화 흔적이 없으면 사람"으로
뒀는데, 그 기준으로 `logs/all_events.jsonl` 10,827건을 재분류하니 5,844건이
사람으로 나왔다. 그런데 그 5,844건은 요청 간격 중앙값이 1.7초였고, 4,338건이
스윕 스크립트 전용 폴더인 `C:\\work`를 쓰고 있었으며, 같은 명령이 97~98회씩
반복됐다. 사람이 아니라 반복 실행된 스윕이었다. 근거 없는 관대함이 오염을
그대로 통과시켰으므로, 확인되지 않은 과거 트래픽은 `UNKNOWN`으로 두고 학습에서
제외한다.

수확기는 `is_user_traffic()` 하나만 부르면 된다. 왜 걸렀는지 남기려면
`classify()`가 돌려주는 `Origin.rule`과 `Origin.detail`을 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import decision_trace

USER = "user"
TEST = "test"
PROBE = "probe"
UNKNOWN = "unknown"

_KNOWN_KINDS = frozenset({USER, TEST, PROBE})

# pytest가 쓰는 세션 id. `test_excel_live_router.py`의 `sess-table-fast`,
# `clarify-invalid-plan` 같은 것들이다.
_TEST_SESSION_PREFIXES = ("sess-", "clarify-")

# 프로브·스윕 스크립트(`scripts/probe-live-app.mjs`, `verify_excel_complex_scenarios.py`,
# `smoke_excel_live_nl.py` 등)가 실행 중인 사이드카에 직접 쏘는 트래픽.
_PROBE_SESSION_PREFIXES = (
    "battery-",
    "complex-",
    "openable-",
    "probe-",
    "bench-",
    "smoke-",
    "reset-cycle-",
    "dashboard-",
    "ambiguous-",
)

_TEST_SESSION_WORDS = re.compile(r"(?:^|[-_])(?:test|fake|dummy|fixture|mock)(?:$|[-_])", re.IGNORECASE)

_PROBE_WORKBOOK_MARKER = "officeclaw_battery_"

# 시나리오 스윕 스크립트가 쓰는 고정 작업 폴더. 사람이 여는 문서가 아니다.
_SWEEP_WORKBOOK_DIRS = ("c:/work/",)

# 임시 디렉터리 밑의 통합문서는 사람이 열어 둔 파일이 아니다.
_TEMP_DIR_MARKERS = (
    "/appdata/local/temp/",
    "/windows/temp/",
    "/tmp/",
    "/var/folders/",
)

# `alpha123`, `beta123` 같은 픽스처 토큰. 엑셀 명령이 아니다.
_FIXTURE_TEXT = re.compile(r"^(?:지금\s+)?(?:alpha|beta|gamma|foo|bar|baz|test)\d*$", re.IGNORECASE)


@dataclass(frozen=True)
class Origin:
    """출처와 그렇게 판단한 근거.

    `rule`은 집계용 고정 키다. `detail`에는 세션 id처럼 건마다 달라지는 값이
    들어가므로, 통계를 낼 때 섞으면 키가 건수만큼 늘어난다.
    """

    kind: str
    rule: str
    detail: str = ""

    @property
    def is_user(self) -> bool:
        return self.kind == USER

    @property
    def label(self) -> str:
        return f"{self.kind}/{self.rule}"

    def __str__(self) -> str:
        return f"{self.label}{f' — {self.detail}' if self.detail else ''}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _looks_temporary(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(marker in normalized for marker in _TEMP_DIR_MARKERS)


def current_origin() -> str:
    """지금 처리 중인 요청을 누가 만들었는지. 기록 시점에 쓴다."""
    src = decision_trace.current_source()
    if src.get("test"):
        return TEST
    if src.get("probe") or src.get("bench"):
        return PROBE
    return USER


def classify(payload: Mapping[str, Any]) -> Origin:
    """harness 이벤트 하나의 출처를 판정한다.

    기록 시점에 찍힌 `origin`이 있으면 그것이 우선이고, 사람으로 인정하는 길은
    그것뿐이다. 태그가 없으면 무엇이 만든 트래픽인지 추정만 하고, 끝내 모르면
    `UNKNOWN`으로 둔다 — 모르는 것을 사람으로 세지 않는다.
    """
    declared = _text(payload.get("origin")).lower()
    if declared in _KNOWN_KINDS:
        return Origin(declared, "declared")

    session = _text(payload.get("session_id"))
    lowered_session = session.lower()
    workbook = _text(payload.get("workbook_id"))
    lowered_workbook = workbook.replace("\\", "/").lower()
    message = _text(payload.get("message"))

    if _PROBE_WORKBOOK_MARKER in lowered_workbook:
        return Origin(PROBE, "probe_workbook", "프로브 전용 임시 통합문서")
    if lowered_workbook.startswith(_SWEEP_WORKBOOK_DIRS):
        return Origin(PROBE, "sweep_workbook", "시나리오 스윕 작업 폴더")
    if lowered_session.startswith(_PROBE_SESSION_PREFIXES):
        return Origin(PROBE, "probe_session", f"프로브 세션 id({session})")
    if lowered_session.startswith(_TEST_SESSION_PREFIXES):
        return Origin(TEST, "pytest_session", f"pytest 세션 id({session})")
    if _TEST_SESSION_WORDS.search(session):
        return Origin(TEST, "test_word_session", f"세션 id에 테스트 표시({session})")
    if _looks_temporary(workbook):
        return Origin(TEST, "temp_workbook", "임시 디렉터리 통합문서")
    if _FIXTURE_TEXT.match(message):
        return Origin(TEST, "fixture_text", f"픽스처 문자열({message})")
    return Origin(UNKNOWN, "untagged", "출처 태그가 없어 사람인지 확인 불가")


def is_user_traffic(payload: Mapping[str, Any]) -> bool:
    """학습·평가 데이터로 써도 되는 트래픽인가."""
    return classify(payload).is_user
