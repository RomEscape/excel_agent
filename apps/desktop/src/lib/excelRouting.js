// 한 문장을 엑셀 경로로 보낼지 일반 채팅으로 보낼지 정한다.
//
// 2026-08-16까지 이 판단이 WorkspacePage 안에 인라인으로 있어서, 화면을 눌러 보지
// 않고는 확인할 방법이 없었다. 라우팅은 도메인 로직이므로 여기로 옮긴다(CLAUDE.md §4).
//
// 왜 기본값이 뒤집혔는가:
//   예전에는 키워드 화이트리스트가 통과시킨 문장만 엑셀로 갔다. 실측으로 자연스러운
//   엑셀 요청 22건 중 18건이, 평가셋 154건 중 83건(53.9%)이 일반 채팅으로 샜다.
//   "지역별로 묶어서 합계 내줘", "피벗으로 요약해줘", "이름순으로 정렬해줘"가 전부
//   구현된 액션인데 입구에서 막혔다. 그리고 새면 정체성 없는 모델을 만났다.
//
//   이제는 **워크북이 열려 있으면 엑셀이 기본**이고, "엑셀 일이 아니다"는 판정은
//   사이드카가 응답 본문으로 돌려준다. 단어 목록을 늘리는 방식은 같은 결함을
//   미룰 뿐이라 버렸다.

/** 문장만 보고 확실히 엑셀이라고 말할 수 있는가 (워크북 상태를 안 볼 때의 빠른 길). */
export function shouldRouteToExcelLive(message) {
  const text = String(message || "").trim();
  if (!text) return false;
  const lower = text.toLowerCase();
  if (/\[\[excel_range:[a-z0-9:]+\]\]/i.test(text)) return true;

  const keywordHit = [
    "엑셀", "excel", "워크북", "workbook", "시트", "sheet",
    "셀", "cell", "수식", "formula", "조건부", "강조", "경계선", "테두리", "border",
    "표", "테이블", "table", "헤더", "항목", "표 형태", "배경색", "색도", "색을", "색깔",
    "노란색", "노랑", "흰색", "하얀색", "하양", "white", "칠해",
  ].some((kw) => lower.includes(kw));
  if (keywordHit) return true;

  const tablePattern = /\b\d{1,3}\s*(?:\*|x|×)\s*\d{1,3}\s*(표|테이블|table)\b/i;
  const rangePattern = /\b[A-Z]{1,3}\d{1,7}(?::[A-Z]{1,3}\d{1,7})?\b/i;
  const columnPattern = /[a-zA-Z]\s*열/;
  return tablePattern.test(text) || rangePattern.test(text) || columnPattern.test(text);
}

/**
 * 이 턴을 엑셀 경로로 보낼 것인가.
 *
 * @param {object}  o
 * @param {string}  o.message             사용자 문장
 * @param {boolean} o.wasExcelFollowUp    직전 턴이 되묻기·승인대기·취소로 끝났는가
 * @param {boolean} o.workbookAvailable   지금 열려 있는 통합문서가 있는가
 *                                        (상태를 못 읽었으면 false로 준다)
 */
export function decideExcelRoute({ message, wasExcelFollowUp = false, workbookAvailable = false }) {
  // 되묻기 답변이 최우선이다. "일별로", "응 그렇게"는 그 자체로는 엑셀 문장처럼
  // 안 보이는데, 채팅으로 보내면 문맥이 끊겨 엉뚱한 답이 나온다.
  if (wasExcelFollowUp) return true;
  if (shouldRouteToExcelLive(message)) return true;
  // 워크북이 열려 있으면 엑셀이 기본. 아니라고 판정하는 건 사이드카의 몫이다.
  return Boolean(workbookAvailable);
}

/** 사이드카가 "이건 엑셀 일이 아니다"라고 돌려줬는가 → 일반 채팅으로 넘겨야 한다. */
export function isChatFallbackResponse(excelResult) {
  return excelResult?.action === "excel_live.not_excel_request";
}

/** 자해·고통 호소 응답인가 → 엑셀 되묻기로 받지 않고 그대로 보여 준다. */
export function isSafetyStopResponse(excelResult) {
  return excelResult?.action === "excel_live.safety_stop";
}
