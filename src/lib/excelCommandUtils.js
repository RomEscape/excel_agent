/**
 * Excel Live 복합 자연어를 단계별 명령으로 분해한다.
 * 예: "C3에 777 입력하고 D열 0이하는 파란색 표시 후 저장해줘"
 */
export function splitExcelCompositeCommand(rawMessage) {
  const text = String(rawMessage ?? "").replace(/\s+/g, " ").trim();
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

