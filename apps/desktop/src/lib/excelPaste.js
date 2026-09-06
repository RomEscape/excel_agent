// Excel에서 복사한 셀을 채팅창에 붙여넣었을 때를 알아본다.
//
// 왜 필요한가 (2026-08-17 사용자 지적):
//   "D2:D5 이런 식으로 정확한 좌표를 찍어서 알려달라고 하지는 않을거 같은데...
//    아님 복사하기 하는 식으로 셀 복사해서 채팅창에 옮겨 넣기 이런 식으로
//    진행하는게 일반적이지 않을까?"
//
//   맞는 지적이다. 지금은 onPaste 핸들러가 없어서 붙여넣은 표가 그냥 긴 문장이
//   된다. 탭과 줄바꿈이 잔뜩 든 텍스트를 명령으로 읽으려 하니 될 리가 없다.
//
// 핵심 착안:
//   Excel에서 Ctrl+C 한 순간 **그 범위가 곧 현재 선택**이다. 그러니 붙여넣은
//   값을 파싱해 보낼 필요가 없다 — Excel에게 "지금 선택이 어디냐"고 물어
//   주소로 바꾸면 된다. 값은 백엔드가 실제 워크북에서 직접 읽는다.

/** 탭으로 나뉜 표처럼 보이는가 (Excel 클립보드의 형태). */
export function looksLikeExcelPaste(text) {
  const raw = String(text ?? "");
  if (!raw.includes("\t")) return false;

  const lines = raw.replace(/\r\n?/g, "\n").split("\n").filter((l) => l.length > 0);
  if (lines.length === 0) return false;

  // 한 줄짜리라도 탭이 있으면 여러 칸을 복사한 것이다.
  if (lines.length === 1) return lines[0].split("\t").length >= 2;

  // 여러 줄이면 열 수가 대체로 일정해야 표다. 산문에 우연히 탭이 든 경우를 거른다.
  const widths = lines.map((l) => l.split("\t").length);
  const first = widths[0];
  if (first < 2) return false;
  return widths.every((w) => w === first);
}

/**
 * 붙여넣기가 "Excel 선택 영역"일 가능성이 있어 주소를 물어봐야 하는가.
 *
 * 2026-08-25 실측(사용자 스크린샷 "저기 위치의 셀 정보가 입력이 안되는데?"):
 * Excel은 **빈 범위**를 복사하면 클립보드에 탭 격자가 아니라 `\r\n` 두 글자만 넣는다
 * (A1에만 값이 있는 A1:D6도 `x\r\n` — 뒤쪽 빈 열·행을 잘라낸다). 탭만 보는
 * `looksLikeExcelPaste`는 여기서 떨어져 기본 붙여넣기(공백 한 줄)가 되고, "여기에
 * 입력해줘" 흐름이 시작조차 못 했다. 08-19의 그 흐름은 러너가 붙여넣기를 흉내 낸
 * 것이라 실제 클립보드의 이 성질을 밟지 않았다.
 *
 * 공백만 붙여넣는 데는 다른 뜻이 없으므로, 공백뿐이면 Excel에 "지금 선택이 어디냐"를
 * 물어 본다. 주소가 없으면 호출부가 안내 문구로 처리한다.
 */
export function isExcelSelectionPaste(text) {
  const raw = String(text ?? "");
  if (looksLikeExcelPaste(raw)) return true;
  return raw.length > 0 && raw.trim() === "";
}

/**
 * 값이 든 **한 칸**을 복사한 클립보드처럼 보이는가 — Excel 은 한 칸을 복사하면
 * `값\r\n`(Mac 은 `값\r`) 한 줄을 넣는다. 탭도 공백도 없어서 예전 관문은 이걸
 * 문장으로 취급했고, "이 칸부터 넣어 줘"라고 한 칸을 복사하는 흐름이 막혔다
 * (2026-09-06 사용자: "셀 복사 붙여넣기 해도 범위 표기가 안 보인다").
 *
 * 다른 앱에서 복사한 한 줄도 같은 모양이므로 이것만으로 배지를 붙이지는 않는다 —
 * 사이드카가 돌려준 **원시 선택 한 칸의 값**과 글자가 같을 때만 배지다(decidePasteBlock).
 */
export function looksLikeSingleCellPaste(text) {
  const raw = String(text ?? "");
  return /^[^\t\r\n]+(\r\n|\r|\n)$/.test(raw) && raw.trim() !== "";
}

/** 붙여넣기 훅이 기본 동작을 막고 프로브를 시도할 가치가 있는가. */
export function isExcelClipboardCandidate(text) {
  return isExcelSelectionPaste(text) || looksLikeSingleCellPaste(text);
}

/**
 * 붙여넣기 + Excel 선택 프로브 결과 → 채팅창에 넣을 것(순수 — 두 채팅창이 같이 쓴다).
 *
 * @param {string} pasted 클립보드 text/plain
 * @param {{address?: string, raw_address?: string, has_real_selection?: boolean, empty?: boolean|null, single_value?: string|null}|null} selection
 *   사이드카 GET /excel-live/selection 응답. null 이면 Excel 을 못 읽은 것.
 * @returns {{kind: "badge"|"text"|"unreadable", block: string, address: string, keepValues: boolean}}
 *   badge — block 을 입력창에 넣는다(마크업). text — 붙여넣은 글자를 그대로 넣는다.
 *   unreadable — 넣을 것이 공백뿐이고 Excel 도 못 읽었다: 호출부가 이유를 말한다.
 */
export function decidePasteBlock(pasted, selection) {
  const text = String(pasted ?? "");
  const sel = selection && typeof selection === "object" ? selection : {};
  const address = String(sel.address || "").toUpperCase();
  // 옛 사이드카는 has_real_selection 을 안 보낸다 — 그때는 주소가 있으면 진짜로 본다.
  const real = sel.has_real_selection === undefined ? Boolean(address) : Boolean(sel.has_real_selection && address);
  const asText = { kind: "text", block: text, address: "", keepValues: false };

  if (looksLikeSingleCellPaste(text)) {
    const raw = String(sel.raw_address || "").toUpperCase();
    const value = sel.single_value == null ? null : String(sel.single_value);
    if (real && raw && !raw.includes(":") && value !== null && value.trim() === text.trim()) {
      return { kind: "badge", block: buildPasteBlock(text, raw, { single: true }), address: raw, keepValues: false };
    }
    return asText;
  }

  const blank = text.length > 0 && text.trim() === "";
  if (blank) {
    if (!real) return { kind: "unreadable", block: "", address: "", keepValues: false };
    return { kind: "badge", block: buildPasteBlock(text, address), address, keepValues: false };
  }

  if (!looksLikeExcelPaste(text)) return asText;
  if (!real) return asText; // Excel 을 못 읽으면 값을 버리지 않는다 — 표를 그대로 둔다
  const keepValues = sel.empty === true && pasteHasValues(text);
  return { kind: "badge", block: buildPasteBlock(text, address, { keepValues }), address, keepValues };
}

/** 붙여넣은 표의 크기. 미리보기 문구에 쓴다. */
export function pasteShape(text) {
  const lines = String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .filter((l) => l.length > 0);
  if (lines.length === 0) return { rows: 0, cols: 0 };
  return { rows: lines.length, cols: lines[0].split("\t").length };
}

/**
 * 붙여넣기를 채팅창에 넣을 문구로 바꾼다.
 *
 * 주소를 알아냈으면 범위 참조로, 못 알아냈으면 붙여넣은 표를 그대로 둔다 —
 * Excel이 꺼져 있거나 다른 앱에서 복사한 경우가 있다.
 */
/** `A1:D13` → {rows: 13, cols: 4}. 한 칸(`B2`)이나 못 읽는 표기는 null. */
export function rangeShape(ref) {
  const m = String(ref || "").toUpperCase().match(/^([A-Z]{1,3})(\d{1,7}):([A-Z]{1,3})(\d{1,7})$/);
  if (!m) return null;
  const col = (s) => [...s].reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0);
  const rows = Math.abs(Number(m[4]) - Number(m[2])) + 1;
  const cols = Math.abs(col(m[3]) - col(m[1])) + 1;
  return rows > 0 && cols > 0 ? { rows, cols } : null;
}

/** 붙여넣은 표에 값이 하나라도 있는가 — 빈 셀만 복사한 경우와 가른다. */
export function pasteHasValues(text) {
  return String(text ?? "")
    .split(/\r\n?|\n/)
    .some((line) => line.split("\t").some((cell) => cell.trim() !== ""));
}

/** 붙여넣은 표를 줄바꿈·탭만 남긴 깨끗한 TSV로. 끝의 빈 줄은 뗀다. */
export function normalizeTsv(text) {
  return String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \u00a0]+$/g, ""))
    .filter((line, idx, arr) => !(idx === arr.length - 1 && line.trim() === ""))
    .join("\n")
    .replace(/\n+$/g, "");
}

/**
 * 붙여넣기를 채팅창에 넣을 문구로 바꾼다.
 *
 * 기본(같은 통합문서에서 복사): 값을 버리고 주소만 남긴다 — 값은 백엔드가 그 범위를
 * 읽으면 되고, 표를 문장에 섞으면 명령이 읽히지 않는다.
 *
 * `keepValues`(다른 앱·통합문서에서 가져온 데이터 — 선택 영역이 비어 있는데 붙여넣은
 * 표에는 값이 있을 때): 값을 **탭·줄바꿈 그대로** 이어 붙인다. 사람이 뒤에 "입력해줘"만
 * 붙이면 사이드카의 붙여넣기 쓰기 규칙이 탭을 칸, 줄을 행으로 읽어 그 범위(또는 한 칸
 * 선택이면 그 칸부터)에 쓴다(2026-08-19 붙여넣기 흐름 강건화 — 전에는 값이 통째로
 * 사라져 "복붙한 값이 안 들어간다"가 됐다).
 */
export function buildPasteBlock(text, address, options = {}) {
  const ref = String(address || "").toUpperCase();
  if (!ref) return String(text ?? "");
  const keepValues = Boolean(options && options.keepValues);
  if (options && options.single) {
    // 값이 든 한 칸 — 값은 그 칸에 이미 있으니 주소만 남긴다.
    return [`[[EXCEL_RANGE:${ref}]]`, `[[EXCEL_PASTE_NOTE]]엑셀에서 복사한 ${ref} 한 칸으로 인식했습니다[[/EXCEL_PASTE_NOTE]]`].join("\n");
  }
  // 행×열은 **범위에서** 센다. 붙여넣은 텍스트로 세면 빈 줄이 걸러져
  // "9행 × 4열 — A1:D13"처럼 서로 안 맞는 숫자가 나간다(2026-08-17 실측 —
  // 사용자가 "인식되는 범위도 다르고"라고 지적한 그 화면). 값은 어차피
  // 백엔드가 범위로 읽으므로 범위가 기준이다. 값을 살려 보낼 때는 표 자체가 기준이다.
  const { rows, cols } = keepValues ? pasteShape(text) : rangeShape(ref) || pasteShape(text);
  // 안내 문구는 반드시 **제거 가능한 표시** 안에 둔다.
  //
  // 2026-08-17 실측: 괄호 문구를 맨 텍스트로 뒀더니 `stripExcelContextBlock`이
  // 못 지워서 명령문에 그대로 섞였다:
  //   "(엑셀에서 붙여넣은 9행 × 4열 — A1:D9 범위로 인식했습니다) 여기를 원래대로…"
  // 게다가 그 안의 `A1:D9`가 "문장에 범위가 있다"로 잡혀 context_range 전달까지
  // 막았다. 사용자에게 보여 줄 말과 모델에게 보낼 말은 갈라 놓아야 한다.
  const note = keepValues
    ? `밖에서 가져온 표 ${rows}행 × ${cols}열 — ${ref}부터 넣습니다`
    : `엑셀에서 붙여넣은 ${rows}행 × ${cols}열 — ${ref} 범위로 인식했습니다`;
  const lines = [`[[EXCEL_RANGE:${ref}]]`, `[[EXCEL_PASTE_NOTE]]${note}[[/EXCEL_PASTE_NOTE]]`];
  if (keepValues) lines.push(normalizeTsv(text));
  return lines.join("\n");
}

/**
 * 저장·말풍선에 쓸 사람용 문구.
 *
 * 2026-08-17 실측(스크린샷): `[[EXCEL_RANGE:A1:D9]] [[EXCEL_PASTE_NOTE]]…`가
 * 사용자 말풍선에 **그대로** 떴다. 마크업은 모델과의 약속이지 사람에게 보일 말이
 * 아니다. 마크업을 만든 이 모듈이 표시용 변환도 책임진다 — 두 표기가 다른 파일에
 * 흩어지면 한쪽만 고치는 사고가 난다(오늘 그랬다).
 */
export function displayMessageText(text) {
  const raw = String(text ?? "");
  if (!raw.includes("[[EXCEL_")) return raw;
  const note = raw.match(/\[\[EXCEL_PASTE_NOTE\]\]([\s\S]*?)\[\[\/EXCEL_PASTE_NOTE\]\]/i);
  const cleaned = raw
    .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
    .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
    .replace(/\[\[EXCEL_PASTE_NOTE\]\][\s\S]*?\[\[\/EXCEL_PASTE_NOTE\]\]/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const prefix = note ? `📋 ${note[1].trim()}` : "";
  return [prefix, cleaned].filter(Boolean).join("\n");
}
