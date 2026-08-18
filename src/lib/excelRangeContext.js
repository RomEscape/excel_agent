// 문장에 범위가 적혀 있는지 — 적혀 있으면 프론트는 lastExcelRangeRef(붙여넣기·직전
// 결과 주소)를 context_range로 보내지 않는다. 사이드카가 문장의 범위를 우선하기
// 때문에 대개 무해하지만, 오래된 문맥이 다른 경로로 새는 것을 여기서 막는다.
//
// 2026-08-19 붙여넣기 흐름 강건화: 값 나열 안의 셀 닮은 토큰("철근 (D25)", "단열재
// (T100)", "CMP-2607-021"의 앞부분)이 범위 지목으로 오인돼 붙여넣기 문맥이 통째로
// 빠졌다 — 그러면 사이드카는 어디에 쓸지 몰라 되묻거나 A1에 쓴다. 값 나열+쓰기 동사
// 문장에서는 **대상으로 부른 범위**(A1:F6, "B3에")만 범위로 본다.

const CELL_OR_RANGE =
  /\b([A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}|[A-Z]{1,3}:[A-Z]{1,3}|[A-Z]{1,3}\d{1,7})\b/i;
const TARGET_RANGE =
  /\b[A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}\b|\b[A-Z]{1,3}\d{1,7}\s*(?:에다가|에다|에|부터|까지)/i;
const WRITE_VERB_END =
  /(입력|기록|넣어|채워|써|적어)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐|조)?\s*[~.!?…]*\s*$/;

/** 값 나열(쉼표·세미콜론·탭·줄바꿈 셋 이상) + 쓰기 동사로 끝나는 문장인가. */
export function looksLikeValueListWrite(text) {
  const t = String(text ?? "");
  const separators = (t.match(/[,;\t\n]/g) || []).length;
  return separators >= 3 && WRITE_VERB_END.test(t);
}

/** 문장이 범위를 **직접** 지목하는가. */
export function hasExplicitRangeInCommand(cmd) {
  const text = String(cmd ?? "");
  if (looksLikeValueListWrite(text)) return TARGET_RANGE.test(text);
  return CELL_OR_RANGE.test(text);
}

/** "여기/이 범위"라고만 했으면 붙여넣기 태그의 범위를 문장 앞에 붙인다. */
export function applyRangeContextToCommand(cmd, rangeRef) {
  const text = String(cmd ?? "").trim();
  if (!rangeRef || !text || hasExplicitRangeInCommand(text)) return text;
  if (!/(이\s*범위|해당\s*범위|복사한\s*범위|선택한\s*범위|여기)/i.test(text)) return text;
  return `${rangeRef} ${text}`;
}
