import test from "node:test";
import assert from "node:assert/strict";

import {
  decideExcelRoute,
  isChatFallbackResponse,
  isSafetyStopResponse,
  shouldRouteToExcelLive,
} from "./excelRouting.js";

// 2026-08-16 실측에서 일반 채팅으로 샜던 문장들. 전부 이미 구현된 액션이다.
const LEAKED = [
  "지역별로 묶어서 합계 내줘",
  "각 부서 평균 계산해줘",
  "피벗으로 요약해줘",
  "이름순으로 정렬해줘",
  "필터 걸어줘",
  "월별 추이 그래프 그려줘",
  "빈 값 있는 행 삭제해줘",
  "날짜 형식 통일해줘",
  "제목행 고정해줘",
  "PDF로 내보내줘",
  "인쇄 영역 설정해줘",
  "상위 10개만 남기고 나머지 빼줘",
];

// 되묻기·승인 대기의 다음 턴. 그 자체로는 엑셀 문장처럼 안 보인다.
const FOLLOW_UPS = [
  "일별로 만들어줄래?",
  "응 그렇게 해줘",
  "두 번째 걸로 해줘",
  "다시 제안해줄래?",
  "아까 그거 취소해줘",
];

test("워크북이 열려 있으면 새던 문장이 전부 엑셀로 간다", () => {
  for (const message of LEAKED) {
    assert.equal(
      decideExcelRoute({ message, workbookAvailable: true }),
      true,
      `엑셀로 가야 한다: ${message}`
    );
  }
});

test("되묻기 다음 턴은 워크북 상태와 무관하게 엑셀로 간다", () => {
  // 워크북이 닫혀 있어도 문맥은 이어져야 한다.
  for (const message of FOLLOW_UPS) {
    assert.equal(
      decideExcelRoute({ message, wasExcelFollowUp: true, workbookAvailable: false }),
      true,
      `문맥이 끊기면 안 된다: ${message}`
    );
  }
});

test("워크북이 없고 되묻기도 아니면 키워드가 있을 때만 엑셀로 간다", () => {
  assert.equal(decideExcelRoute({ message: "엑셀 정렬해줘" }), true);
  assert.equal(decideExcelRoute({ message: "A1:C5 정렬해줘" }), true);
  // 워크북이 없으면 보낼 데가 없다 — 채팅이 맞다.
  assert.equal(decideExcelRoute({ message: "지역별로 합계 내줘" }), false);
});

test("사이드카 상태를 못 읽었으면 채팅으로 간다", () => {
  // 사이드카가 죽었을 때 모든 문장이 실패로 끝나는 것보다 낫다.
  assert.equal(
    decideExcelRoute({ message: "합계 내줘", workbookAvailable: false }),
    false
  );
});

test("빈 문장은 엑셀로 보내지 않는다", () => {
  assert.equal(shouldRouteToExcelLive(""), false);
  assert.equal(shouldRouteToExcelLive("   "), false);
  assert.equal(decideExcelRoute({ message: "", workbookAvailable: false }), false);
});

test("드래그 범위 태그가 붙으면 무조건 엑셀이다", () => {
  assert.equal(shouldRouteToExcelLive("[[EXCEL_RANGE:A1:C5]] 여기 정렬"), true);
});

test("엑셀 아님 응답을 알아본다", () => {
  assert.equal(
    isChatFallbackResponse({ action: "excel_live.not_excel_request" }),
    true
  );
  assert.equal(isChatFallbackResponse({ action: "excel_live.sort_range" }), false);
  assert.equal(isChatFallbackResponse(null), false);
  assert.equal(isChatFallbackResponse(undefined), false);
});

test("안전 정지 응답을 알아본다", () => {
  assert.equal(isSafetyStopResponse({ action: "excel_live.safety_stop" }), true);
  assert.equal(isSafetyStopResponse({ action: "excel_live.clear_range" }), false);
  assert.equal(isSafetyStopResponse(null), false);
});

test("두 응답 판별은 서로 겹치지 않는다", () => {
  const notExcel = { action: "excel_live.not_excel_request" };
  const safety = { action: "excel_live.safety_stop" };
  assert.equal(isSafetyStopResponse(notExcel), false);
  assert.equal(isChatFallbackResponse(safety), false);
});
