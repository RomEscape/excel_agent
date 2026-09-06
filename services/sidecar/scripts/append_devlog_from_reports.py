from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9), name="KST")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _read_json_optional(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _run_git_changed_files(staged: bool) -> list[str]:
    cmd = ["git", "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=ACMRD"]
    if staged:
        # 예전엔 index 2 에 넣어 `git -c --cached core.quotepath=false …` 가 됐다 — git 이
        # "key does not contain a section: --cached" 로 exit 128 → except 가 [] → 언제나
        # "[SKIP] staged 변경 파일이 없어". 2026-08-04 도입 이래 자동 블록이 0회 붙은 이유
        # (2026-09-06 감사에서 재현). `diff` 뒤에 넣어야 한다.
        cmd.insert(cmd.index("diff") + 1, "--cached")
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    except Exception:
        return []
    rows = []
    for line in raw.splitlines():
        path = str(line or "").strip().replace("\\", "/")
        if path:
            rows.append(path)
    return rows


def _requires_devlog(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return False
    if normalized == "개발일지.md":
        return False
    if normalized.startswith("docs/"):
        return False
    if normalized.startswith("logs/"):
        return False
    if normalized.endswith(".md"):
        return False
    return True


def _build_auto_id(
    *,
    changed_files: list[str],
    complex_report: dict[str, Any] | None,
    gate_report: dict[str, Any] | None,
) -> str:
    payload = {
        "changed_files": sorted(changed_files),
        "complex_at": (complex_report or {}).get("at", ""),
        "gate_at": (gate_report or {}).get("at", ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_section(
    *,
    changed_files: list[str],
    complex_report: dict[str, Any] | None,
    gate_report: dict[str, Any] | None,
    auto_id: str,
) -> str:
    now = _now_kst()
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M:%S")
    lines: list[str] = []
    lines.append(f"## {date_label} — 자동 품질 리포트")
    lines.append("")
    lines.append(f"- 기록 시각(KST): `{date_label} {time_label}`")
    lines.append(f"- 자동 기록 ID: `{auto_id}`")

    if complex_report:
        total = _safe_int(
            complex_report.get("total_scenarios", complex_report.get("total", 0))
        )
        passed = _safe_int(
            complex_report.get("passed_scenarios", complex_report.get("passed", 0))
        )
        rate = _safe_float(
            complex_report.get("pass_rate", (passed / total) if total > 0 else 0.0)
        )
        critical = _safe_int(complex_report.get("critical_failures", 0))
        failed_ids = complex_report.get("failed_scenarios")
        if not isinstance(failed_ids, list):
            failed_ids = []
        lines.append(f"- 복잡 시나리오: `{passed}/{total}` (`{rate:.2%}`), critical_failures=`{critical}`")
        if failed_ids:
            sample = ", ".join(str(x) for x in failed_ids[:5])
            lines.append(f"- 복잡 시나리오 실패 샘플: `{sample}`")
    else:
        lines.append("- 복잡 시나리오 리포트: `없음`")

    if gate_report:
        passed = bool(gate_report.get("passed", False))
        summary = gate_report.get("summary") if isinstance(gate_report.get("summary"), dict) else {}
        parse_gain_pp = _safe_float(summary.get("parse_gain_pp", 0.0))
        p95_ratio = _safe_float(summary.get("p95_latency_ratio", 0.0))
        lines.append(
            f"- 릴리즈 게이트: `{'통과' if passed else '실패'}` "
            f"(parse_gain_pp=`{parse_gain_pp:.2f}`, p95_ratio=`{p95_ratio:.3f}`)"
        )
    else:
        lines.append("- 릴리즈 게이트 리포트: `없음`")

    if changed_files:
        lines.append("- 이번 변경 파일(상위 20):")
        for path in changed_files[:20]:
            lines.append(f"  - `{path}`")
    lines.append("")
    lines.append(f"<!-- devlog-auto-id:{auto_id} -->")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="검증 리포트 기반 개발일지 자동 append")
    parser.add_argument(
        "--devlog",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "개발일지.md",
    )
    parser.add_argument("--complex-report", type=Path, default=None)
    parser.add_argument("--release-gate", type=Path, default=None)
    parser.add_argument("--from-staged", action="store_true")
    parser.add_argument("--skip-if-devlog-staged", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    changed_files = _run_git_changed_files(staged=bool(args.from_staged))
    requires = any(_requires_devlog(path) for path in changed_files)

    if args.from_staged and not changed_files:
        print("[SKIP] staged 변경 파일이 없어 자동 append를 건너뜁니다.")
        return
    if not requires:
        print("[SKIP] 개발일지 업데이트 대상 코드 변경이 없어 자동 append를 건너뜁니다.")
        return
    if args.skip_if_devlog_staged and "개발일지.md" in changed_files:
        print("[SKIP] 개발일지.md가 이미 staged 되어 자동 append를 건너뜁니다.")
        return

    complex_report = _read_json_optional(args.complex_report)
    gate_report = _read_json_optional(args.release_gate)
    auto_id = _build_auto_id(
        changed_files=changed_files,
        complex_report=complex_report,
        gate_report=gate_report,
    )
    marker = f"<!-- devlog-auto-id:{auto_id} -->"

    if not args.devlog.exists():
        raise FileNotFoundError(f"개발일지 파일이 없습니다: {args.devlog}")
    before = args.devlog.read_text(encoding="utf-8")
    if marker in before:
        print(f"[SKIP] 동일 자동 기록 ID가 이미 존재합니다: {auto_id}")
        return

    section = _build_section(
        changed_files=changed_files,
        complex_report=complex_report,
        gate_report=gate_report,
        auto_id=auto_id,
    )
    updated = before.rstrip() + "\n\n---\n\n" + section
    args.devlog.write_text(updated, encoding="utf-8")
    print(f"[DONE] 개발일지 자동 append 완료: {args.devlog} (id={auto_id})")


if __name__ == "__main__":
    main()
