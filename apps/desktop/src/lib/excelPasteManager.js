/**
 * excelPasteManager — Excel 셀 복사 → 채팅창 붙여넣기 배지의 액션 소유자.
 *
 * 2026-09-06 감사: 붙여넣기 훅이 워크스페이스 인라인 채팅 한 곳에만 있어서, 홈에서
 * 대화를 시작하면 기본이 되는 우측 ChatPanel 에서는 Excel 표가 원시 탭 텍스트로
 * 그냥 전송됐다(chat_log 17:21~17:25 실측). 판정(lib/excelPaste.decidePasteBlock)은
 * 순수 함수로 두고, 여기는 사이드카 왕복과 마지막 범위 보관만 한다. 두 채팅창이
 * 같은 함수를 부른다 — 한쪽만 고치는 사고를 막는 유일한 방법이다.
 */
import { excelLiveSelection, traceClientEvent } from "@/lib/api";
import { decidePasteBlock } from "@/lib/excelPaste.js";

/** 마지막으로 붙여넣기에서 인식한 Excel 범위 — 되묻기·문맥 범위에 쓴다. */
let lastPasteRef = "";

export function getLastExcelPasteRef() {
  return lastPasteRef;
}

/**
 * 붙여넣은 텍스트를 Excel 선택과 대조해 채팅창에 넣을 것을 정한다.
 *
 * @param {string} pasted
 * @param {{ sessionId?: string }} [opts]
 * @returns {Promise<ReturnType<typeof decidePasteBlock>>}
 */
export async function probeExcelPaste(pasted, opts = {}) {
  // 주소 조회는 전용 경량 엔드포인트다. 전체 명령 파이프라인을 타면 LLM 이 바쁠 때
  // 수십 초가 걸려 붙여넣기가 조용히 죽는다(2026-08-17 실측).
  // Excel 이 꺼져 있거나 사이드카가 죽어 있으면 null — 판정이 "못 읽음"으로 처리한다.
  const selection = await excelLiveSelection().catch(() => null);
  const decision = decidePasteBlock(pasted, selection);
  if (decision.address) lastPasteRef = decision.address;
  // 붙여넣기 사고("복붙했는데 값이 안 들어간다")는 이 값들이 있어야 재현된다.
  try {
    Promise.resolve(
      traceClientEvent({
        kind: "paste_probe",
        session_id: String(opts.sessionId || ""),
        detail: {
          address: decision.address || "(없음)",
          raw_address: selection?.raw_address ?? null,
          has_real_selection: selection?.has_real_selection ?? null,
          selection_empty: selection?.empty ?? null,
          keep_values: decision.keepValues,
          kind: decision.kind,
          pasted_chars: String(pasted ?? "").length,
        },
      }),
    ).catch(() => {});
  } catch {
    /* 로그는 편의다 */
  }
  return decision;
}

/** 입력창의 기존 글 뒤에 블록을 붙인다 — 줄 끝이면 그대로, 아니면 줄을 바꿔서. */
export function appendPasteBlock(prev, block) {
  const before = String(prev ?? "");
  const add = String(block ?? "");
  if (!before) return add;
  if (!add) return before;
  return before.endsWith("\n") ? `${before}${add}` : `${before}\n${add}`;
}
