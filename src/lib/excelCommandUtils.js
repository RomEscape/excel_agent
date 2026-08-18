/**
 * Excel Live 복합 자연어를 단계별 명령으로 분해한다.
 * 예: "C3에 777 입력하고 D열 0이하는 파란색 표시 후 저장해줘"
 */
export function splitExcelCompositeCommand(rawMessage) {
  // 줄바꿈은 살린다. 사람이 값을 여러 줄로 붙여넣으면("지역,주문건수⏎수도권,10452 입력해줘")
  // 줄이 곧 표의 행이다 — 공백으로 뭉개면 사이드카가 한 줄로 받아 칸이 밀린다
  // (2026-08-19 붙여넣기 흐름 강건화). 가로 공백만 하나로 줄인다.
  const text = String(rawMessage ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/[^\S\n\t]+/g, " ")
    .replace(/\n{2,}/g, "\n")
    .trim();
  if (!text) return [];

  const separators = [
    /\s+그리고\s+/i,
    /\s+그리고나서\s+/i,
    /\s*하고나서\s*/i,
    /\s*한\s*다음(?:에)?\s*/i,
    /\s*한\s*뒤(?:에)?\s*/i,
    /\s*후에\s*/i,
    /\s+그\s*다음(?:으로)?\s+/i,
    /\s+다음(?:으로)?\s+/i,
    /\s+이후\s+/i,
    /\s*하고\s+/i,
    /\s+then\s+/i,
    /\s+and then\s+/i,
  ];

  let parts = [text];
  for (const sep of separators) {
    parts = parts.flatMap((part) => part.split(sep));
  }

  return parts
    .map((p) => p.trim())
    .map((p) => p.replace(/^[,\s]+|[,\s]+$/g, ""))
    .filter(Boolean);
}
