"""시나리오 팩의 오라클 강도를 점검한다.

액션 이름만 확인하고 실제 결과(셀 값·서식·행 수)를 전혀 검사하지 않는
시나리오를 찾아낸다. 이런 시나리오는 통과해도 기능이 동작한다는 근거가 되지 못한다.
"""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack", type=Path)
    args = parser.parse_args()

    data = json.loads(args.pack.read_text(encoding="utf-8"))
    scenarios = data["scenarios"] if isinstance(data, dict) else data

    weak: list[str] = []
    strong: list[str] = []
    for s in scenarios:
        assertions = (s.get("oracle", {}).get("result", {}) or {}).get("assertions") or []
        label = f"{s['id']} (severity={s.get('severity', '-')}, 검증 {len(assertions)}건)"
        (strong if assertions else weak).append(label)

    print(f"총 {len(scenarios)}개 / 결과 검증 있음 {len(strong)} / 이름만 확인 {len(weak)}\n")
    print("[결과 검증 없음 — 통과해도 근거가 약함]")
    for row in weak:
        print(f"  - {row}")
    print("\n[결과 검증 있음]")
    for row in strong:
        print(f"  - {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
