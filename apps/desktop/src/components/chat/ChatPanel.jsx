/**
 * ChatPanel — 최종 와이어프레임 B-2 / B-3 / B-5 ~ B-7의 390px 채팅 패널.
 *
 * 구버전은 채팅이 전체 폭 페이지였다. 최종안에서 채팅은 본문(엑셀/문서) 위의
 * 패널이고 크기가 두 단계로 변한다 — 규칙은 lib/chatPanel.js가 소유한다:
 *
 *   docked   오른쪽에 붙어 본문을 밀어낸다 (Frame 168)
 *   floating 본문 위 우하단에 떠 있다     (Frame 169)
 *
 * 위→아래 구성:
 *   상단 바 (크기 토글 aspect-ratio · 새 대화 · 닫기)
 *   스레드 (말풍선 + 액션 행 + 타임스탬프 · 툴 스텝 칩 · 스켈레톤)
 *   빠른 프롬프트 칩 행
 *   컴포저
 *
 * 조합만 한다 — 버블/칩/컴포저는 ui/chat.jsx, 액션은 lib/chatManager.js,
 * 상태는 store/chatStore.js + appStore가 소유한다.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, PanelRightClose, Plus, Save, Scaling, ShieldCheck, X, Zap } from "lucide-react";

import {
  AttachmentChip,
  BubbleActions,
  ChatComposer,
  ErrorBubble,
  InlineApproval,
  MessageBubble,
  QuickPromptRow,
  ThinkingSkeleton,
  ToolStepChip,
} from "@/components/ui/chat";
import { BrandWordmark } from "@/components/ui/logo";
import { cn } from "@/lib/utils";
import { panelToggleLabel } from "@/lib/chatPanel";
import {
  sendMessage,
  retryFromMessage,
  confirmExcelApproval,
  cancelExcelApproval,
  buildSelectedRangeContext,
  startNewSession,
  isChatUnavailable,
  saveWorkbook,
} from "@/lib/chatManager";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import useStatusStore from "@/store/statusStore";

/**
 * 빠른 프롬프트 — 파서/테스트로 검증된 Excel Live 명령만 노출한다.
 * 와이어프레임의 `중복 데이터 처리해줘` 같은 문장에 대응한다.
 */
const QUICK_PROMPTS = Object.freeze([
  { label: "중복 데이터 처리해줘", prompt: "중복된 행을 제거해줘" },
  { label: "범위 읽기", prompt: "A1:C3 범위 읽어줘" },
  { label: "조건부 강조", prompt: "A열 20보다 큰 값 빨간색으로 칠해줘" },
  { label: "합계 수식", prompt: "C1에 A1:A10 합계 수식 넣어줘" },
]);

export default function ChatPanel() {
  const agentMessages = useAppStore((s) => s.agentMessages);
  const ollamaState = useStatusStore((s) => s.modules.ollama.state);

  const sending = useChatStore((s) => s.sending);
  const taskLabel = useChatStore((s) => s.taskLabel);
  const toolSteps = useChatStore((s) => s.toolSteps);
  const pendingExcelApproval = useChatStore((s) => s.pendingExcelApproval);
  const excelApprovalBusy = useChatStore((s) => s.excelApprovalBusy);
  const insertingRange = useChatStore((s) => s.insertingRange);
  const excelSaving = useChatStore((s) => s.excelSaving);
  const panelMode = useChatStore((s) => s.panelMode);
  const togglePanelMode = useChatStore((s) => s.togglePanelMode);
  const setPanelOpen = useChatStore((s) => s.setPanelOpen);

  const [input, setInput] = useState("");
  const composerRef = useRef(null);
  const endRef = useRef(null);

  const unavailable = isChatUnavailable(ollamaState);

  // panelMode도 의존성이다 — 크기를 바꾸면 스레드 높이가 달라지는데 다시 내리지
  // 않으면 보던 위치가 그대로 남아 마지막 말풍선이 잘린 채로 보인다.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentMessages, sending, toolSteps, panelMode]);

  /**
   * CONFIRM 대기 중 Y/N 단축키 — 와이어프레임의 `네 Y` / `아니오 N` 라벨이
   * 약속하는 동작이다. 입력창에 포커스가 있거나 IME 조합 중이면 무시한다.
   */
  useEffect(() => {
    if (!pendingExcelApproval) return;
    const onKey = (e) => {
      if (e.isComposing || e.nativeEvent?.isComposing || e.keyCode === 229) return;
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        confirmExcelApproval();
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        cancelExcelApproval();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingExcelApproval]);

  const handleSubmit = useCallback(() => {
    const text = input.trim();
    if (!text || sending || unavailable) return;
    setInput("");
    sendMessage(text);
  }, [input, sending, unavailable]);

  const handleInsertRange = useCallback(async () => {
    const block = await buildSelectedRangeContext();
    if (!block) return;
    setInput((prev) => (prev ? `${prev}\n\n${block}\n` : `${block}\n`));
    composerRef.current?.focus();
  }, []);

  // 입력창에 범위 참조가 들어 있으면 첨부 칩을 띄운다.
  const rangeRef = useMemo(() => {
    const m = input.match(/\[\[EXCEL_RANGE:([A-Z0-9:]+)\]\]/i);
    return m ? m[1].toUpperCase() : null;
  }, [input]);

  const clearRangeRef = useCallback(() => {
    setInput((prev) =>
      prev
        .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
        .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
        .trim()
    );
  }, []);

  // 사용자 메시지 편집 — 그 문장을 입력창으로 되돌리고 이후 대화를 잘라낸다.
  const handleEdit = useCallback((idx, text) => {
    setInput(text ?? "");
    retryFromMessage(idx, { send: false });
    composerRef.current?.focus();
  }, []);

  const isFloating = panelMode === "floating";
  const lastIndex = agentMessages.length - 1;

  return (
    <aside
      className={cn(
        "z-30 flex w-[390px] shrink-0 flex-col border-border bg-background",
        isFloating
          ? // 플로팅 — 본문 위 우하단에 떠 있다 (Frame 169: 390×507)
            "absolute bottom-4 right-4 h-[507px] max-h-[calc(100%-2rem)] rounded-xl border shadow-2xl"
          : // 도킹 — 지면 전체 높이로 오른쪽에 붙는다 (Frame 168: 390×900)
            "h-full border-l"
      )}
      aria-label="김대리 채팅"
    >
      {/* 상단 바 — 크기 토글 + 워드마크 */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <BrandWordmark className="h-6 w-auto" />
        <div className="flex items-center gap-0.5">
          {/*
            엑셀 저장 — 라이브 COM 편집은 통합문서를 건드리기만 하고 저장은 하지
            않는다. 저장 시점을 앱 안에서 잡을 수단이 하나도 없으면 사용자가
            엑셀 창을 직접 찾아가야 한다. 결과(성공/실패)는 스레드에 문장으로 남는다.
          */}
          <button
            type="button"
            onClick={saveWorkbook}
            disabled={excelSaving || sending}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            aria-label="엑셀 저장"
            title="열려 있는 엑셀 통합문서 저장"
          >
            {excelSaving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
          </button>
          <button
            type="button"
            onClick={startNewSession}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="새 대화"
            title="새 대화"
          >
            <Plus className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={togglePanelMode}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label={panelToggleLabel(panelMode)}
            title={panelToggleLabel(panelMode)}
          >
            {isFloating ? <PanelRightClose className="h-4 w-4" /> : <Scaling className="h-4 w-4" />}
          </button>
          <button
            type="button"
            onClick={() => setPanelOpen(false)}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="채팅 닫기"
            title="채팅 닫기"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* 스레드 */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-card px-4 py-4">
        <div className="flex flex-col gap-3">
          {agentMessages.length === 0 && (
            <p className="px-1 py-8 text-center text-xs text-muted-foreground">
              무엇을 도와드릴까요? 아래에 명령을 입력해 주세요.
            </p>
          )}

          {agentMessages.map((msg, idx) => {
            if (msg.role === "agent" && msg.error) {
              return <ErrorBubble key={idx}>{msg.error}</ErrorBubble>;
            }

            const isUser = msg.role === "user";
            const isLastAgent = msg.role === "agent" && idx === lastIndex;
            // 승인 대기 중이면 마지막 AI 말풍선에 인라인 버튼을 붙인다
            // (와이어프레임 B-6 — 모달이 아니라 말풍선 옆).
            const showApproval = isLastAgent && !!pendingExcelApproval;

            return (
              <MessageBubble
                key={idx}
                role={msg.role}
                time={msg.time}
                actions={
                  msg.role === "system" ? null : (
                    <BubbleActions
                      text={msg.text}
                      align={isUser ? "end" : "start"}
                      onRetry={() => retryFromMessage(idx)}
                      onEdit={isUser ? () => handleEdit(idx, msg.text) : undefined}
                    >
                      {showApproval && (
                        <span className="ml-2">
                          <InlineApproval
                            busy={excelApprovalBusy}
                            onApprove={confirmExcelApproval}
                            onReject={cancelExcelApproval}
                          />
                        </span>
                      )}
                    </BubbleActions>
                  )
                }
                footer={
                  (msg.maskedCount > 0 || msg.toolCalls?.length > 0) && (
                    <div className="flex flex-wrap items-center gap-2">
                      {msg.maskedCount > 0 && (
                        <span
                          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground"
                          title={`${msg.maskedTypes?.join(", ")} ${msg.maskedCount}건이 자동 마스킹되어 AI에 전달되지 않았습니다`}
                        >
                          <ShieldCheck className="h-3 w-3" />
                          민감정보 {msg.maskedCount}건 마스킹됨
                        </span>
                      )}
                      {msg.toolCalls?.length > 0 && (
                        <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                          <Zap className="h-3 w-3" />
                          {msg.toolCalls.length}개 도구 실행됨
                        </span>
                      )}
                    </div>
                  )
                }
              >
                {msg.text}
              </MessageBubble>
            );
          })}

          {/* 툴 진행 스텝 칩 — 와이어프레임 B-7 */}
          {toolSteps.map((step) => (
            <ToolStepChip key={step.id} label={step.label} done={step.done} />
          ))}

          {sending && <ThinkingSkeleton label={taskLabel} />}
          <div ref={endRef} />
        </div>
      </div>

      {/* 빠른 프롬프트 + 컴포저 */}
      <div className="shrink-0 border-t border-border px-3 py-3">
        {unavailable ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] leading-relaxed text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
            로컬 AI 엔진이 실행되지 않아 사용할 수 없습니다. 사이드바의 환경 설정에서
            로컬 AI를 준비해 주세요.
          </p>
        ) : (
          <ChatComposer
            ref={composerRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSubmit}
            onAttach={handleInsertRange}
            busy={sending}
            disabled={sending}
            focused={!!rangeRef}
            header={
              <div className="flex flex-col gap-2">
                {rangeRef && (
                  <AttachmentChip
                    name={`선택 범위 ${rangeRef}`}
                    onRemove={clearRangeRef}
                  />
                )}
                {insertingRange && (
                  <span className="text-[11px] text-muted-foreground">
                    엑셀에서 선택 범위를 읽는 중...
                  </span>
                )}
                <QuickPromptRow
                  prompts={QUICK_PROMPTS}
                  disabled={sending}
                  onPick={(p) => {
                    setInput(p);
                    composerRef.current?.focus();
                  }}
                />
              </div>
            }
          />
        )}
      </div>
    </aside>
  );
}
