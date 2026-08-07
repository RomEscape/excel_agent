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
import { toResultView, formatResultText } from "@/lib/excelResult";
import {
  toOutboundCommand,
  buildRangeContextBlock,
} from "@/lib/excelRangeContext";
import {
  excelLiveCommand,
  excelLiveSubmitApproval,
  excelLiveSaveWorkbook,
  chatSaveMessage,
  chatListSessions,
  chatGetMessages,
  chatDeleteSession,
} from "@/lib/api";

/** 멀티턴 맥락으로 보낼 최근 턴 수. */
const HISTORY_TURNS = 8;

/** 사이드바에 불러올 세션 개수. */
const SESSION_LIST_LIMIT = 30;

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

/** 최근 대화를 OpenAI 형식 history로 — 오류 메시지와 빈 턴은 제외. */
function buildHistory(messages) {
  const turns = [];
  for (const m of messages) {
    if (m.error) continue;
    const role = m.role === "user" ? "user" : m.role === "agent" ? "assistant" : null;
    const content = String(m.text ?? "").trim();
    if (!role || !content) continue;
    turns.push({ role, content });
  }
  return turns.slice(-HISTORY_TURNS);
}

// ── 세션 ────────────────────────────────────────────────────────────────────

/** 세션 목록 새로고침. sidecar 미지원이면 sessionsAvailable을 내린다. */
export async function refreshSessions() {
  const { setSessions, setSessionsLoading, setSessionsAvailable } = chat();
  setSessionsLoading(true);
  try {
    const list = await chatListSessions(SESSION_LIST_LIMIT);
    setSessions(Array.isArray(list) ? list : list?.sessions || []);
    setSessionsAvailable(true);
  } catch {
    setSessions([]);
    setSessionsAvailable(false);
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
  setActiveSessionId(null);
  setAgentMessages([]);
  // 직전 세션이 목록에 반영될 시간을 준 뒤 갱신
  setTimeout(() => refreshSessions(), 100);
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
    agentMessages,
    addAgentMessage,
    activeSessionId,
    setActiveSessionId,
  } = app();
  const { setSending, setTaskLabel, setPendingExcelApproval } = chat();

  // 보낼 명령문은 참조 블록을 정리한 형태, 화면에 남는 건 사용자가 친 원문.
  const message = toOutboundCommand(trimmed);
  const history = buildHistory(agentMessages);
  addAgentMessage({ role: "user", text: trimmed });

  // 세션 확보 — 없으면 프론트에서 새 ID 생성 (chat_history 영속화 키)
  let sid = activeSessionId;
  if (!sid) {
    sid = crypto.randomUUID();
    setActiveSessionId(sid);
  }
  persistSilent(sid, "user", trimmed);

  setSending(true);
  setTaskLabel("AI가 처리하는 중...");
  try {
    const res = await excelLiveCommand(message, null, null, false, history);
    if (res?.approval_required && res?.pending_approval) {
      setPendingExcelApproval(res.pending_approval);
      const note = res.reason || "엑셀 변경 작업은 승인 후 실행됩니다.";
      addAgentMessage({ role: "agent", text: note });
      persistSilent(sid, "agent", note);
    } else {
      const view = toResultView(res?.action, res?.result);
      const text = res?.assistant_text || view.summary;
      // 카드가 붙는 결과면 view를 같이 실어 보낸다 — 렌더는 ChatPage가 판단.
      addAgentMessage({
        role: "agent",
        text,
        resultView: view.kind === "text" ? undefined : view,
      });
      // 영속화는 문자열만 — 세션을 다시 불러오면 카드 없이 문장으로 복원된다.
      persistSilent(sid, "agent", text);
    }
  } catch (err) {
    const errText = toUserMessage(
      err,
      "작업 처리 중 오류가 발생했습니다. 다시 시도해 주세요."
    );
    addAgentMessage({ role: "agent", text: null, error: errText });
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
  const { pendingExcelApproval, setExcelApprovalBusy, setPendingExcelApproval, setTaskLabel } = chat();
  if (!pendingExcelApproval) return;
  const { addAgentMessage, activeSessionId } = app();

  setExcelApprovalBusy(true);
  let hasNext = false;
  try {
    const out = await excelLiveSubmitApproval(pendingExcelApproval.approval_id, true, null);
    const view = toResultView(out?.action, out?.result);
    const text = out?.assistant_text || view.summary;
    addAgentMessage({
      role: "agent",
      text,
      resultView: view.kind === "text" ? undefined : view,
    });
    persistSilent(activeSessionId, "agent", text);

    if (out?.approval_required && out?.pending_approval) {
      setPendingExcelApproval(out.pending_approval);
      const note = out.reason || "다음 작업도 승인이 필요합니다.";
      addAgentMessage({ role: "agent", text: note });
      persistSilent(activeSessionId, "agent", note);
      hasNext = true;
    }
  } catch (err) {
    addAgentMessage({
      role: "agent",
      error: toUserMessage(err, "엑셀 승인 처리 중 오류가 발생했습니다. 다시 시도해 주세요."),
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
  try {
    await excelLiveSubmitApproval(pendingExcelApproval.approval_id, false, "사용자 거부");
    addAgentMessage({ role: "system", text: "엑셀 작업 실행을 취소했습니다." });
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
    const out = await excelLiveCommand("지금 선택한 범위 읽어줘", null, null, false);
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
