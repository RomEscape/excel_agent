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
export function buildPasteBlock(text, address) {
  const { rows, cols } = pasteShape(text);
  const ref = String(address || "").toUpperCase();
  if (!ref) return String(text ?? "");
  // 안내 문구는 반드시 **제거 가능한 표시** 안에 둔다.
  //
  // 2026-08-17 실측: 괄호 문구를 맨 텍스트로 뒀더니 `stripExcelContextBlock`이
  // 못 지워서 명령문에 그대로 섞였다:
  //   "(엑셀에서 붙여넣은 9행 × 4열 — A1:D9 범위로 인식했습니다) 여기를 원래대로…"
  // 게다가 그 안의 `A1:D9`가 "문장에 범위가 있다"로 잡혀 context_range 전달까지
  // 막았다. 사용자에게 보여 줄 말과 모델에게 보낼 말은 갈라 놓아야 한다.
  return [
    `[[EXCEL_RANGE:${ref}]]`,
    `[[EXCEL_PASTE_NOTE]]엑셀에서 붙여넣은 ${rows}행 × ${cols}열 — ${ref} 범위로 인식했습니다[[/EXCEL_PASTE_NOTE]]`,
  ].join("\n");
}
