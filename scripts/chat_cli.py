# -*- coding: utf-8 -*-
"""김대리 터미널 채팅 — UI 없이 모델 개발 루프를 도는 CLI.

용도: 터미널에서 대화 → 엑셀 반영 확인. GUI의 붙여넣기 흐름을 그대로 재현한다:
엑셀에서 범위를 복사(Ctrl+C)하면 클립보드에 TSV 텍스트가 실리므로 터미널에
그대로 붙여넣으면 되고, 범위 주소(A1:F6)는 사이드카의 /excel-live/selection으로
조회해 context_range로 보낸다 — GUI가 하던 것과 동일한 두 재료다.

사용:
    # 사이드카가 떠 있어야 한다(앱 실행 중이면 그 사이드카를 그대로 씀).
    # 단독 실행: cd services/sidecar && $PY -m office_claw_sidecar --port 19532 --auth-token dev-token
    $PY scripts/chat_cli.py

    명령> B7에 완료라고 써줘
    명령> (엑셀에서 범위 복사 후 그대로 붙여넣기 → 빈 줄로 마침)
    명령> /new     새 세션   /quit 종료

승인(CONFIRM) 작업은 카드 요약을 보여 주고 y/n을 받는다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = f"http://127.0.0.1:{os.environ.get('OFFICECLAW_PORT', '19532')}/excel-live"
TOKEN = os.environ.get("OFFICECLAW_TOKEN", "dev-token")
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _http(method: str, path: str, body: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=HDR, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _selection_address() -> str:
    try:
        out = _http("GET", "/selection", timeout=10)
        return str(out.get("address") or "").upper()
    except Exception:
        return ""


def _grid_to_message(lines: list[str]) -> tuple[str, int, int]:
    rows = [[c.strip() for c in ln.split("\t")] for ln in lines if ln.strip()]
    cols = max(len(r) for r in rows)
    text = "; ".join(",".join(cell if cell else "" for cell in r) for r in rows)
    return text, len(rows), cols


def _render(res: dict, session_id: str) -> None:
    """응답 하나를 사람이 읽게 — 되묻기·승인·보고서·실패를 가리지 않고."""
    result = res.get("result") if isinstance(res.get("result"), dict) else {}
    ask = res.get("ask_follow_up") or result.get("ask_follow_up")
    question = res.get("follow_up_question") or result.get("follow_up_question")
    approval = res.get("approval_required") or result.get("approval_required")
    pending = res.get("pending_approval") or result.get("pending_approval")

    if ask and question:
        print(f"❓ {question}")
        return
    if approval and pending:
        summary = pending.get("summary") or res.get("reason") or "(요약 없음)"
        print(f"🔒 승인 필요: {summary}")
        interp = pending.get("interpretation")
        if interp:
            print(f"   해석: {json.dumps(interp, ensure_ascii=False)[:200]}")
        answer = input("   실행할까요? [y/N] ").strip().lower()
        approved = answer in {"y", "yes", "ㅇ", "네", "응"}
        out = _http(
            "POST",
            "/approval",
            {"approval_id": pending.get("approval_id"), "approved": approved, "session_id": session_id},
        )
        if approved:
            _render(out, session_id)
        else:
            print("   취소했습니다.")
        return

    ok = res.get("ok")
    report = res.get("execution_report") or result.get("execution_report") or ""
    reason = res.get("reason") or ""
    if ok and report:
        print(f"✅ {report}")
    elif ok:
        print(f"✅ {reason or '완료'}")
    else:
        detail = res.get("failure_detail") or result.get("failure_detail") or ""
        print(f"❌ 실패: {reason}" + (f" ({detail})" if detail else ""))
        failed = ((res.get("diag") or {}).get("failed_steps")) or []
        for f in failed[:3]:
            print(f"   · {f}")


def main() -> int:
    try:
        health = _http("GET", "/status", timeout=5)
    except Exception as exc:
        print("사이드카에 연결할 수 없습니다:", exc)
        print("먼저 띄우세요: cd services/sidecar && $PY -m office_claw_sidecar --port 19532 --auth-token dev-token")
        return 1
    engine = health.get("engine")
    books = [b.get("name") for b in (health.get("workbooks") or [])]
    print(f"김대리 CLI — 엔진 {engine} · 열린 통합문서 {books or '없음'}")
    print("엑셀에서 범위를 복사해 그대로 붙여넣을 수 있습니다(빈 줄로 마침). /new 새 대화, /quit 종료.\n")

    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    while True:
        try:
            line = input("명령> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line.strip():
            continue
        if line.strip() == "/quit":
            return 0
        if line.strip() == "/new":
            session_id = f"cli-{uuid.uuid4().hex[:8]}"
            print("새 세션:", session_id)
            continue

        context_range = None
        message = line
        if "\t" in line:
            # 엑셀 붙여넣기(TSV) — 빈 줄이 나올 때까지 격자를 모은다.
            grid_lines = [line]
            while True:
                try:
                    more = input()
                except (EOFError, KeyboardInterrupt):
                    break
                if not more.strip():
                    break
                grid_lines.append(more)
            grid_text, n_rows, n_cols = _grid_to_message(grid_lines)
            context_range = _selection_address() or None
            addr_note = f" — {context_range} 범위로 인식" if context_range else ""
            print(f"📋 엑셀에서 붙여넣은 {n_rows}행 × {n_cols}열{addr_note}")
            command = input("이 데이터로 뭘 할까요> ").strip()
            if not command:
                print("명령이 없어 취소합니다.")
                continue
            message = f"{grid_text} {command}"

        try:
            res = _http(
                "POST",
                "/command",
                {
                    "message": message,
                    "session_id": session_id,
                    "context_range": context_range,
                    "approve": False,
                },
            )
        except urllib.error.HTTPError as exc:
            print(f"❌ HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 요청 실패: {exc}")
            continue
        _render(res, session_id)


if __name__ == "__main__":
    raise SystemExit(main())
