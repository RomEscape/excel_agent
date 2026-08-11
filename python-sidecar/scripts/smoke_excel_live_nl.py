from __future__ import annotations

import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "http://127.0.0.1:19532"
TOKEN = "dev-token"
TIMEOUT_SECONDS = 8

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass
class StepResult:
    kind: str
    message: str
    status_code: int
    elapsed_ms: int
    ok: bool
    action: str
    ask_follow_up: bool
    approval_required: bool
    reason: str
    error: str


def _post_command(message: str, *, session_id: str, approve: bool = False) -> StepResult:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {"message": message, "session_id": session_id, "approve": approve}
    t0 = time.time()
    try:
        resp = requests.post(
            f"{BASE_URL}/excel-live/command",
            headers=headers,
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
        elapsed = int((time.time() - t0) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        return StepResult(
            kind="single",
            message=message,
            status_code=resp.status_code,
            elapsed_ms=elapsed,
            ok=bool(body.get("ok", False)),
            action=str(body.get("action", "")),
            ask_follow_up=bool(result.get("ask_follow_up", False)),
            approval_required=bool(body.get("approval_required", False)),
            reason=str(body.get("reason", ""))[:160],
            error="" if resp.status_code == 200 else str(body)[:160],
        )
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        return StepResult(
            kind="single",
            message=message,
            status_code=0,
            elapsed_ms=elapsed,
            ok=False,
            action="",
            ask_follow_up=False,
            approval_required=False,
            reason="",
            error=f"{type(exc).__name__}: {exc}",
        )


def run() -> None:
    single_prompts = [
        "표 만들어줘",
        "회의록 표 만들어줘",
        "체크리스트 표 하나 만들어줘",
        "5x5 표 만들어줘",
        "정렬해줘",
        "매출 큰 순으로 정렬해줘",
        "완료만 보여줘",
        "중복 없애줘",
        "피벗으로 정리해줘",
        "차트로 보여줘",
        "데이터 이상한 값 점검해줘",
        "수량이랑 단가 곱해서 금액 계산해줘",
        "세금 포함 금액 계산해줘",
        "목표 대비 부족한지 계산해줘",
        "완료 건수 세어줘",
        "코드 기준으로 가격 찾아와",
        "점수 70 미만이면 미달 아니면 통과로 표시해줘",
        "D2:D50 수식 결과 확인해줘",
        "A1:C3 읽어줘",
        "여기에 테두리 넣어줘",
        "안에 내용 전부 지우고 깨끗하게 만들어줘",
        "저장해줘",
        "이거 한 번에 정리해줘",
        # 광범위 러프 표현 확장
        "이거 알아서 정리해줘",
        "보기 좋게 만들어줘",
        "계산되게 해줘",
        "자동으로 나오게 해줘",
        "조건에 맞는 것만 골라줘",
        "틀린 값 있는지 봐줘",
        "빠진 사람 찾아줘",
        "제일 큰 값 알려줘",
        "이번 달 것만 보여줘",
        "지난달이랑 비교해줘",
        "월별로 나눠줘",
        "담당자별로 정리해줘",
        "상품별로 정리해줘",
        "지역별로 묶어줘",
        "부서별로 얼마 썼는지 알려줘",
        "누가 제일 많이 했는지 알려줘",
        "보고용으로 바꿔줘",
        "사장님한테 보여줄 수 있게 정리해줘",
        "한눈에 보이게 해줘",
        "색깔로 구분해줘",
        "마감 지난 건 빨갛게 해줘",
        "완료된 건 숨겨줘",
        "미완료만 보여줘",
        "진행률 계산해줘",
        "등급 나오게 해줘",
        "합격 불합격 나오게 해줘",
        "가격 자동으로 들어오게 해줘",
        "이름 넣으면 정보 나오게 해줘",
        "코드 넣으면 상품명 나오게 해줘",
        "두 표 비교해줘",
        "파일 여러 개 합쳐줘",
        "시트 여러 개 합쳐줘",
        "시트 나눠줘",
        "양식만 남겨줘",
        "프린트 잘 되게 해줘",
        "PDF로 저장하기 좋게 맞춰줘",
        "매달 반복되는 작업 자동화해줘",
        "버튼 누르면 정리되게 해줘",
        "새 데이터 넣으면 차트도 바뀌게 해줘",
        "조건에 맞는 것만 더해줘",
        "조건에 맞는 사람 수 세줘",
        "값이 없으면 오류 말고 빈칸으로 나오게 해줘",
        "중복 없이 목록만 뽑아줘",
        "자동으로 가나다순 정렬되게 해줘",
        "날짜에서 월만 따로 뽑아줘",
        "오늘 기준으로 며칠 남았는지 계산해줘",
        "근무일 기준으로 며칠 걸렸는지 계산해줘",
        "순위 자동으로 나오게 해줘",
        "목표 넘으면 달성 아니면 미달로 나오게 해줘",
        "기준표에 없는 값만 찾아줘",
        "월별 매출 정리해줘",
        "요약 시트 하나 만들어줘",
        "발표용 차트로 예쁘게 만들어줘",
        "상태를 목록에서 고르게 해줘",
        "잘못 입력 못 하게 해줘",
        "이 데이터 요약해줘",
        "중요한 내용만 말해줘",
        "앞으로 어떻게 될지 예측해줘",
        "재고 언제 부족해질지 알려줘",
        "지출 내역 정리해줘",
        "예산 초과한 항목 찾아줘",
        "직원 근태 정리해줘",
        "프로젝트 일정 정리해줘",
        "성적표 만들어줘",
        # 시스템 설계 관점 보강(권한/복구/성능/버전/교육)
        "파일이 읽기 전용이라 수정이 안 돼",
        "보호된 보기라 편집이 안 돼",
        "왜 편집이 안 되지?",
        "#N/A가 왜 떠?",
        "#VALUE 오류 고쳐줘",
        "합계가 이상하게 나와",
        "내 엑셀에서 FILTER 함수가 안 돼",
        "피벗이 업데이트가 안 돼",
        "파일이 너무 느려",
        "엑셀이 자꾸 멈춰",
        "A4 한 장에 맞춰줘",
        "다른 사람 못 고치게 잠가줘",
        "특정 셀만 수정 못 하게 해줘",
        "외부 링크 제거해줘",
        "개인정보 열 지워줘",
        "실수로 지운 거 되돌릴 수 있어?",
        "원본 백업하고 작업해줘",
        "피벗이 뭐야? 쉽게 설명해줘",
        "Power Query가 뭐야?",
        "매크로 실행해도 안전해?",
        "원본 덮어써도 돼?",
        "읽기 전용 파일인데도 강제로 수정해줘",
        "버전 낮아서 최신 함수 못 쓰는데 대체식으로 해줘",
    ]

    multi_turn = [
        ["표 만들어줘", "5*5, 금액, 장소, 날짜, 요건, 비고"],
        ["정렬해줘", "매출 열 기준 내림차순"],
        ["완료만 보여줘", "상태 열 기준으로 해줘"],
        ["중복 없애줘", "전화번호 기준"],
        ["피벗으로 정리해줘", "월별 매출 합계로"],
        ["그래프로 보여줘", "선 그래프로"],
        ["수량이랑 가격 곱해서 금액 계산해줘", "B열이 수량, C열이 단가"],
        ["완료 건수 세어줘", "B열 상태에서 완료 개수"],
        ["코드 기준으로 가격 찾아와", "조회값은 A열, 참조표는 F열부터 H열, 반환 2열"],
        ["점수 기준 조건식 넣어줘", "C열이 70 미만이면 미달, 아니면 통과"],
        # 시스템 설계 관점 멀티턴(복구/권한/버전/디버깅)
        ["파일이 읽기 전용이라 수정이 안 돼", "원본 백업 후 새 시트에 결과 만들어줘"],
        ["수식이 이상해", "총액 열이 수량*단가랑 안 맞는 행만 찾아줘"],
        ["A4 한 장에 맞춰줘", "가로 방향, 제목행 반복, 여백 좁게"],
        ["외부 링크 제거해줘", "삭제 전에 백업부터 만들어줘"],
        ["코드 기준으로 가격 찾아와", "FILTER 안 되면 VLOOKUP 호환식으로 넣어줘"],
        ["매크로 실행해도 안전해?", "확인 절차 먼저 보여주고 진행할게"],
    ]

    results: list[StepResult] = []

    total_steps = len(single_prompts) + sum(len(t) for t in multi_turn)
    done = 0

    # single turn
    for p in single_prompts:
        sid = f"smoke-single-{uuid.uuid4().hex[:8]}"
        r = _post_command(p, session_id=sid, approve=False)
        results.append(r)
        done += 1
        print(f"[{done}/{total_steps}] single | {p} | status={r.status_code} | {r.elapsed_ms}ms", flush=True)

    # multi turn
    for turns in multi_turn:
        sid = f"smoke-multi-{uuid.uuid4().hex[:8]}"
        for idx, msg in enumerate(turns):
            r = _post_command(msg, session_id=sid, approve=False)
            r.kind = f"multi-{idx + 1}"
            results.append(r)
            done += 1
            print(
                f"[{done}/{total_steps}] multi-{idx + 1} | {msg} | status={r.status_code} | {r.elapsed_ms}ms",
                flush=True,
            )

    total = len(results)
    status_ok = sum(1 for r in results if r.status_code == 200)
    timeout_or_net = [r for r in results if r.status_code == 0]
    slow = [r for r in results if r.elapsed_ms >= 4000]
    follow = sum(1 for r in results if r.ask_follow_up)
    approvals = sum(1 for r in results if r.approval_required)

    elapsed_values = [r.elapsed_ms for r in results]
    p50 = int(statistics.median(elapsed_values)) if elapsed_values else 0
    p95 = int(sorted(elapsed_values)[int(len(elapsed_values) * 0.95) - 1]) if elapsed_values else 0

    print("=== Excel Live 자연어 스모크 테스트 ===")
    print(f"total={total}")
    print(f"http_200={status_ok}/{total}")
    print(f"network_or_timeout={len(timeout_or_net)}")
    print(f"ask_follow_up={follow}")
    print(f"approval_required={approvals}")
    print(f"slow_ge_4000ms={len(slow)}")
    print(f"latency_p50_ms={p50}")
    print(f"latency_p95_ms={p95}")
    print("")

    if timeout_or_net:
        print("[네트워크/타임아웃]")
        for r in timeout_or_net:
            print(f"- {r.kind} | {r.message} | {r.error}")
        print("")

    if slow:
        print("[느린 케이스 >= 4s]")
        for r in sorted(slow, key=lambda x: -x.elapsed_ms)[:20]:
            print(f"- {r.elapsed_ms}ms | {r.kind} | {r.message} | action={r.action} | ask={r.ask_follow_up}")
        print("")

    print("[샘플 결과 15건]")
    for r in results[:15]:
        print(
            json.dumps(
                {
                    "kind": r.kind,
                    "message": r.message,
                    "status": r.status_code,
                    "elapsed_ms": r.elapsed_ms,
                    "action": r.action,
                    "ask_follow_up": r.ask_follow_up,
                    "approval_required": r.approval_required,
                    "reason": r.reason,
                    "error": r.error,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    run()

