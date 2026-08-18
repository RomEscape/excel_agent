"""대화 각본 정적 검사 — 붙여넣기 턴의 범위 크기와 값 격자가 맞는지, 금지 문형이 없는지.

사용: PYTHONUTF8=1 python scripts/validate_dialogue.py scenarios/dialogue/dialogue_ex1.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from office_claw_sidecar.services.excel_live_agent import (
    _split_header_tokens,
    normalize_common_typos,
    parse_rangeless_row_write,
)

VERB_END = re.compile(r"(입력|기록|넣어|채워|써)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐|조)?\s*[~.!?…]*\s*$")
GRID_RANGE_IN_TEXT = re.compile(r"[A-Za-z]{1,3}\d{1,7}:[A-Za-z]{1,3}\d{1,7}\s*(?:에다가?|에)\s")


def col_num(s: str) -> int:
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


def shape(ref: str) -> tuple[int, int]:
    m = re.match(r"^([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?$", ref.upper())
    if not m:
        return (0, 0)
    if not m.group(3):
        return (1, 1)
    return (int(m.group(4)) - int(m.group(2)) + 1, col_num(m.group(3)) - col_num(m.group(1)) + 1)


def main(path: Path) -> int:
    sc = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for i, t in enumerate(sc["turns"], 1):
        cmd = str(t.get("command") or "")
        paste = str(t.get("paste") or "").strip().upper()
        tsv = str(t.get("tsv") or "")
        if paste and tsv:
            plan = parse_rangeless_row_write(normalize_common_typos(tsv + chr(10) + cmd), paste)
            n_rows = len([ln for ln in tsv.split(chr(10)) if ln.strip()])
            if plan is None or len(plan["params"]["values_2d"]) != n_rows:
                errors.append(f"[{i}] TSV 붙여넣기 파서 실패/행 수 불일치: {cmd[:40]}")
            continue
        if paste:
            rows, cols = shape(paste)
            if rows == 0:
                errors.append(f"[{i}] paste 범위 표기 오류: {paste}")
                continue
            norm = normalize_common_typos(cmd)
            plan = parse_rangeless_row_write(norm, paste)
            if plan is None:
                if t.get("expect") in ("ask", "noop"):
                    continue  # 값 없는 실수 턴은 의도된 되묻기다
                errors.append(f"[{i}] 붙여넣기 파서가 못 잡음: {cmd[:80]}")
                continue
            grid = plan["params"]["values_2d"]
            if len(grid) != rows:
                errors.append(f"[{i}] 행 수 불일치: 범위 {rows}행 vs 값 {len(grid)}행 — {cmd[:60]}")
            body = cmd
            groups = [g for g in re.split(r"[;\n]", VERB_END.sub("", body)) if g.strip()]
            for gi, g in enumerate(groups):
                toks = _split_header_tokens(g.strip())
                if len(toks) != cols:
                    errors.append(f"[{i}] {gi + 1}번째 줄 열 수 {len(toks)} ≠ {cols}: {g.strip()[:70]}")
        else:
            if GRID_RANGE_IN_TEXT.search(cmd) and VERB_END.search(cmd) and (cmd.count(",") + cmd.count(";")) >= 2:
                errors.append(f"[{i}] 좌표 격자 쓰기 문형이 남아 있음(붙여넣기 턴으로 바꿀 것): {cmd[:80]}")
            if "=" in cmd and t.get("expect", "ok") == "ok":
                errors.append(f"[{i}] 수식 문자열 직접 지정 남아 있음(사람 말투로 바꿀 것): {cmd[:80]}")
    for e in errors:
        print("ERR", e)
    print(f"{path.name}: {len(sc['turns'])}턴, 오류 {len(errors)}건")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
