"""작업 트리가 HEAD 대비 lint 지적을 새로 만들었는지 본다.

이 저장소는 이미 400건이 넘는 기존 지적을 안고 있어서 "ruff 통과"를 합격 기준으로
쓸 수 없다. 대신 **늘었는가**를 본다. 줄 번호는 무시하고 (파일, 규칙) 쌍의 개수만
비교한다 — 코드를 몇 줄 밀어 넣었다고 새 지적으로 세면 안 되기 때문이다.

    uv run python scripts/ruff_delta.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

# Windows에서는 `uvx`가 .cmd 셰임이라 이름만으로는 실행되지 않는다. shell=True로
# 넘기면 리스트 인자가 통째로 무시되고 조용히 0건이 나오므로, 실행 파일을 찍어 둔다.
UVX = shutil.which("uvx") or "uvx"
GIT = shutil.which("git") or "git"

HERE = Path(__file__).resolve().parent.parent
LINE = re.compile(r"^(?P<path>.+?):\d+:\d+:\s+(?P<rule>[A-Z]+\d+)")
# ruff는 파이프로 넘길 때도 색을 입힌다. 이걸 안 걷어내면 경로 앞에 escape가 붙어
# 한 줄도 매칭되지 않고, 지적 0건이라는 조용한 거짓 합격이 나온다.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def findings(target: Path, *, strip: Path) -> Counter[tuple[str, str]]:
    proc = subprocess.run(
        [UVX, "ruff", "check", "--output-format=concise", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "NO_COLOR": "1"},
        check=False,
    )
    lines = [ANSI.sub("", raw).strip() for raw in proc.stdout.splitlines()]
    if not any(line.startswith(("Found ", "All checks passed")) for line in lines):
        raise RuntimeError(f"ruff 출력을 해석하지 못했다:\n{proc.stdout[-500:]}\n{proc.stderr[-500:]}")

    counts: Counter[tuple[str, str]] = Counter()
    for raw in lines:
        match = LINE.match(raw)
        if not match:
            continue
        path = Path(match.group("path"))
        try:
            rel = path.resolve().relative_to(strip.resolve())
        except ValueError:
            rel = path
        counts[(rel.as_posix(), match.group("rule"))] += 1
    return counts


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        baseline_root = Path(tmp) / "head"
        subprocess.run(
            [GIT, "worktree", "add", "--detach", str(baseline_root), "HEAD"],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            before = findings(baseline_root / "python-sidecar", strip=baseline_root / "python-sidecar")
            after = findings(HERE, strip=HERE)
        finally:
            subprocess.run(
                [GIT, "worktree", "remove", "--force", str(baseline_root)],
                cwd=HERE,
                capture_output=True,
                text=True,
                check=False,
            )

    print(f"HEAD  : {sum(before.values())}건")
    print(f"작업본: {sum(after.values())}건")

    added = {key: after[key] - before.get(key, 0) for key in after if after[key] > before.get(key, 0)}
    removed = {key: before[key] - after.get(key, 0) for key in before if before[key] > after.get(key, 0)}

    if added:
        print("\n새로 생긴 지적:")
        for (path, rule), count in sorted(added.items()):
            print(f"  +{count}  {rule:8} {path}")
    if removed:
        print("\n사라진 지적:")
        for (path, rule), count in sorted(removed.items()):
            print(f"  -{count}  {rule:8} {path}")
    if not added:
        print("\n새로 생긴 지적 없음.")
    return 1 if added else 0


if __name__ == "__main__":
    raise SystemExit(main())
