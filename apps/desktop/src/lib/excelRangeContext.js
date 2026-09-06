/**
 * excelRangeContext — 입력창에 삽입되는 엑셀 범위 참조 블록을 다루는 순수 모듈.
 *
 * "범위 참조 삽입" 버튼은 사용자가 Excel에서 선택한 범위를 아래 형태의 블록으로
 * 입력창에 넣는다:
 *
 *   [[EXCEL_RANGE:A1:C3]]
 *   [[EXCEL_VALUES_TSV]]
 *   제품군\t매출
 *   클라우드\t3200
 *   [[/EXCEL_VALUES_TSV]]
 *
 * 전송 시에는 이 블록을 LLM에 그대로 보내지 않는다 — 주소만 명령문 앞에 붙이고
 * 값 블록은 떼어낸다. 그 규칙이 정규식 몇 개에 걸쳐 있어서 WorkspacePage 안에
 * 묻어두면 검증이 안 되므로 분리했다.
 */

/** 값 미리보기 상한 — 프롬프트가 과하게 길어지지 않도록 자른다. */
export const TSV_MAX_ROWS = 12;
export const TSV_MAX_COLS = 8;

/** 입력 텍스트에서 [[EXCEL_RANGE:..]] 주소를 뽑는다. 없으면 null. */
export function extractRangeTag(text) {
  const m = String(text || "").match(/\[\[EXCEL_RANGE:([A-Z0-9:]+)\]\]/i);
  return m ? m[1].toUpperCase() : null;
}

/**
 * 태그와 값 블록을 모두 제거한 순수 명령문만 남긴다.
 *
 * 붙여넣기 안내(`[[EXCEL_PASTE_NOTE]]…`)와, 말풍선에 남았던 사람용 안내 줄(`📋 …인식했습니다`,
 * 재시도·편집으로 되돌아온 문장)도 걷어낸다 — 2026-08-17 에 안내 문구가 명령에 섞여
 * 사이드카로 간 사고가 ChatPanel 경로에서 재현될 수 있었다(2026-09-06 감사 발견 7).
 */
export function stripContextBlock(text) {
  return String(text || "")
    .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
    .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
    .replace(/\[\[EXCEL_PASTE_NOTE\]\][\s\S]*?\[\[\/EXCEL_PASTE_NOTE\]\]/gi, "")
    .replace(/^📋 [^\n]*(인식했습니다|넣습니다)[ \t]*$/gm, "")
    .trim();
}

/** 명령문에 이미 셀/범위 주소가 적혀 있는지. */
export function hasExplicitRange(command) {
  return /\b([A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}|[A-Z]{1,3}:[A-Z]{1,3}|[A-Z]{1,3}\d{1,7})\b/i.test(
    String(command || "")
  );
}

/**
 * 삽입된 범위를 명령문에 반영한다.
 *
 * 무조건 앞에 붙이지 않는 이유: 사용자가 "A1:C3 읽어줘"처럼 주소를 직접 썼거나
 * 범위와 무관한 질문("이 파일 요약해줘")을 했을 수 있다. 그래서 지시대명사가
 * 있을 때만("이 범위", "여기" 등) 주소를 앞에 세운다.
 */
export function applyRangeToCommand(command, rangeRef) {
  const text = String(command || "").trim();
  if (!rangeRef || !text || hasExplicitRange(text)) return text;
  if (!/(이\s*범위|해당\s*범위|복사한\s*범위|선택한\s*범위|여기)/i.test(text)) return text;
  return `${rangeRef} ${text}`;
}

/** 2차원 값 배열 → TSV 문자열 (상한까지만). */
export function stringifyTsv(values, maxRows = TSV_MAX_ROWS, maxCols = TSV_MAX_COLS) {
  if (!Array.isArray(values) || values.length === 0) return "";
  return values
    .slice(0, maxRows)
    .map((row) =>
      (Array.isArray(row) ? row.slice(0, maxCols) : [row])
        .map((cell) => (cell == null ? "" : String(cell)))
        .join("\t")
    )
    .join("\n");
}

/**
 * read_range 결과 → 입력창에 넣을 참조 블록.
 *
 * @param {{address?: string, row_count?: number, col_count?: number, values?: Array}} result
 * @returns {{block: string, address: string, rows: number, cols: number}}
 * @throws {Error} 주소를 못 얻으면 — 호출부가 사용자에게 안내해야 한다.
 */
export function buildRangeContextBlock(result = {}) {
  const address = String(result.address || "").toUpperCase();
  if (!address) {
    throw new Error("선택 범위 주소를 가져오지 못했습니다.");
  }
  const rows = Number(result.row_count || 0);
  const cols = Number(result.col_count || 0);
  const tsv = stringifyTsv(result.values);
  const hasMore = rows > TSV_MAX_ROWS || cols > TSV_MAX_COLS;

  const block = [
    `[[EXCEL_RANGE:${address}]]`,
    "[[EXCEL_VALUES_TSV]]",
    tsv || "(빈 범위)",
    hasMore
      ? `... (미리보기 제한: 최대 ${TSV_MAX_ROWS}행 x ${TSV_MAX_COLS}열, 실제 범위 ${rows}행 x ${cols}열)`
      : "",
    "[[/EXCEL_VALUES_TSV]]",
  ]
    .filter(Boolean)
    .join("\n");

  return { block, address, rows, cols };
}

/**
 * 전송 직전 정규화 — 입력 원문에서 LLM에 보낼 명령문을 만든다.
 * 블록이 없으면 원문 그대로다.
 */
export function toOutboundCommand(rawInput) {
  const trimmed = String(rawInput || "").trim();
  const rangeRef = extractRangeTag(trimmed);
  const cleaned = stripContextBlock(trimmed);
  return applyRangeToCommand(cleaned, rangeRef) || cleaned || trimmed;
}

// ─── 이하: 붙여넣기 흐름의 값 나열 감지(데모 브랜치) — WorkspacePage가 쓴다 ───

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
