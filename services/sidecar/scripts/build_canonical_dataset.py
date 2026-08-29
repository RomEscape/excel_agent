# 정준 번역 데이터셋 생성 — "자유로운 사람 문장 → 검증된 정준 문형" SFT의 재료.
#
# 근거(2026-08-18 방향성): 플래너를 직접 학습시키는 대신, 사람 문장을 규칙이
# 100% 처리하는 정준 문형으로 번역하는 소형 모델을 만든다. 학습쌍은 3개월간
# 쌓인 실측 그 자체다 — chat_log의 성공 턴(사용자 원문 + 확정 계획)을
# 정준 문장으로 되돌려 렌더링한다.
#
# 사용:
#   & $PY scripts\build_canonical_dataset.py            # 전체 로그에서 생성
#   & $PY scripts\build_canonical_dataset.py --min-len 4
import argparse
import json
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parents[3] / "logs" / "chat_log.jsonl"
OUT = Path(__file__).resolve().parents[3] / "datasets" / "train" / "canonical_translate_v1.jsonl"


def _values_text(values_2d) -> str:
    rows = []
    for row in values_2d or []:
        cells = [str(c) if c is not None else "" for c in (row if isinstance(row, list) else [row])]
        rows.append(",".join(cells))
    return "; ".join(rows)


def canonical_step(action: str, params: dict) -> str | None:
    """계획 한 단계 → 규칙이 확실히 처리하는 정준 문장 하나."""
    a = action.replace("excel_live.", "")
    p = params or {}
    sheet = str(p.get("sheet_name") or "").strip()
    prefix = f"{sheet} 시트 " if sheet else ""
    if a == "write_range":
        start = str(p.get("start_cell") or "").strip()
        vals = _values_text(p.get("values_2d"))
        if not start or not vals or start.startswith("__"):
            return None
        return f"{prefix}{start}에 {vals} 입력"
    if a == "set_formula":
        ref = str(p.get("range_ref") or "").strip()
        formula = str(p.get("formula_a1") or "").strip()
        if not ref or not formula or ref.startswith("__"):
            return None
        return f"{prefix}{ref}에 {formula} 수식 넣어줘"
    if a == "fill_range":
        rng = str(p.get("target_range") or "").strip()
        color = str(p.get("fill_color") or "").strip()
        if not rng or rng.startswith("__") or not color:
            return None
        if color == "none":
            return f"{prefix}{rng} 배경색 없애줘"
        return f"{prefix}{rng} 배경색 {color}로 칠해줘"
    if a == "apply_border":
        rng = str(p.get("target_range") or "").strip()
        if not rng or rng.startswith("__"):
            return None
        if str(p.get("line_style") or "") == "none":
            return f"{prefix}{rng} 테두리 없애줘"
        return f"{prefix}{rng} 범위에 경계선 적용해줘"
    if a == "clear_range":
        rng = str(p.get("target_range") or "").strip()
        if not rng or rng.startswith("__"):
            return None
        return f"{prefix}{rng} 내용 비워줘"
    if a == "set_number_format":
        rng = str(p.get("target_range") or "").strip()
        code = str(p.get("format_code") or "").strip()
        if not rng or rng.startswith("__") or not code:
            return None
        return f"{prefix}{rng}에 숫자 형식 {code} 적용"
    if a == "set_font":
        rng = str(p.get("target_range") or "").strip()
        if not rng or rng.startswith("__"):
            return None
        bits = []
        if p.get("color"):
            bits.append(f"글자 {p['color']}")
        if p.get("bold"):
            bits.append("굵게")
        if p.get("size"):
            bits.append(f"크기 {int(p['size'])}")
        return f"{prefix}{rng} {' '.join(bits) or '글꼴 변경'} 해줘"
    if a == "create_chart":
        src = str(p.get("source_range") or "").strip()
        kind = {"line": "선", "bar": "막대", "doughnut": "도넛", "pie": "원형", "area": "영역"}.get(
            str(p.get("chart_type") or ""), str(p.get("chart_type") or "선")
        )
        if not src or src.startswith("__"):
            return None
        return f"{prefix}{src} 데이터로 {kind} 그래프 만들어줘"
    if a == "highlight_by_condition":
        rng = str(p.get("target_range") or "").strip()
        value = str(p.get("value") or "").strip()
        color = str(p.get("fill_color") or "").strip()
        if not rng or rng.startswith("__"):
            return None
        if value:
            return f"{prefix}{rng}에 값이 {value}인 셀만 배경 {color or '#FFC7CE'}로 강조해줘"
        op = str(p.get("operator") or "")
        thr = p.get("threshold")
        word = {">": "초과", ">=": "이상", "<": "미만", "<=": "이하", "==": "인"}.get(op, op)
        return f"{prefix}{rng}에 {thr} {word} 값 {color or '#FFC7CE'}로 강조해줘"
    if a == "sort_range":
        key = str(p.get("key_column") or "").strip()
        order = "내림차순" if str(p.get("order") or "").lower().startswith("desc") else "오름차순"
        if not key:
            return None
        return f"{key} 기준으로 {order} 정렬해줘"
    if a == "merge_cells":
        rng = str(p.get("target_range") or "").strip()
        return f"{prefix}{rng} 병합해줘" if rng and not rng.startswith("__") else None
    if a == "freeze_panes":
        return "1행 틀고정 해줘"
    if a == "autofit_columns":
        return "열 너비 자동 맞춤해줘"
    if a == "create_sheet":
        name = str(p.get("sheet_name") or "").strip()
        return f"{name} 시트 만들어줘" if name else None
    if a == "delete_charts":
        return f"{prefix}차트 다 지워줘"
    if a == "save_workbook":
        return "저장해줘"
    return None


def canonical_from_plan(steps: list[dict]) -> str | None:
    parts = []
    for s in steps or []:
        line = canonical_step(str(s.get("action") or ""), s.get("params") or {})
        if line is None:
            return None  # 한 단계라도 정준화 불가면 그 턴은 버린다 — 반쪽 번역은 오답 학습이다
        parts.append(line)
    if not parts:
        return None
    return " 그리고 ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-len", type=int, default=4, help="입력 문장 최소 길이")
    ap.add_argument("--since", default="2026-08-17", help="이 날짜 이후 턴만 (수정 전의 틀린 계획을 학습쌍에서 배제)")
    args = ap.parse_args()

    pairs: dict[str, str] = {}
    total = kept = 0
    with LOG.open(encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            out = e.get("outcome") or {}
            if out.get("ok") is not True or out.get("ask_follow_up") or out.get("approval_required"):
                continue
            if str(e.get("at") or "") < args.since:
                # 옛 로그에는 이미 고쳐진 결함의 **틀린 계획**이 들어 있다
                # (실측: '글자도 흰색으로'가 배경 칠하기로 기록된 시절).
                continue
            origin_kind = str((e.get("origin") or {}).get("kind") or "")
            if origin_kind in {"macro_step", "approval"}:
                # 매크로 하위 턴의 user_input은 **원 문장**인데 계획은 하위 한
                # 단계다 — 짝지으면 오답 학습이 된다(실측: '대시보드 만들어줘'
                # → '열 너비 자동 맞춤'). 승인 재개 턴은 계획 기록이 없다.
                continue
            user = str(e.get("user_input") or "").strip()
            if len(user) < args.min_len or "[[EXCEL_" in user:
                continue
            plan = None
            for stage in e.get("stages") or []:
                if stage.get("stage") == "plan_final" and isinstance(stage.get("steps"), list):
                    plan = stage["steps"]
            if not plan:
                continue
            canonical = canonical_from_plan(plan)
            if not canonical:
                continue
            import re as _re
            if _re.search(r"여기|이거|그거|저거|방금|아까|얘", user):
                # 지시어 입력은 문맥(선택 범위) 없이는 절대 좌표로 번역할 수 없다.
                # 문맥 없는 텍스트 쌍으로 학습하면 좌표를 지어내는 모델이 된다.
                continue
            if user == canonical:
                continue  # 이미 정준형 — 번역 학습에 정보가 없다
            pairs[user] = canonical
            kept += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for user, canonical in pairs.items():
            f.write(json.dumps({"input": user, "output": canonical}, ensure_ascii=False) + "\n")
    print(f"로그 턴 {total} → 정준쌍 {len(pairs)} (중복 제거 전 {kept})")
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
