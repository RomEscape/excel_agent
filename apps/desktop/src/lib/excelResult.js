/**
 * excelResult — Excel Live 실행 결과를 "표시 모델"로 번역하는 순수 모듈.
 *
 * 왜 분리했나:
 *   기존에는 WorkspacePage 안의 formatExcelLiveResult()가 action+result를 곧바로
 *   한 줄 문자열로 눌러버렸다. 그래서 read_range가 실제로 값 배열(values)을 들고
 *   와도 "A1:C3 범위를 읽었습니다"라는 문장만 남고 데이터는 버려졌다.
 *
 *   UI가 표/차트를 그리려면 그 데이터가 살아있어야 하므로, 이 모듈은 문자열 대신
 *   구조화된 뷰모델을 돌려준다. 렌더는 components/ui/result-card.jsx가 맡는다.
 *   (데이터 ↔ 표시 분리 — CLAUDE.md 가이드라인 2)
 *
 * 반환 형태 (kind로 판별):
 *   { kind: "sheet",  summary, address, rowCount, colCount, columns, rows, truncated }
 *   { kind: "bars",   summary, title, items: [{label, value}], max }
 *   { kind: "stat",   summary, label, value }
 *   { kind: "text",   summary }
 *
 * summary는 항상 채워진다 — 카드가 렌더 못 하는 환경(메신저 전송, 세션 영속화)에서
 * 그대로 쓰는 폴백 문장이다. 즉 이 모듈은 기존 문자열 동작의 상위집합이다.
 */

/** 표 카드 미리보기 상한 — 목업 기준 헤더 1행 + 데이터 몇 행. */
export const SHEET_PREVIEW_MAX_ROWS = 12;
export const SHEET_PREVIEW_MAX_COLS = 8;

/** 막대 카드에 그릴 최대 항목 수. 그 이상은 summary에만 남긴다. */
export const BARS_MAX_ITEMS = 8;

/**
 * 0-based 열 인덱스 → 엑셀 열 문자 (0→A, 25→Z, 26→AA).
 * 표 카드 헤더에 A/B/C를 찍기 위해 필요하다.
 */
export function columnLetter(index) {
  let n = Number(index);
  if (!Number.isInteger(n) || n < 0) return "";
  let out = "";
  do {
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return out;
}

/**
 * read_range 결과의 시작 셀에서 열 오프셋을 얻는다.
 * "C2:E10" → 2 (C는 0-based로 2). 파싱 실패 시 0.
 *
 * 이걸 안 하면 C열부터 읽은 범위인데 표 헤더가 A부터 시작해서
 * 사용자가 실제 시트와 대조할 때 어긋난다.
 */
export function startColumnIndex(address) {
  const m = String(address || "").match(/^([A-Z]{1,3})/i);
  if (!m) return 0;
  const letters = m[1].toUpperCase();
  let n = 0;
  for (const ch of letters) {
    n = n * 26 + (ch.charCodeAt(0) - 64);
  }
  return n - 1;
}

/** 시작 행 번호. "C2:E10" → 2. 파싱 실패 시 1. */
export function startRowNumber(address) {
  const m = String(address || "").match(/^[A-Z]{1,3}(\d{1,7})/i);
  return m ? Number(m[1]) : 1;
}

function toCellText(cell) {
  if (cell == null) return "";
  return String(cell);
}

/**
 * 숫자로 해석 가능한 값만 숫자로. "1,234" 같은 천단위 구분자도 받는다.
 * 막대 길이 계산에 쓰이므로 해석 실패는 null로 돌려 렌더에서 제외한다.
 */
export function toNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const cleaned = value.replace(/,/g, "").trim();
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** 표 미리보기 모델 — values 2차원 배열을 헤더행 + 데이터행으로 자른다. */
function buildSheetView(result) {
  const address = String(result.address || "");
  const values = Array.isArray(result.values) ? result.values : [];
  const rowCount = Number(result.row_count || values.length || 0);
  const colCount = Number(
    result.col_count || (Array.isArray(values[0]) ? values[0].length : 0) || 0
  );

  const sliced = values
    .slice(0, SHEET_PREVIEW_MAX_ROWS)
    .map((row) =>
      (Array.isArray(row) ? row : [row])
        .slice(0, SHEET_PREVIEW_MAX_COLS)
        .map(toCellText)
    );

  const colOffset = startColumnIndex(address);
  const rowOffset = startRowNumber(address);
  const widest = sliced.reduce((max, row) => Math.max(max, row.length), 0);

  // 표 헤더는 엑셀 실제 열 문자(A/B/C)를 그대로 쓴다 — 시트와 대조 가능해야 함.
  const columns = Array.from({ length: widest }, (_, i) => columnLetter(colOffset + i));
  const rows = sliced.map((cells, i) => ({ number: rowOffset + i, cells }));

  return {
    kind: "sheet",
    summary: `${address} 범위를 읽었습니다 (${rowCount}행 × ${colCount}열).`,
    address,
    rowCount,
    colCount,
    columns,
    rows,
    truncated: rowCount > SHEET_PREVIEW_MAX_ROWS || colCount > SHEET_PREVIEW_MAX_COLS,
  };
}

/** 막대 차트 모델 — group_by_aggregate의 groups를 값 내림차순으로 정렬. */
function buildBarsView(result) {
  const groups = Array.isArray(result.groups) ? result.groups : [];
  const groupColumn = String(result.group_column || "");
  const agg = String(result.agg || "");

  const items = groups
    .map((g) => ({ label: toCellText(g?.key), value: toNumber(g?.value) }))
    .filter((it) => it.label !== "" && it.value != null)
    .sort((a, b) => b.value - a.value);

  const shown = items.slice(0, BARS_MAX_ITEMS);
  // 막대 길이는 최댓값 대비 비율. 전부 0이면 0으로 나누지 않도록 1로 눕힌다.
  const max = shown.reduce((m, it) => Math.max(m, it.value), 0) || 1;

  const preview = groups
    .slice(0, 5)
    .map((g) => `${toCellText(g?.key)}: ${toCellText(g?.value)}`)
    .join(", ");

  return {
    kind: "bars",
    summary: `${groupColumn}별 ${agg} — ${preview}${groups.length > 5 ? " …" : ""}`,
    title: `${groupColumn}별 ${agg}`.trim(),
    items: shown,
    max,
    truncated: items.length > BARS_MAX_ITEMS,
  };
}

/** 단일 통계값 모델 — calculate_column_stat. */
function buildStatView(result) {
  const label = `${result.header || result.column || ""} ${result.stat || ""}`.trim();
  return {
    kind: "stat",
    summary: `${result.header || result.column || ""} 열 ${result.stat || ""} = ${result.value}`,
    label,
    value: toCellText(result.value),
  };
}

/** 카드 없이 문장만 있는 결과. */
function text(summary) {
  return { kind: "text", summary };
}

/**
 * Excel Live action + result → 표시 모델.
 *
 * 기존 formatExcelLiveResult()가 담당하던 모든 action의 문장을 그대로 유지하되,
 * 데이터를 들고 오는 3개 action(read_range · group_by_aggregate ·
 * calculate_column_stat)만 카드 모델로 승격한다.
 *
 * @param {string} action
 * @param {Record<string, unknown>} result
 * @returns {{kind: string, summary: string} & Record<string, unknown>}
 */
export function toResultView(action, result = {}) {
  if (!result || typeof result !== "object") {
    return text("엑셀 작업이 완료되었습니다.");
  }

  switch (action) {
    case "excel_live.read_range":
      return buildSheetView(result);
    case "excel_live.group_by_aggregate":
      return buildBarsView(result);
    case "excel_live.calculate_column_stat":
      return buildStatView(result);

    case "excel_live.list_workbooks": {
      const rows = Array.isArray(result.workbooks) ? result.workbooks : [];
      if (rows.length === 0) return text("열려 있는 엑셀 통합문서가 없습니다.");
      return text(
        `열린 통합문서 ${rows.length}개: ${rows.map((r) => r.name || r.workbook_id).join(", ")}`
      );
    }
    case "excel_live.write_range":
      return text(
        `${result.address || ""} 범위에 ${result.written_cells || 0}개 셀을 기록했습니다.`
      );
    case "excel_live.highlight_by_condition":
      return text(
        `${result.address || ""} 범위에서 ${result.changed_cells || 0}개 셀을 강조했습니다.`
      );
    case "excel_live.apply_border":
      return text(
        `${result.address || ""} 범위에 경계선을 적용했습니다 (${result.changed_cells || 0}개 셀).`
      );
    case "excel_live.set_formula":
      return text(
        `${result.address || ""} 범위에 수식을 적용했습니다 (${result.formula_applied_cells || 0}개 셀).`
      );
    case "excel_live.save_workbook":
      return text(
        `엑셀 파일을 저장했습니다 (${result.name || result.full_path || "현재 통합문서"}).`
      );
    case "excel_live.filter_rows":
      return text(
        `조건에 맞는 ${result.kept_rows || 0}개 행을 남기고 ${result.removed_rows || 0}개 행을 제거했습니다.`
      );
    case "excel_live.sort_rows":
      return text(
        `${result.column || ""} 기준으로 ${result.sorted_rows || 0}개 행을 정렬했습니다.`
      );
    case "excel_live.dedupe_rows":
      return text(
        `중복 ${result.removed_duplicates || 0}개 행을 제거했습니다 (${result.kept_rows || 0}개 유지).`
      );
    case "excel_live.drop_column":
      return text(`'${result.dropped_column || ""}' 열을 삭제했습니다.`);
    case "excel_live.rename_column":
      return text(
        `'${result.old_name || ""}' 열을 '${result.new_name || ""}'로 변경했습니다.`
      );
    case "excel_live.add_column":
      return text(`'${result.name || ""}' 열을 추가했습니다.`);
    default:
      return text("엑셀 작업이 완료되었습니다.");
  }
}

/**
 * 기존 호출부 호환용 — 표시 모델의 summary만 뽑는다.
 * 세션 영속화·메신저 전송처럼 문자열이 필요한 경로가 그대로 쓴다.
 */
export function formatResultText(action, result = {}) {
  return toResultView(action, result).summary;
}
