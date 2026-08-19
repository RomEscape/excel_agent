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
  // 값 격자(쉼표·세미콜론·탭·줄바꿈 셋 이상 + 끝의 쓰기 동사)는 한 명령이다 — 값 속 "성실하고", "그리고"로
  // 쪼개면 표 절반이 잘려 나간다(2026-08-19 ex17 실측: 11행 붙여넣기가 "…우수, 성실"에서 끊겼다).
  if (looksLikeValueGrid(text)) return [text];

  const separators = [
    /\s+그리고\s+/i,
    /\s+그리고나서\s+/i,
    /\s*하고나서\s*/i,
    /\s*한\s*다음(?:에)?\s*/i,
    /\s*한\s*뒤(?:에)?\s*/i,
    /\s*후에\s*/i,
    // "그 다음 줄에 …"의 다음은 순서가 아니라 자리다 — 줄·행·칸·열·시트·표가 뒤따르면 쪼개지 않는다
    // (2026-08-19 ex9 v2 실측: "아 그리고 …"가 "아" + 명령으로 갈려 "아"만 전송됐다).
    /\s+그\s*다음(?:으로)?\s+(?!(?:줄|행|칸|열|시트|탭|표|페이지|단계))/i,
    /\s+다음(?:으로)?\s+(?!(?:줄|행|칸|열|시트|탭|표|페이지|단계))/i,
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
    .filter(Boolean)
    // 추임새만 남은 조각("아", "음", "ㅇㅇ", "그럼")은 명령이 아니다 — 보내면 되묻기가 나고 뒤 조각이 막힌다.
    .filter((p) => !FILLER_ONLY_PART.test(p));
}

export function looksLikeValueGrid(text) {
  const t = String(text ?? "");
  const seps = (t.match(/[,;\t\n]/g) || []).length;
  return seps >= 3 && /(입력|기록|넣어|채워|써|적어)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐|조|주십시오)?\s*[~.!?…]*\s*$/.test(t);
}

const FILLER_ONLY_PART =
  /^(?:(?:아|어|음|응|네|넵|옙|예|ㅇㅇ|ㅇㅋ|ㅋㅋ+|ㅎㅎ+|흠|오|아하|좋아|그래|ok|okay|그럼|자|그|이|저|일단|먼저|우선|그냥|아니|근데|그런데|그리고|그리구|또)[\s,.!~]*)+$/i;
