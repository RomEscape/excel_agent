/**
 * ChatPage — 목업(desktop-app) 1~5번 화면의 본문.
 *
 *   메시지 없음 → 1번(인사말 + 상태 카드 + CTA)
 *   메시지 있음 → 2·4·5번(스레드 + 인라인 결과 카드)
 *   엑셀 파일 참조 중 → 3번(첨부 칩 + 빠른 프롬프트)
 *
 * 이 파일은 조합만 한다. 버블/컴포저/칩은 ui/chat.jsx, 결과 카드는
 * ui/result-card.jsx, 액션은 lib/chatManager.js가 소유한다.
 *
 * 목업의 `● ● ● 김대리 AI - 로컬 워크스페이스` 타이틀바는 옮기지 않았다.
 * Figma가 "이건 데스크톱 창"임을 나타내려고 그린 장식이고, 실제 Tauri 창에는
 * OS 타이틀바가 이미 있어서 겹친다. 그 자리의 정보(보안/AI 상태)는 StatusBar가
 * 이미 실데이터로 보여준다.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cpu, ShieldCheck, Sparkles, Zap } from "lucide-react";

import {
  ChatComposer,
  ErrorBubble,
  MessageBubble,
  QuickPromptRow,
  TypingBubble,
  AttachmentChip,
  LOCAL_ONLY_FOOTNOTE,
} from "@/components/ui/chat";
import ResultCard from "@/components/ui/result-card";
import AlertDialog from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getOllamaStatus } from "@/lib/statusTokens";
import { sessionTitle } from "@/lib/chatSessions";
import {
  sendMessage,
  confirmExcelApproval,
  cancelExcelApproval,
  saveWorkbook,
  buildSelectedRangeContext,
  isChatUnavailable,
} from "@/lib/chatManager";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import useStatusStore from "@/store/statusStore";

/**
 * 빠른 프롬프트 — 파서/테스트로 검증된 Excel Live 명령만 노출한다.
 * (기존 VERIFIED_EXCEL_EXAMPLES를 라벨 있는 형태로 옮긴 것)
 */
const QUICK_PROMPTS = Object.freeze([
  { label: "범위 읽기", prompt: "A1:C3 범위 읽어줘" },
  { label: "값 입력", prompt: "B2:D2에 이름,수량,금액 입력" },
  { label: "조건부 강조", prompt: "A열 20보다 큰 값 빨간색으로 칠해줘" },
  { label: "합계 수식", prompt: "C1에 A1:A10 합계 수식 넣어줘" },
]);

/** 시간대에 맞는 인사 — 목업의 "좋은 아침입니다". */
function greeting(hour) {
  if (hour < 6) return "늦은 시간까지 고생이 많으세요";
  if (hour < 12) return "좋은 아침입니다";
  if (hour < 18) return "좋은 오후입니다";
  return "좋은 저녁입니다";
}

/** 웰컴 화면의 상태 카드 — 목업 1번의 3장. */
function WelcomeCard({ icon: Icon, label, value, tone = "default" }) {
  return (
    <div className="flex items-center gap-3.5 rounded-[20px] border border-border bg-muted/40 py-5 pl-5 pr-6">
      <span
        className={cn(
          "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl",
          tone === "warning"
            ? "bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400"
            : "bg-primary/10 text-primary"
        )}
      >
        <Icon className="h-5 w-5" />
      </span>
      <span className="flex min-w-0 flex-col gap-[3px]">
        <span className="text-xs font-bold text-muted-foreground">{label}</span>
        <span className="truncate text-sm font-bold text-foreground" title={value}>
          {value}
        </span>
      </span>
    </div>
  );
}

/** 목업 1번 — 인사말 + 상태 카드 3장 + CTA. */
function WelcomeScreen({ onStart }) {
  const ollamaModule = useStatusStore((s) => s.modules.ollama);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const sessions = useChatStore((s) => s.sessions);

  const engine = getOllamaStatus(ollamaModule.state);
  const recent = sessions[0];

  // 인사말은 마운트 시점에 한 번 정한다 — 매 렌더 new Date()면 자정 근처에서
  // 이유 없이 문구가 바뀐다.
  const hello = useMemo(() => greeting(new Date().getHours()), []);

  return (
    // 부모(스크롤 컨테이너)는 flex가 아니므로 flex-1이 아니라 h-full로 높이를 잡아야
    // justify-center가 실제로 가운데로 보낸다.
    <div className="flex h-full flex-col items-center justify-center gap-10 py-14">
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-3xl font-bold text-foreground">
          {hello}. 무엇을 도와드릴까요?
        </h1>
        <p className="text-base font-bold text-primary">
          당신의 전문 비서 김대리가 대기중입니다.
        </p>
      </div>

      <div className="flex flex-wrap items-start justify-center gap-5">
        <WelcomeCard
          icon={ShieldCheck}
          label="보안 상태"
          value="100% 로컬 처리 · 외부 전송 없음"
        />
        <WelcomeCard
          icon={Cpu}
          label="AI 엔진"
          value={
            engine.tone === "ok"
              ? `${llmConfig.model} 준비됨`
              : engine.label
          }
          tone={engine.tone === "warning" ? "warning" : "default"}
        />
        <WelcomeCard
          icon={Sparkles}
          label="최근 대화"
          value={recent ? sessionTitle(recent, 24) : "아직 대화가 없습니다"}
        />
      </div>

      <Button size="lg" className="rounded-[40px] px-10" onClick={onStart}>
        새 업무 시작하기
      </Button>
    </div>
  );
}

export default function ChatPage() {
  const agentMessages = useAppStore((s) => s.agentMessages);
  const ollamaState = useStatusStore((s) => s.modules.ollama.state);

  const sending = useChatStore((s) => s.sending);
  const taskLabel = useChatStore((s) => s.taskLabel);
  const pendingExcelApproval = useChatStore((s) => s.pendingExcelApproval);
  const excelApprovalBusy = useChatStore((s) => s.excelApprovalBusy);
  const excelSaving = useChatStore((s) => s.excelSaving);
  const insertingRange = useChatStore((s) => s.insertingRange);

  const [input, setInput] = useState("");
  // 대화 중 사용자가 직접 펼친 경우에만 true — 전송하면 다시 접힌다.
  const [promptBoxOpen, setPromptBoxOpen] = useState(false);
  const composerRef = useRef(null);
  const endRef = useRef(null);

  const unavailable = isChatUnavailable(ollamaState);
  const isEmpty = agentMessages.length === 0;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentMessages, sending]);

  const handleSubmit = useCallback(() => {
    const text = input.trim();
    if (!text || sending || unavailable) return;
    setInput("");
    setPromptBoxOpen(false);
    sendMessage(text);
  }, [input, sending, unavailable]);

  const handleInsertRange = useCallback(async () => {
    const block = await buildSelectedRangeContext();
    if (!block) return;
    setInput((prev) => (prev ? `${prev}\n\n${block}\n` : `${block}\n`));
    composerRef.current?.focus();
  }, []);

  const focusComposer = useCallback(() => composerRef.current?.focus(), []);

  // 입력창에 범위 참조가 들어 있으면 목업 3번처럼 첨부 칩을 띄운다.
  const rangeRef = useMemo(() => {
    const m = input.match(/\[\[EXCEL_RANGE:([A-Z0-9:]+)\]\]/i);
    return m ? m[1].toUpperCase() : null;
  }, [input]);

  // 첫 화면이거나, 범위를 첨부했거나, 사용자가 직접 펼쳤을 때만 프롬프트 박스를 띄운다.
  const showPromptBox = isEmpty || !!rangeRef || promptBoxOpen;

  const clearRangeRef = useCallback(() => {
    setInput((prev) =>
      prev
        .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
        .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
        .trim()
    );
  }, []);

  return (
    <div className="flex h-full flex-col">
      {/* 스레드 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        {isEmpty ? (
          <WelcomeScreen onStart={focusComposer} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            {agentMessages.map((msg, idx) => {
              if (msg.role === "agent" && msg.error) {
                return <ErrorBubble key={idx}>{msg.error}</ErrorBubble>;
              }
              return (
                <MessageBubble
                  key={idx}
                  role={msg.role}
                  attachment={
                    msg.resultView ? <ResultCard view={msg.resultView} /> : null
                  }
                  footer={
                    <>
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
                    </>
                  }
                >
                  {msg.text}
                </MessageBubble>
              );
            })}
            {sending && <TypingBubble label={taskLabel} />}
            <div ref={endRef} />
          </div>
        )}
      </div>

      {/* 컴포저 영역 */}
      <div className="shrink-0 px-8 pb-5 pt-4">
        <div className="mx-auto max-w-3xl">
          {unavailable ? (
            <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
              <Cpu className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Ollama(로컬 AI 엔진)가 실행되지 않아 사용할 수 없습니다. 왼쪽
              내비게이션에서 워크스페이스를 눌러 설치 가이드를 여세요.
            </div>
          ) : (
            <ChatComposer
              ref={composerRef}
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              busy={sending}
              disabled={sending}
              focused={!!rangeRef}
              footnote={LOCAL_ONLY_FOOTNOTE}
              header={
                <>
                  {/* 목업 3번 — 첨부 칩 + 빠른 프롬프트 추천.
                      대화가 진행 중이면 접는다. 항상 띄우면 스레드가 볼 수 있는
                      세로 공간을 계속 잡아먹는데, 목업도 2번(대화 중) 화면에는
                      이 박스를 그리지 않았다. */}
                  {showPromptBox && (
                    <div className="flex flex-col gap-3.5 rounded-xl border border-border bg-card p-5">
                      {rangeRef && (
                        <AttachmentChip
                          name={`선택 범위 ${rangeRef}`}
                          onRemove={clearRangeRef}
                        />
                      )}
                      <QuickPromptRow
                        title="엑셀 맞춤형 빠른 프롬프트 추천"
                        prompts={QUICK_PROMPTS}
                        onPick={(p) => {
                          setInput(p);
                          composerRef.current?.focus();
                        }}
                        disabled={sending}
                      />
                    </div>
                  )}

                  {/* 엑셀 보조 액션은 대화 중에도 필요하므로 항상 남긴다. */}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={handleInsertRange}
                      disabled={sending || insertingRange}
                      title="현재 선택한 엑셀 범위를 입력창에 참조로 삽입"
                    >
                      {insertingRange ? "범위 읽는 중..." : "범위 참조 삽입"}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={saveWorkbook}
                      disabled={sending || excelSaving}
                      title="현재 열려 있는 엑셀 파일 저장"
                    >
                      {excelSaving ? "저장 중..." : "엑셀 저장"}
                    </Button>
                    {!showPromptBox && (
                      <button
                        type="button"
                        onClick={() => setPromptBoxOpen(true)}
                        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <Sparkles className="h-3 w-3" />
                        빠른 프롬프트
                      </button>
                    )}
                  </div>
                </>
              }
            />
          )}
        </div>
      </div>

      {/* 엑셀 CONFIRM 승인 */}
      <AlertDialog
        open={!!pendingExcelApproval}
        title={pendingExcelApproval?.tool_display_name || "엑셀 작업 승인"}
        description={
          pendingExcelApproval
            ? `${pendingExcelApproval.summary}\n\n정말 실행하시겠습니까?`
            : ""
        }
        confirmLabel={excelApprovalBusy ? "처리 중..." : "승인 후 실행"}
        confirmVariant="default"
        onConfirm={confirmExcelApproval}
        onCancel={cancelExcelApproval}
      />
    </div>
  );
}
