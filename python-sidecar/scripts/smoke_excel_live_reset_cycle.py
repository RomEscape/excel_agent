from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

BASE_URL = str(os.getenv("EXCEL_E2E_BASE_URL", "http://127.0.0.1:19532") or "http://127.0.0.1:19532")
TOKEN = str(os.getenv("EXCEL_E2E_TOKEN", "dev-token") or "dev-token")
TIMEOUT_SECONDS = int(str(os.getenv("EXCEL_E2E_TIMEOUT_SECONDS", "60") or "60"))
MAX_RETRIES = max(0, int(str(os.getenv("EXCEL_E2E_MAX_RETRIES", "2") or "2")))
RETRY_BACKOFF_SECONDS = float(str(os.getenv("EXCEL_E2E_RETRY_BACKOFF_SECONDS", "1.5") or "1.5"))
DEFAULT_WORKBOOK_PATH = Path(__file__).resolve().parents[2] / "smoke_excel_live_reset.xlsx"


@dataclass
class StepResult:
    stage: str
    scenario: str
    message: str
    status_code: int
    ok: bool
    action: str
    ask_follow_up: bool
    approval_required: bool
    elapsed_ms: int
    error: str


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _extract_error_text(resp: requests.Response) -> str:
    detail = ""
    try:
        body = resp.json() if resp.content else {}
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("reason") or body)
        else:
            detail = str(body)
    except Exception:
        try:
            detail = resp.text or ""
        except Exception:
            detail = ""
    return detail.strip()


def _is_retryable_response(resp: requests.Response) -> bool:
    if resp.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    if resp.status_code == 400:
        detail = _extract_error_text(resp).lower()
        if "해석 시간이 초과" in detail or "timeout" in detail:
            return True
    return False


def _request_with_retry(
    method: str,
    url: str,
    *,
    json_payload: dict[str, Any] | None = None,
) -> requests.Response:
    last_exc: Exception | None = None
    resp: requests.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=_headers(),
                json=json_payload,
                timeout=TIMEOUT_SECONDS,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * float(attempt + 1))
                continue
            raise

        if _is_retryable_response(resp) and attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * float(attempt + 1))
            continue
        return resp

    if resp is not None:
        return resp
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("HTTP 요청이 비정상 종료되었습니다.")


def _fetch_active_workbook() -> tuple[str | None, str | None]:
    resp = _request_with_retry("GET", f"{BASE_URL}/excel-live/status")
    if resp.status_code != 200:
        return None, None
    body = resp.json() if resp.content else {}
    rows = body.get("workbooks") if isinstance(body.get("workbooks"), list) else []
    if not rows:
        return None, None
    first = rows[0] if isinstance(rows[0], dict) else {}
    return str(first.get("workbook_id") or ""), str(first.get("active_sheet") or "Sheet1")


def _post_command(
    *,
    scenario: str,
    stage: str,
    message: str,
    session_id: str,
    workbook_id: str,
    sheet_name: str,
) -> StepResult:
    payload = {
        "message": message,
        "session_id": session_id,
        "workbook_id": workbook_id,
        "sheet_name": sheet_name,
        "approve": True,
    }
    t0 = time.time()
    try:
        resp = _request_with_retry(
            "POST",
            f"{BASE_URL}/excel-live/command",
            json_payload=payload,
        )
        elapsed = int((time.time() - t0) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        return StepResult(
            stage=stage,
            scenario=scenario,
            message=message,
            status_code=resp.status_code,
            ok=bool(body.get("ok", False)),
            action=str(body.get("action", "")),
            ask_follow_up=bool(result.get("ask_follow_up", False)),
            approval_required=bool(body.get("approval_required", False)),
            elapsed_ms=elapsed,
            error="" if resp.status_code == 200 else str(body)[:220],
        )
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        return StepResult(
            stage=stage,
            scenario=scenario,
            message=message,
            status_code=0,
            ok=False,
            action="",
            ask_follow_up=False,
            approval_required=False,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def _post_action(
    *,
    scenario: str,
    stage: str,
    action: str,
    params: dict[str, Any],
    workbook_id: str,
    sheet_name: str,
) -> StepResult:
    payload = {
        "action": action,
        "params": params,
        "workbook_id": workbook_id,
        "sheet_name": sheet_name,
        "approve": True,
    }
    t0 = time.time()
    try:
        resp = _request_with_retry(
            "POST",
            f"{BASE_URL}/excel-live/action",
            json_payload=payload,
        )
        elapsed = int((time.time() - t0) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        return StepResult(
            stage=stage,
            scenario=scenario,
            message=f"{action} {json.dumps(params, ensure_ascii=False)}",
            status_code=resp.status_code,
            ok=bool(body.get("ok", False)),
            action=str(body.get("action", action)),
            ask_follow_up=bool(result.get("ask_follow_up", False)),
            approval_required=bool(body.get("approval_required", False)),
            elapsed_ms=elapsed,
            error="" if resp.status_code == 200 else str(body)[:220],
        )
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        return StepResult(
            stage=stage,
            scenario=scenario,
            message=f"{action} {json.dumps(params, ensure_ascii=False)}",
            status_code=0,
            ok=False,
            action=action,
            ask_follow_up=False,
            approval_required=False,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def _seed_base_data(*, scenario: str, workbook_id: str, sheet_name: str) -> list[StepResult]:
    seed_results: list[StepResult] = []
    seed_results.append(
        _post_action(
            scenario=scenario,
            stage="seed_action",
            action="excel_live.write_range",
            params={"start_cell": "A1", "values_2d": [["이름", "수량", "단가", "금액"]]},
            workbook_id=workbook_id,
            sheet_name=sheet_name,
        )
    )
    seed_results.append(
        _post_action(
            scenario=scenario,
            stage="seed_action",
            action="excel_live.write_range",
            params={
                "start_cell": "A2",
                "values_2d": [
                    ["민수", 10, 1000],
                    ["지연", 16, 1200],
                    ["도윤", 15, 900],
                    ["하늘", 13, 1500],
                    ["나래", 12, 800],
                ],
            },
            workbook_id=workbook_id,
            sheet_name=sheet_name,
        )
    )
    seed_results.append(
        _post_action(
            scenario=scenario,
            stage="seed_action",
            action="excel_live.set_formula",
            params={"range_ref": "D2:D6", "formula_a1": "=B2*C2"},
            workbook_id=workbook_id,
            sheet_name=sheet_name,
        )
    )
    return seed_results


def _effective_success(row: StepResult) -> bool:
    if row.status_code != 200:
        return False
    return bool(row.ok or row.ask_follow_up or row.approval_required)


def run() -> None:
    workbook_id, sheet_name = _fetch_active_workbook()
    if not workbook_id and DEFAULT_WORKBOOK_PATH.exists():
        try:
            os.startfile(str(DEFAULT_WORKBOOK_PATH))  # type: ignore[attr-defined]
        except Exception:
            pass
        for _ in range(10):
            time.sleep(1)
            workbook_id, sheet_name = _fetch_active_workbook()
            if workbook_id:
                break
    if not workbook_id:
        print("ERROR: Excel Live에서 활성 통합문서를 찾지 못했습니다. Excel 파일을 열어주세요.")
        return

    scenarios: list[dict[str, Any]] = [
        {
            "name": "조건부색-이중",
            "turns": ["B열 15 이상은 빨간색, 나머지는 노란색으로 구분해줘"],
            "reset": "A1:Z200 범위 내용 전부 지워서 초기 상태로 돌려줘",
        },
        {
            "name": "정렬-러프표현",
            "turns": ["매출 큰 순으로 보기 좋게 정렬해줘"],
            "reset": "현재 시트 데이터 싹 비워줘",
        },
        {
            "name": "필터-자연어",
            "turns": ["수량 12 이상인 항목만 남겨줘"],
            "reset": "안에 있는 내용만 전부 삭제해줘",
        },
        {
            "name": "중복제거",
            "turns": ["이름 기준으로 중복 제거해줘"],
            "reset": "표 내용 전체 클리어해서 초기화해줘",
        },
        {
            "name": "수식-vlookup-멀티턴",
            "turns": [
                "코드 기준으로 가격 찾아와",
                "조회값은 A열, 참조표는 F열부터 H열, 반환 2열",
            ],
            "reset": "A1:Z200 다 지우고 새로 시작할 수 있게 해줘",
        },
        {
            "name": "수식-if-멀티턴",
            "turns": [
                "점수 기준 조건식 넣어줘",
                "점수는 C열, 70 미만이면 미달 아니면 통과",
            ],
            "reset": "현재 시트 값 전체 삭제해줘",
        },
        {
            "name": "테두리",
            "turns": ["A1:D6에 테두리 넣어줘"],
            "reset": "A1:Z200 데이터 전부 비워줘",
        },
        {
            "name": "요약요청-일반의도",
            "turns": ["보고용으로 한눈에 정리해줘"],
            "reset": "이번 테스트 내용 전부 지워줘",
        },
    ]

    results: list[StepResult] = []

    for idx, scenario in enumerate(scenarios, start=1):
        scenario_id = f"reset-cycle-{idx}-{uuid.uuid4().hex[:6]}"
        results.extend(
            _seed_base_data(
                scenario=scenario["name"],
                workbook_id=workbook_id,
                sheet_name=sheet_name or "Sheet1",
            )
        )

        for msg in scenario["turns"]:
            r = _post_command(
                scenario=scenario["name"],
                stage="scenario",
                message=msg,
                session_id=scenario_id,
                workbook_id=workbook_id,
                sheet_name=sheet_name or "Sheet1",
            )
            results.append(r)

        reset_id = f"reset-only-{idx}-{uuid.uuid4().hex[:6]}"
        r_reset = _post_command(
            scenario=scenario["name"],
            stage="reset",
            message=str(scenario["reset"]),
            session_id=reset_id,
            workbook_id=workbook_id,
            sheet_name=sheet_name or "Sheet1",
        )
        results.append(r_reset)
        if r_reset.status_code != 200:
            results.append(
                _post_action(
                    scenario=scenario["name"],
                    stage="reset_fallback",
                    action="excel_live.clear_range",
                    params={"target_range": "A1:Z200"},
                    workbook_id=workbook_id,
                    sheet_name=sheet_name or "Sheet1",
                )
            )

        print(
            f"[{idx}/{len(scenarios)}] {scenario['name']} | "
            f"scenario_last={results[-2].status_code if len(scenario['turns']) else 0} "
            f"reset={r_reset.status_code} action={r_reset.action}",
            flush=True,
        )

    total = len(results)
    http_200 = sum(1 for r in results if r.status_code == 200)
    effective_ok = sum(1 for r in results if _effective_success(r))
    scenario_steps = [r for r in results if r.stage == "scenario"]
    scenario_200 = sum(1 for r in scenario_steps if r.status_code == 200)
    scenario_effective = sum(1 for r in scenario_steps if _effective_success(r))
    reset_steps = [r for r in results if r.stage == "reset"]
    reset_200 = sum(1 for r in reset_steps if r.status_code == 200)
    reset_effective = sum(1 for r in reset_steps if _effective_success(r))
    asks = sum(1 for r in scenario_steps if r.ask_follow_up)
    approvals = sum(1 for r in scenario_steps if r.approval_required)
    p50 = int(statistics.median([r.elapsed_ms for r in results])) if results else 0
    p95 = int(sorted([r.elapsed_ms for r in results])[max(0, int(total * 0.95) - 1)]) if results else 0

    # 실행 id + 영속 JSON — 없으면 "개선했다"는 주장을 나중에 같은 로그로 재확인할 수
    # 없다(CLAUDE.md §1: 수치는 실행 id와 함께). 223항목 감사(2026-08-24)에서
    # 이 스크립트가 수치를 stdout에만 흘려 개선분이 일지에 실리지 못한 것이 확인됐다.
    run_id = time.strftime("%m%d-%H%M%S") + "-reset-cycle"
    summary = {
        "run_id": run_id,
        "workbook_id": workbook_id,
        "total_steps": total,
        "http_200": http_200,
        "effective_ok": effective_ok,
        "scenario": {"total": len(scenario_steps), "http_200": scenario_200, "effective_ok": scenario_effective},
        "reset": {"total": len(reset_steps), "http_200": reset_200, "effective_ok": reset_effective},
        "ask_follow_up": asks,
        "approval_required": approvals,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "steps": [
            {
                "scenario": r.scenario, "stage": r.stage, "message": r.message,
                "status": r.status_code, "ok": r.ok, "action": r.action,
                "elapsed_ms": r.elapsed_ms, "error": r.error,
            }
            for r in results
        ],
    }
    out_dir = Path(__file__).resolve().parent.parent.parent / "logs" / "e2e"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"smoke_reset_cycle_{run_id}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print("")
    print("=== Excel Live Reset-Cycle E2E ===")
    print(f"run_id={run_id}")
    print(f"report={out_path}")
    print(f"workbook_id={workbook_id}")
    print(f"total_steps={total}")
    print(f"http_200={http_200}/{total}")
    print(f"effective_ok={effective_ok}/{total}")
    print(f"scenario_http_200={scenario_200}/{len(scenario_steps)}")
    print(f"scenario_effective_ok={scenario_effective}/{len(scenario_steps)}")
    print(f"reset_http_200={reset_200}/{len(reset_steps)}")
    print(f"reset_effective_ok={reset_effective}/{len(reset_steps)}")
    print(f"scenario_follow_up={asks}")
    print(f"scenario_approval_required={approvals}")
    print(f"latency_p50_ms={p50}")
    print(f"latency_p95_ms={p95}")

    failed = [r for r in results if r.status_code != 200]
    if failed:
        print("")
        print("[실패 단계]")
        for row in failed:
            print(
                json.dumps(
                    {
                        "scenario": row.scenario,
                        "stage": row.stage,
                        "message": row.message,
                        "status": row.status_code,
                        "action": row.action,
                        "error": row.error,
                    },
                    ensure_ascii=False,
                )
            )

    print("")
    print("[시나리오 단계 샘플 결과]")
    for row in scenario_steps[:12]:
        print(
            json.dumps(
                {
                    "scenario": row.scenario,
                    "stage": row.stage,
                    "message": row.message,
                    "status": row.status_code,
                    "ok": row.ok,
                    "action": row.action,
                    "ask_follow_up": row.ask_follow_up,
                    "approval_required": row.approval_required,
                    "elapsed_ms": row.elapsed_ms,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    run()
