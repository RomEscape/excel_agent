import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rows = report["results"] if isinstance(report, dict) and "results" in report else report
lines = []
for row in rows:
    detail = row.get("result") if isinstance(row.get("result"), dict) else {}
    asserts = row.get("assertion_results") or detail.get("assertions") or []
    passed = row.get("passed", row.get("ok"))
    lines.append(f"[{'PASS' if passed else 'FAIL'}] {row.get('id')} assertions={len(asserts)}")
    for item in asserts:
        lines.append(f"    {'ok' if item.get('ok') else 'NG'}  {item.get('detail')}")
Path(sys.argv[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
