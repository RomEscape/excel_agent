/**
 * chatManager — 에이전트 채팅 도메인의 액션 소유자.
 *
 * WorkspacePage의 ChatSidePanel 안에 있던 핸들러들을 그대로 끌어냈다. 동작은
 * 종전과 같고, 달라진 건 상태를 store에서 읽고 쓴다는 점뿐이다.
 *   - 메시지/세션 ID : appStore (기존 구독처 유지)
 *   - 세션 목록/진행 : chatStore
 *
 * UI는 이 모듈의 함수만 호출한다 — invoke를 직접 부르지 않는다
 * (CLAUDE.md 안티패턴: "컴포넌트 안에 fetch/invoke 직접 호출").
 */
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import { toUserMessage } from "@/lib/errorMessages";
import { formatResultText } from "@/lib/excelResult";
import { toToolSteps } from "@/lib/toolSteps";
import {
  toOutboundCommand,
  buildRangeContextBlock,
  extractRangeTag,
  hasExplicitRangeInCommand,
} from "@/lib/excelRangeContext";
import { displayMessageText } from "@/lib/excelPaste.js";
import {
  excelLiveCommand,
  excelLiveSubmitApproval,
  excelLiveSaveWorkbook,
  chatSaveMessage,
  chatListSessions,
  chatGetMessages,
  chatDeleteSession,
} from "@/lib/api";

/** 사이드바에 불러올 세션 개수 — 저장은 무한(로그·DB에 자동 삭제 없음)이고
 * 이것은 표시 상한일 뿐이다. 30이면 옛 대화가 사라진 것처럼 보였다(2026-09-01). */
const SESSION_LIST_LIMIT = 200;

const app = () => useAppStore.getState();
const chat = () => useChatStore.getState();

/**
 * 메시지 영속화는 실패해도 대화를 막지 않는다.
 * sidecar가 chat_history를 지원하지 않는 빌드에서도 채팅 자체는 되어야 한다.
 */
function persistSilent(sessionId, role, text, extra = {}) {
  if (!sessionId) return;
  chatSaveMessage(
    sessionId,
    role,
    text ?? "",
    extra.toolCalls ?? null,
    extra.maskedCount ?? null,
    extra.maskedTypes ?? null,
    extra.errorText ?? null
  ).catch(() => {});
}

/**
 * 말풍선에 찍을 시각.
 *
 * 전송 시점에 프론트에서 박는다 — sidecar 응답에는 시각이 없고, 렌더 시점에
 * new Date()를 부르면 스크롤할 때마다 시간이 바뀐다.
 */
function stamp() {
  return new Date().toISOString();
}

// ── 세션 ────────────────────────────────────────────────────────────────────

/** 세션 목록 새로고침. sidecar 미지원이면 sessionsAvailable을 내린다.

 * 첫 구동에서는 사이드카보다 먼저 호출돼 반드시 진다 — 재시도가 없으면
 * "대화 기록을 사용할 수 없습니다"가 박제된다(2026-09-01 첫 구동 실측).
 * 부팅 레이스 한정으로 짧게 재시도한다.
 */
let sessionsRetryTimer = 0;
let sessionsRetriesLeft = 15;

export async function refreshSessions() {
  const { setSessions, setSessionsLoading, setSessionsAvailable } = chat();
  setSessionsLoading(true);
  if (typeof window !== "undefined") window.clearTimeout(sessionsRetryTimer);
  try {
    const list = await chatListSessions(SESSION_LIST_LIMIT);
    setSessions(Array.isArray(list) ? list : list?.sessions || []);
    setSessionsAvailable(true);
    sessionsRetriesLeft = 15;
  } catch {
    setSessions([]);
    setSessionsAvailable(false);
    if (sessionsRetriesLeft > 0 && typeof window !== "undefined") {
      sessionsRetriesLeft -= 1;
      sessionsRetryTimer = window.setTimeout(() => refreshSessions(), 2000);
    }
  } finally {
    setSessionsLoading(false);
  }
}

/** 저장된 세션을 화면으로 불러온다. */
export async function loadSession(sessionId) {
  const { setAgentMessages, setActiveSessionId, addAgentMessage } = app();
  try {
    const msgs = await chatGetMessages(sessionId);
    const list = Array.isArray(msgs) ? msgs : msgs?.messages || [];
    // sidecar의 snake_case → 프론트 camelCase 정규화
    setAgentMessages(
      list.map((m) => ({
        role: m.role,
        text: m.text,
        toolCalls: m.tool_calls,
        maskedCount: m.masked_count,
        maskedTypes: m.masked_types,
        // 저장된 시각이 없으면 말풍선이 타임스탬프를 안 그린다 (빈 문자열 취급).
        time: m.created_at || m.timestamp || undefined,
        error: m.error_text || undefined,
      }))
    );
    setActiveSessionId(sessionId);
  } catch (err) {
    addAgentMessage({
      role: "system",
      text: `세션을 불러올 수 없습니다 — ${toUserMessage(err)}`,
    });
  }
}

/** 세션 삭제. 현재 보고 있던 세션이면 화면도 비운다. */
export async function deleteSession(sessionId) {
  const { activeSessionId, setActiveSessionId, setAgentMessages, addAgentMessage } = app();
  try {
    await chatDeleteSession(sessionId);
    if (activeSessionId === sessionId) {
      setActiveSessionId(null);
      setAgentMessages([]);
    }
    await refreshSessions();
  } catch (err) {
    addAgentMessage({
      role: "system",
      text: `세션 삭제에 실패했습니다 — ${toUserMessage(err)}`,
    });
  }
}

/** 새 대화 — 화면을 비우고 세션 ID를 놓는다. */
export function startNewSession() {
  const { setActiveSessionId, setAgentMessages } = app();
  const { setToolSteps } = chat();
  setActiveSessionId(null);
  setAgentMessages([]);
  // 스텝 칩까지 비워야 한다 — 남으면 새 대화 첫 화면에 지난 턴의 진행이 떠 있다.
  setToolSteps([]);
  // 직전 세션이 목록에 반영될 시간을 준 뒤 갱신
  setTimeout(() => refreshSessions(), 100);
}

/**
 * 특정 메시지 지점으로 되돌린다 — 와이어프레임의 말풍선 `arrow-back` 액션.
 *
 * 그 메시지부터 뒤를 전부 잘라내고, 해당 턴의 사용자 요청을 다시 보낸다.
 * AI 말풍선에서 눌렀으면 그 앞의 사용자 메시지를 찾아 그 지점부터 자른다 —
 * AI 응답만 지우면 같은 요청을 다시 보낼 방법이 없다.
 *
 * @param {number} index 되돌릴 메시지의 위치
 * @param {{send?: boolean}} [options] send:false면 잘라내기만 한다 (편집용)
 */
export function retryFromMessage(index, options = {}) {
  const { send = true } = options;
  const { agentMessages, setAgentMessages } = app();
  const { setToolSteps } = chat();

  if (!Array.isArray(agentMessages) || index < 0 || index >= agentMessages.length) return;

  // 자를 지점 = 이 메시지가 속한 턴의 사용자 메시지.
  let userIdx = index;
  while (userIdx >= 0 && agentMessages[userIdx].role !== "user") userIdx -= 1;
  if (userIdx < 0) return;

  // 재시도는 사람용 문구(📋 안내 포함)가 아니라 입력 원문(마크업 포함)으로 — 안내가 명령에 섞이지 않게.
  const prompt = String(agentMessages[userIdx].raw ?? agentMessages[userIdx].text ?? "");
  setAgentMessages(agentMessages.slice(0, userIdx));
  setToolSteps([]);

  if (send && prompt.trim()) sendMessage(prompt);
}

// ── 전송 ────────────────────────────────────────────────────────────────────

/**
 * 사용자 메시지를 보내고 응답을 붙인다.
 *
 * 통합 창구다 — LLM이 tool-calling으로 엑셀 작업인지 일반 대화인지 판단하므로
 * 프론트에서 분기하지 않는다.
 *
 * @param {string} rawInput 입력창 원문 (범위 참조 블록 포함 가능)
 */
export async function sendMessage(rawInput) {
  const trimmed = String(rawInput || "").trim();
  if (!trimmed) return;

  const {
    addAgentMessage,
    activeSessionId,
    setActiveSessionId,
  } = app();
  const { setSending, setTaskLabel, setPendingExcelApproval, setToolSteps } = chat();

  // 보낼 명령문은 참조 블록을 정리한 형태. 화면·저장에는 사람용 문구를 쓴다 —
  // `[[EXCEL_RANGE:…]]` 마크업은 모델과의 약속이지 사람에게 보일 말이 아니다
  // (인라인 채팅은 2026-08-17 부터 그랬고, 이 경로는 2026-09-06 감사에서 맞췄다).
  const message = toOutboundCommand(trimmed);
  const displayText = displayMessageText(trimmed);
  addAgentMessage({ role: "user", text: displayText, raw: trimmed, time: stamp() });
  // 붙여넣기로 인식한 범위는 문장에 범위가 없을 때 문맥 범위로 같이 보낸다 —
  // 되묻기("어디에 넣을까요?")가 위치를 알고 물을 수 있게(감사 발견 8).
  const pasteRef = extractRangeTag(trimmed);
  const contextRange = pasteRef && !hasExplicitRangeInCommand(message) ? pasteRef : null;
  const clientContext = pasteRef
    ? { raw_message: trimmed, display_text: displayText, paste_ref: pasteRef, surface: "chat_panel" }
    : { surface: "chat_panel" };
  // 지난 턴의 스텝 칩을 비운다 — 안 비우면 어느 요청의 진행인지 알 수 없다.
  setToolSteps([]);

  // 세션 확보 — 없으면 프론트에서 새 ID 생성 (chat_history 영속화 키)
  let sid = activeSessionId;
  if (!sid) {
    sid = crypto.randomUUID();
    setActiveSessionId(sid);
  }
  persistSilent(sid, "user", displayText);

  setSending(true);
  setTaskLabel("AI가 처리하는 중...");
  try {
    // 리베이스 후 시그니처가 (message, wb, sheet, sessionId, approve, …)로
    // 바뀌었는데 옛 자리(approve 위치에 history 배열)로 호출해 Rust 역직렬화가
    // 즉사했다 — 채팅 패널발 명령이 사이드카에 닿지도 못한 원인(2026-09-01).
    const res = await excelLiveCommand(message, null, null, sid, false, contextRange, clientContext);
    // 이번 턴에 실제로 실행된 액션 → 툴 진행 스텝 칩 (와이어프레임 B-7).
    setToolSteps(toToolSteps(res?.executed_actions));

    // 사이드카 응답의 승인·되묻기 필드는 result 안에 있다 — 최상위만 읽으면
    // 되묻기("D1:G6에 어떤 값을 넣을까요?")가 "작업이 완료되었습니다"로 둔갑한다
    // (2026-09-01 실측: 사이드카는 옳게 물었는데 화면이 완료라고 했다).
    const rr = (res && typeof res.result === "object" && res.result) || {};
    const approvalRequired = res?.approval_required ?? rr.approval_required;
    const pendingApproval = res?.pending_approval ?? rr.pending_approval;
    const askFollowUp = res?.ask_follow_up ?? rr.ask_follow_up;
    const followUpQuestion = res?.follow_up_question ?? rr.follow_up_question;

    if (approvalRequired && pendingApproval) {
      setPendingExcelApproval(pendingApproval);
      const note = res?.reason || "엑셀 변경 작업은 승인 후 실행됩니다.";
      addAgentMessage({ role: "agent", text: note, time: stamp() });
      persistSilent(sid, "agent", note);
    } else if (askFollowUp && followUpQuestion) {
      addAgentMessage({ role: "agent", text: followUpQuestion, time: stamp() });
      persistSilent(sid, "agent", followUpQuestion);
    } else {
      const text = res?.assistant_text || formatResultText(res?.action, rr);
      addAgentMessage({ role: "agent", text, time: stamp() });
      persistSilent(sid, "agent", text);
    }
  } catch (err) {
    const fallback = "작업 처리 중 오류가 발생했습니다. 다시 시도해 주세요.";
    let errText = toUserMessage(err, fallback);
    // 일반 문구만 반복되면 원인을 알 길이 없다 — 매핑 안 된 오류는 원문을 덧붙인다.
    if (errText === fallback) {
      const rawErr = String(err?.message || err || "").slice(0, 160);
      if (rawErr) errText = `${fallback}
(${rawErr})`;
    }
    addAgentMessage({ role: "agent", text: null, error: errText, time: stamp() });
    persistSilent(sid, "agent", "", { errorText: errText });
  } finally {
    setSending(false);
    setTaskLabel("");
    refreshSessions();
  }
}

// ── 엑셀 승인 ────────────────────────────────────────────────────────────────

/**
 * CONFIRM 등급 엑셀 작업을 승인하고 실행한다.
 *
 * 승인 실행 결과를 본 LLM이 다음 CONFIRM을 요청하면 다이얼로그를 다시 띄워
 * 복합 명령을 승인 체인으로 이어간다 (B안: 승인 후 재개).
 */
export async function confirmExcelApproval() {
  const {
    pendingExcelApproval,
    setExcelApprovalBusy,
    setPendingExcelApproval,
    setTaskLabel,
    setToolSteps,
  } = chat();
  if (!pendingExcelApproval) return;
  const { addAgentMessage, activeSessionId } = app();

  setExcelApprovalBusy(true);
  let hasNext = false;

  // 승인 자체가 대화 기록에 남는다 — 와이어프레임 B-7에서 `네 Y`가 사용자
  // 말풍선으로 스레드에 쌓여 있다. 나중에 기록을 되짚을 때 "누가 승인했나"가
  // 스레드 안에 있어야 감사 로그를 따로 열어보지 않는다.
  addAgentMessage({ role: "user", text: "네 Y", time: stamp() });
  persistSilent(activeSessionId, "user", "네 Y");

  try {
    const out = await excelLiveSubmitApproval(pendingExcelApproval.approval_id, true, null);
    setToolSteps(toToolSteps(out?.executed_actions));

    const text = out?.assistant_text || formatResultText(out?.action, out?.result);
    addAgentMessage({ role: "agent", text, time: stamp() });
    persistSilent(activeSessionId, "agent", text);

    if (out?.approval_required && out?.pending_approval) {
      setPendingExcelApproval(out.pending_approval);
      const note = out.reason || "다음 작업도 승인이 필요합니다.";
      addAgentMessage({ role: "agent", text: note, time: stamp() });
      persistSilent(activeSessionId, "agent", note);
      hasNext = true;
    }
  } catch (err) {
    addAgentMessage({
      role: "agent",
      error: toUserMessage(err, "엑셀 승인 처리 중 오류가 발생했습니다. 다시 시도해 주세요."),
      time: stamp(),
    });
  } finally {
    setExcelApprovalBusy(false);
    if (!hasNext) setPendingExcelApproval(null);
    setTaskLabel("");
  }
}

/** CONFIRM 등급 엑셀 작업을 거부한다. */
export async function cancelExcelApproval() {
  const { pendingExcelApproval, setExcelApprovalBusy, setPendingExcelApproval, setTaskLabel } = chat();
  if (!pendingExcelApproval) return;
  const { addAgentMessage } = app();

  setExcelApprovalBusy(true);
  // 승인과 마찬가지로 거부도 스레드에 남긴다.
  addAgentMessage({ role: "user", text: "아니오 N", time: stamp() });
  try {
    await excelLiveSubmitApproval(pendingExcelApproval.approval_id, false, "사용자 거부");
    addAgentMessage({
      role: "system",
      text: "엑셀 작업 실행을 취소했습니다.",
      time: stamp(),
    });
  } catch {
    // 거부 전달 실패는 조용히 — sidecar 측 타임아웃으로 정리된다
  } finally {
    setExcelApprovalBusy(false);
    setPendingExcelApproval(null);
    setTaskLabel("");
  }
}

// ── 엑셀 보조 액션 ───────────────────────────────────────────────────────────

/** 현재 열려 있는 통합문서를 저장한다. */
export async function saveWorkbook() {
  const { excelSaving, setExcelSaving } = chat();
  if (excelSaving) return;
  const { addAgentMessage, activeSessionId } = app();

  setExcelSaving(true);
  try {
    const out = await excelLiveSaveWorkbook(null);
    const text = formatResultText(out?.action, out?.result);
    addAgentMessage({ role: "system", text });
    persistSilent(activeSessionId, "system", text);
  } catch (err) {
    addAgentMessage({
      role: "agent",
      error: toUserMessage(err, "엑셀 저장 중 오류가 발생했습니다. 다시 시도해 주세요."),
    });
  } finally {
    setExcelSaving(false);
  }
}

/**
 * Excel에서 선택 중인 범위를 읽어 입력창에 붙일 참조 블록을 만든다.
 *
 * @returns {Promise<string|null>} 입력창에 이어붙일 블록. 실패 시 null.
 */
export async function buildSelectedRangeContext() {
  const { insertingRange, setInsertingRange } = chat();
  if (insertingRange) return null;
  const { addAgentMessage } = app();

  setInsertingRange(true);
  try {
    const out = await excelLiveCommand(
      "지금 선택한 범위 읽어줘",
      null,
      null,
      app().activeSessionId ?? null,
      false,
    );
    const { block, address, rows, cols } = buildRangeContextBlock(out?.result || {});
    addAgentMessage({
      role: "system",
      text: `선택 범위 ${address} (${rows}행 × ${cols}열) 참조가 입력창에 삽입되었습니다.`,
    });
    return block;
  } catch (err) {
    addAgentMessage({
      role: "agent",
      error: toUserMessage(
        err,
        "엑셀 선택 범위를 가져오지 못했습니다. 먼저 Excel에서 범위를 선택해 주세요."
      ),
    });
    return null;
  } finally {
    setInsertingRange(false);
  }
}

/**
 * Ollama 상태로 채팅 가능 여부를 판정한다.
 * unknown/checking 중에는 막지 않는다 — 실제 미응답이면 전송 시 오류로 안내된다.
 */
export function isChatUnavailable(ollamaState) {
  return (
    ollamaState === "not_installed" ||
    ollamaState === "installed_stopped" ||
    ollamaState === "error"
  );
}
