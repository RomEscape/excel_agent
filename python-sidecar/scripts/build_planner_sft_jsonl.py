"""증류 데이터셋을 **프로덕션 플래너와 동일한 프롬프트**의 SFT 학습 데이터로 변환한다.

v3까지의 문제 두 가지를 여기서 고친다.

1. **통합문서 상태가 비어 있었다.** 추론 시 프롬프트에는 지금 열린 파일의 시트명·머리글이
   1600자까지 붙는데, 학습 데이터 1000건에는 그 블록이 단 한 건도 없었다. 모델은 읽는 법을
   배우지 못했고, 대신 학습 중 가장 자주 본 시트명(`Sales_Data`, 정답의 16.8%)을 뱉었다.
   이제 정답 계획과 아귀가 맞는 통합문서를 레코드마다 합성해 붙이고, 시트 이름도 함께 바꾼다.

2. **되묻기를 한 번도 가르치지 않았다.** 1000건 전부 `follow_up_question`이 빈 문자열이라
   모델은 "무슨 일이 있어도 실행하라"고 배웠다. `--with-clarify`로 되묻기/답변 쌍을 섞는다.

정답(assistant)은 프로덕션 파서가 실제로 읽는 스키마를 그대로 따른다:
    {"intent", "mutates_workbook", "action_plan", "slot_fill",
     "partial_params", "follow_up_question", "reason"}

사용:
    python scripts/build_planner_sft_jsonl.py \
        --input ../datasets/distill/excel_distill_v1_verified_augmented.jsonl \
        --output ../datasets/train/planner_sft_v4_train.jsonl \
        --with-clarify
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_action_coverage_cases import (
    build_action_coverage_records,
)
from office_claw_sidecar.services.excel_clarify_cases import build_clarify_records
from office_claw_sidecar.services.excel_live_plan_validator import (
    EDIT_ACTIONS as VALIDATOR_EDIT_ACTIONS,
)
from office_claw_sidecar.services.excel_live_plan_validator import (
    SUPPORTED_ACTIONS,
)
from office_claw_sidecar.services.excel_planner_prompt import (
    build_planner_prompt,
    render_conversation_history,
)
from office_claw_sidecar.services.excel_workbook_digest import render_workbook_digest
from office_claw_sidecar.services.excel_workbook_fixtures import synthesize_digest

PREFIX = "excel_live."

# intent 판정 기준은 검증기가 단일 소스다. 여기에 목록을 복사해 두면
# 실행 계층이 바뀔 때 조용히 어긋난다 — 실제로 예전 사본에는 실행조차 되지 않는
# "chart"/"sort"가 편집 액션으로 들어가 있었다.
EDIT_ACTIONS = frozenset(name.removeprefix(PREFIX) for name in VALIDATOR_EDIT_ACTIONS)

NAVIGATE_ACTIONS = frozenset(
    {"list_workbooks", "select_workbook", "list_sheets", "select_sheet", "create_sheet"}
)

# 증류 데이터에 섞여 있던 잘못된 액션 이름 → 실제 실행 가능한 액션.
# 원본 티처가 축약형으로 뱉은 것을 그대로 정답으로 굳혀 버린 흔적이다.
ACTION_ALIASES = {
    "formula": "set_formula",
    "chart": "create_chart",
    "sort": "sort_range",
}

# 플래너가 고를 수 있는 액션이 아니라, 계획이 비었을 때 **라우터가 만들어 내는**
# 응답이다. 이걸 정답으로 가르치면 모델은 실행 불가능한 계획을 내놓는다.
# clarify는 이제 정식 액션이라 여기서 빠진다 — 되묻기 데이터는 별도 생성기가 만든다.
ROUTER_FALLBACK_ACTIONS = frozenset({"general", "debug"})


def _short_name(action: str) -> str:
    return action.split(".", 1)[-1] if "." in action else action


def normalize_action(action: str) -> str | None:
    """액션 이름을 실행 가능한 정식 이름으로 맞춘다. 못 살리면 None."""
    short = _short_name(str(action or "").strip())
    if not short:
        return None
    short = ACTION_ALIASES.get(short, short)
    if short in ROUTER_FALLBACK_ACTIONS:
        return None
    full = PREFIX + short
    return full if full in SUPPORTED_ACTIONS else None


def derive_intent(action_plan: list[dict[str, Any]]) -> str:
    """첫 단계의 액션으로 intent를 정한다 (프롬프트 규칙 5·6과 같은 기준)."""
    if not action_plan:
        return "read"
    first = _short_name(str(action_plan[0].get("action", "")))
    if first == "clarify":
        return "clarify"
    if first in EDIT_ACTIONS:
        return "edit"
    if first in NAVIGATE_ACTIONS:
        return "navigate"
    return "read"


def get_instruction(row: dict[str, Any]) -> str:
    """세 입력 스키마 모두에서 원문을 뽑는다."""
    direct = str(row.get("instruction", "")).strip()
    if direct:
        return direct
    return str((row.get("input") or {}).get("instruction", "")).strip()


def build_context(row: dict[str, Any]) -> dict[str, Any]:
    """레코드에 남아 있는 실행 컨텍스트를 플래너 컨텍스트 형태로 복원한다."""
    inp = row.get("input") or {}
    hints = inp.get("context_hints") or row.get("context_hints") or {}
    return {
        "sheet_name": hints.get("sheet_name", "") or "",
        "context_range": hints.get("context_range", "") or "",
        "workbook_id": hints.get("workbook_id", "") or "",
        "reasoning_mode": "fast",
        "complexity_score": 0,
    }


def _normalize_steps(action_plan: Any) -> list[dict[str, Any]] | None:
    """단계 목록을 정규화한다. 살릴 수 없는 액션이 하나라도 있으면 레코드를 버린다.

    일부만 남기면 원래 의도와 다른 계획이 정답으로 남는다.
    (예: 3단계 중 한 단계를 지우면 나머지 2단계가 전체 답인 것처럼 학습된다)
    """
    if not isinstance(action_plan, list) or not action_plan:
        return None

    steps: list[dict[str, Any]] = []
    for raw in action_plan[:4]:
        if not isinstance(raw, dict):
            continue
        action = normalize_action(raw.get("action", ""))
        if action is None:
            return None
        steps.append(
            {
                "action": action,
                "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
                "reason": str(raw.get("reason", "")).strip(),
            }
        )
    return steps or None


def build_target(row: dict[str, Any]) -> dict[str, Any] | None:
    """프로덕션 파서가 읽는 스키마 그대로의 정답 JSON을 만든다."""
    output_json = row.get("output_json")
    if isinstance(output_json, dict) and isinstance(output_json.get("action_plan"), list):
        steps = _normalize_steps(output_json.get("action_plan"))
        if steps is None:
            return None
        return {**output_json, "action_plan": steps}

    target = row.get("target") or {}
    steps = _normalize_steps(target.get("action_plan"))
    if steps is None:
        return None

    intent = derive_intent(steps)
    hints = (row.get("input") or {}).get("context_hints") or {}
    return {
        "intent": intent,
        "mutates_workbook": intent == "edit",
        "action_plan": steps,
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": "",
        "reason": str(hints.get("reason", "")).strip() or "사용자 요청 실행",
    }


def _digest_and_plan(
    row: dict[str, Any], target: dict[str, Any], instruction: str, record_id: str
) -> tuple[str, dict[str, Any]]:
    """프롬프트에 넣을 통합문서 상태 텍스트와, 그에 맞춰 다시 쓴 정답을 만든다."""
    prebuilt = row.get("digest")
    if isinstance(prebuilt, dict) and prebuilt.get("sheets"):
        # 되묻기 생성기처럼 통합문서를 먼저 정하고 질문을 만든 레코드.
        # 여기서 시트를 다시 섞으면 질문에 적힌 머리글과 어긋난다.
        return render_workbook_digest(prebuilt), target

    digest, rewritten_plan = synthesize_digest(
        target["action_plan"], instruction=instruction, seed=record_id
    )
    return render_workbook_digest(digest), {**target, "action_plan": rewritten_plan}


def convert(
    rows: list[dict[str, Any]],
    *,
    planner_model: str,
    only_passed: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    stats = {
        "total": 0,
        "skipped_no_plan": 0,
        "skipped_quality": 0,
        "skipped_invalid_action": 0,
        "converted": 0,
        "with_digest": 0,
        "with_history": 0,
        "clarify_targets": 0,
    }

    for row in rows:
        stats["total"] += 1

        # quality 블록이 없는 스키마는 품질 필터 대상이 아니다.
        quality = row.get("quality")
        if only_passed and isinstance(quality, dict) and not quality.get("passed", False):
            stats["skipped_quality"] += 1
            continue

        instruction = get_instruction(row)
        if not instruction:
            stats["skipped_no_plan"] += 1
            continue

        target = build_target(row)
        if target is None:
            # 계획 자체가 없던 건지, 살릴 수 없는 액션 때문에 버린 건지 구분한다.
            raw_plan = (row.get("output_json") or {}).get("action_plan") or (
                row.get("target") or {}
            ).get("action_plan")
            if isinstance(raw_plan, list) and raw_plan:
                stats["skipped_invalid_action"] += 1
            else:
                stats["skipped_no_plan"] += 1
            continue

        record_id = str(row.get("record_id") or row.get("id", "") or instruction)
        digest_text, target = _digest_and_plan(row, target, instruction, record_id)

        history = row.get("conversation_history") or {}
        history_text = render_conversation_history(
            history.get("original_message", ""), history.get("question", "")
        )

        context = build_context(row)
        context["workbook_digest_text"] = digest_text
        context["conversation_history_text"] = history_text

        prompt = build_planner_prompt(
            instruction,
            context=context,
            planner_model=planner_model,
        )

        if digest_text:
            stats["with_digest"] += 1
        if history_text:
            stats["with_history"] += 1
        if str(target["action_plan"][0]["action"]) == "excel_live.clarify":
            stats["clarify_targets"] += 1

        out.append(
            {
                "record_id": record_id,
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
                ],
            }
        )
        stats["converted"] += 1

    return out, stats


def _first_action_of_row(row: dict[str, Any]) -> str:
    """변환 전 레코드에서 첫 액션 이름을 꺼낸다. 못 꺼내면 빈 문자열."""
    plan = (row.get("output_json") or {}).get("action_plan") or (row.get("target") or {}).get(
        "action_plan"
    )
    if not isinstance(plan, list) or not plan or not isinstance(plan[0], dict):
        return ""
    normalized = normalize_action(plan[0].get("action", ""))
    return normalized or ""


# 되묻기는 "액션 하나"가 아니라 배워야 할 **행동**이다. 애매한 문장 전체를 대표하므로
# 다른 액션과 같은 상한을 걸면 비중이 3%까지 떨어져 모델이 다시 무조건 실행하려 든다.
# 반대로 v4처럼 18%까지 두면 멀쩡한 지시에도 되묻는다. 그 사이를 노린 값이다.
CLARIFY_CAP_MULTIPLIER = 3


def balance_rows(
    rows: list[dict[str, Any]], *, max_per_action: int, seed: int = 5
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """액션별 상한을 걸어 분포를 평평하게 만든다.

    증류 로그에서 뽑은 데이터는 "그때 많이 시킨 작업"으로 쏠려 있다. v4 학습셋은
    pivot_table 161건인데 compare_ranges는 1건이었다. 머릿수가 많은 액션은
    자주 보여서가 아니라 **로그가 편향돼서** 많은 것뿐이라, 그대로 두면 모델이
    애매할 때 무조건 다수 액션으로 끌려간다. 상한을 걸어 그 인력을 줄인다.
    """
    rng = random.Random(seed)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_first_action_of_row(row), []).append(row)

    kept: list[dict[str, Any]] = []
    dropped: dict[str, int] = {}
    for action, bucket in grouped.items():
        cap = max_per_action
        if action == "excel_live.clarify":
            cap = max_per_action * CLARIFY_CAP_MULTIPLIER
        # 첫 액션을 못 읽은 레코드(변환 단계에서 어차피 버려진다)는 손대지 않는다.
        if not action or len(bucket) <= cap:
            kept.extend(bucket)
            continue
        rng.shuffle(bucket)
        kept.extend(bucket[:cap])
        dropped[action] = len(bucket) - cap

    rng.shuffle(kept)
    return kept, dropped


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        help="입력 JSONL 경로 (여러 개 지정 시 순서대로 이어 붙인다)",
    )
    parser.add_argument("--output", required=True, help="출력 SFT JSONL 경로")
    parser.add_argument(
        "--planner-model",
        default="skt/A.X-4.0-Light:latest",
        help="프롬프트에 박히는 planner 모델 힌트 (추론 시 값과 같아야 함)",
    )
    parser.add_argument(
        "--only-passed",
        action="store_true",
        help="quality.passed가 true인 레코드만 사용",
    )
    parser.add_argument(
        "--with-clarify",
        action="store_true",
        help="되묻기 1턴 + 답변 2턴 사례를 함께 생성해 섞는다",
    )
    parser.add_argument(
        "--clarify-repeats",
        type=int,
        default=3,
        help="되묻기 사례를 통합문서를 바꿔 가며 몇 바퀴 만들지",
    )
    parser.add_argument(
        "--with-coverage",
        action="store_true",
        help="실행 가능한 액션 전부에 대해 최소 사례를 생성해 섞는다",
    )
    parser.add_argument(
        "--coverage-per-action",
        type=int,
        default=20,
        help="액션당 목표 사례 수 (커버리지 바닥)",
    )
    parser.add_argument(
        "--max-per-action",
        type=int,
        default=0,
        help="액션당 상한 (0이면 상한 없음). 증류 로그 쏠림을 눌러 준다",
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for raw_path in args.input:
        path = Path(raw_path)
        chunk = read_jsonl(path)
        print(f"입력 {path.name}: {len(chunk)}건")
        rows.extend(chunk)

    if args.with_clarify:
        clarify_rows = build_clarify_records(repeats=args.clarify_repeats)
        print(f"되묻기 생성      : {len(clarify_rows)}건")
        rows.extend(clarify_rows)

    if args.with_coverage:
        coverage_rows = build_action_coverage_records(per_action=args.coverage_per_action)
        print(f"액션 커버리지 생성: {len(coverage_rows)}건")
        rows.extend(coverage_rows)

    if args.max_per_action > 0:
        rows, dropped = balance_rows(rows, max_per_action=args.max_per_action)
        if dropped:
            top = sorted(dropped.items(), key=lambda kv: -kv[1])[:8]
            summary = ", ".join(f"{a.removeprefix(PREFIX)} -{n}" for a, n in top)
            print(f"상한 적용({args.max_per_action}) : {sum(dropped.values())}건 제외 — {summary}")

    converted, stats = convert(
        rows, planner_model=args.planner_model, only_passed=args.only_passed
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for item in converted:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"입력 레코드         : {stats['total']}")
    print(f"품질 미달 제외      : {stats['skipped_quality']}")
    print(f"계획 없음 제외      : {stats['skipped_no_plan']}")
    print(f"실행 불가 액션 제외 : {stats['skipped_invalid_action']}")
    print(f"변환 완료           : {stats['converted']}")
    print(f"  통합문서 상태 포함: {stats['with_digest']}")
    print(f"  이전 대화 포함    : {stats['with_history']}")
    print(f"  되묻기 정답       : {stats['clarify_targets']}")
    if converted:
        sample = converted[0]["messages"]
        print(f"프롬프트 길이(첫 건): {len(sample[0]['content'])}자")
        print(f"정답 길이(첫 건)    : {len(sample[1]['content'])}자")
    print(f"출력             : {out_path}")


if __name__ == "__main__":
    main()
