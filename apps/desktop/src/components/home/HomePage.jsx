/**
 * HomePage — 최종 와이어프레임 B-1 (Frame 166 / 다크 152).
 *
 * 구버전 홈은 인사말 + 상태 카드 3장(보안/AI엔진/최근 대화) + `새 업무 시작하기`
 * 였다. 최종안은 그 자리가 통째로 **문서 관리 지면**이다:
 *
 *   배경 장식 타원 → 인사말 + 로고 → 서브텍스트 → 컴포저
 *   → 문서 액션 바(생성 · 업로드 · 삭제) → 문서 카드 그리드 + 더보기 타일
 *
 * 우상단에는 `로컬 에이전트 작동중` + 초록 점 8×8이 붙는다.
 *
 * 여기서 명령을 보내면 채팅 패널이 뜨고 본문이 워크스페이스로 넘어간다 —
 * 와이어프레임에서 패널이 떠 있는 화면(B-2/B-3)의 본문은 홈이 아니라 문서
 * 지면이기 때문이다.
 *
 * 조합만 한다. 표시 모델은 lib/documents.js, 액션은 lib/documentManager.js,
 * 전송은 lib/chatManager.js가 소유한다.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FilePlus2, Trash2, Upload } from "lucide-react";

import { BrandMark } from "@/components/ui/logo";
import { DocumentCard, MoreDocumentsTile } from "@/components/ui/document-card";
import AlertDialog from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { buildDocumentGrid } from "@/lib/documents";
import {
  createExcelDocument,
  deleteDocument,
  openDocument,
  refreshDocuments,
  uploadDocuments,
} from "@/lib/documentManager";
import { sendMessage, isChatUnavailable } from "@/lib/chatManager";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import useDocumentStore from "@/store/documentStore";
import useStatusStore from "@/store/statusStore";
import { getOllamaStatus } from "@/lib/statusTokens";

/** 시간대에 맞는 인사 — 와이어프레임의 `좋은 아침입니다`. */
function greeting(hour) {
  if (hour < 6) return "늦은 시간까지 고생이 많으세요";
  if (hour < 12) return "좋은 아침입니다";
  if (hour < 18) return "좋은 오후입니다";
  return "좋은 저녁입니다";
}

/** 문서 액션 바의 버튼 한 개 — 와이어프레임 242×44. */
function DocumentAction({ icon: Icon, label, onClick, disabled, tone = "default" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex h-11 items-center gap-2 rounded-lg px-4 text-xs font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        tone === "danger"
          ? "text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          : "border border-border bg-card text-foreground hover:border-primary/50 hover:bg-accent"
      )}
    >
      <Icon
        className={cn("h-4 w-4 shrink-0", tone === "danger" ? "" : "text-brand-file")}
      />
      {label}
    </button>
  );
}

export default function HomePage() {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const setPanelOpen = useChatStore((s) => s.setPanelOpen);
  const ollamaState = useStatusStore((s) => s.modules.ollama.state);

  const files = useDocumentStore((s) => s.files);
  const loading = useDocumentStore((s) => s.loading);
  const busy = useDocumentStore((s) => s.busy);
  const error = useDocumentStore((s) => s.error);

  const [input, setInput] = useState("");
  // 삭제 모드 — 카드를 눌러 고르고 확인 다이얼로그로 지운다.
  const [deleteMode, setDeleteMode] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const fileInputRef = useRef(null);

  const unavailable = isChatUnavailable(ollamaState);
  const engine = getOllamaStatus(ollamaState);

  useEffect(() => {
    refreshDocuments();
  }, []);

  // 인사말은 마운트 시점에 한 번 정한다 — 매 렌더 new Date()면 자정 근처에서
  // 이유 없이 문구가 바뀐다.
  const hello = useMemo(() => greeting(new Date().getHours()), []);
  const grid = useMemo(() => buildDocumentGrid(files, new Date()), [files]);

  const handleSubmit = useCallback(() => {
    const text = input.trim();
    if (!text || unavailable) return;
    setInput("");
    // 전송 → 패널을 띄우고 본문을 문서 지면으로 넘긴다 (와이어프레임 B-3).
    sendMessage(text);
    setPanelOpen(true);
    setCurrentPage("workspace");
  }, [input, unavailable, setPanelOpen, setCurrentPage]);

  const handleKeyDown = (e) => {
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const toggleSelect = useCallback((path) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const exitDeleteMode = () => {
    setDeleteMode(false);
    setSelected(new Set());
  };

  const handleConfirmDelete = async () => {
    setConfirmDelete(false);
    for (const path of selected) {
      await deleteDocument(path);
    }
    exitDeleteMode();
  };

  const handleCreate = async () => {
    const name = newName.trim();
    setCreateOpen(false);
    setNewName("");
    if (name) await createExcelDocument(name);
  };

  return (
    <div className="relative h-full overflow-y-auto">
      {/*
        배경 장식 타원 938×632 #7FD163. 와이어프레임에서 아주 옅게 깔려 있다.
        pointer-events-none 이라 아래 요소 클릭을 막지 않는다.
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-0 h-[39.5rem] w-[58.6rem] -translate-x-1/2 -translate-y-1/3 rounded-full bg-brand-glow/10 blur-3xl"
      />

      {/* 우상단 상태 — `로컬 에이전트 작동중` + 8×8 점 */}
      <div className="absolute right-6 top-4 flex items-center gap-2">
        <span className="text-[11px] text-muted-foreground">
          {engine.tone === "ok" ? "로컬 에이전트 작동중" : engine.label}
        </span>
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            engine.tone === "ok" ? "bg-brand" : "bg-amber-500"
          )}
          aria-hidden="true"
        />
      </div>

      <div className="relative mx-auto flex max-w-5xl flex-col items-center px-8 pb-14 pt-20">
        {/* 인사말 + 로고 */}
        <BrandMark className="h-9 w-9 rounded-lg" />
        <h1 className="mt-5 text-center text-3xl font-bold text-foreground">
          {hello}, 무엇을 도와드릴까요?
        </h1>
        <p className="mt-3 text-sm font-semibold text-primary">
          당신의 업무비서 김대리 대기중입니다
        </p>

        {/* 컴포저 750×48 — plus 버튼 + placeholder */}
        <div className="mt-6 w-full max-w-[46.875rem]">
          <div
            className={cn(
              "flex items-center gap-3 rounded-full border bg-background px-4 py-2.5 transition-colors",
              unavailable ? "border-border opacity-60" : "border-primary/40 focus-within:border-primary"
            )}
          >
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
              aria-label="문서 첨부"
              title="문서 업로드"
            >
              +
            </button>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={unavailable}
              placeholder="김대리에게 명령을 내려주세요."
              className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
            />
          </div>
          {unavailable && (
            <p className="mt-2 text-center text-xs text-amber-600 dark:text-amber-400">
              로컬 AI 엔진이 실행되지 않아 명령을 보낼 수 없습니다. 사이드바의 환경
              설정에서 로컬 AI를 먼저 준비해 주세요.
            </p>
          )}
        </div>

        {/* 문서 액션 바 */}
        <div className="mt-16 flex w-full items-center gap-3">
          <DocumentAction
            icon={FilePlus2}
            label="새로운 문서 생성"
            onClick={() => setCreateOpen(true)}
            disabled={busy}
          />
          <DocumentAction
            icon={Upload}
            label="문서 업로드"
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
          />
          <div className="flex-1" />
          {deleteMode ? (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={exitDeleteMode}
                className="h-11 rounded-lg px-3 text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                disabled={selected.size === 0 || busy}
                className="h-11 rounded-lg bg-destructive px-4 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {selected.size}개 삭제
              </button>
            </div>
          ) : (
            <DocumentAction
              icon={Trash2}
              label="문서 삭제"
              tone="danger"
              onClick={() => setDeleteMode(true)}
              disabled={busy || grid.total === 0}
            />
          )}
        </div>

        {error && (
          <p className="mt-3 w-full rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        {/* 문서 카드 그리드 */}
        <div className="mt-4 grid w-full grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-5">
          {grid.cards.map((doc) => (
            <DocumentCard
              key={doc.path}
              doc={doc}
              selected={selected.has(doc.path)}
              onOpen={openDocument}
              onToggleSelect={deleteMode ? toggleSelect : undefined}
            />
          ))}
          {grid.remaining > 0 && (
            <MoreDocumentsTile
              count={grid.remaining}
              onClick={() => setCurrentPage("workspace")}
            />
          )}
        </div>

        {grid.total === 0 && !loading && (
          <p className="mt-10 text-xs text-muted-foreground">
            아직 문서가 없습니다. 위에서 새 문서를 만들거나 업로드해 보세요.
          </p>
        )}
      </div>

      {/* 업로드용 숨은 input — 컴포저의 + 와 액션 바가 공유한다 */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        accept=".xlsx,.xlsm,.xls,.csv,.docx,.doc,.pptx,.ppt,.pdf,.txt,.md,.json"
        onChange={async (e) => {
          const picked = e.target.files;
          e.target.value = "";
          await uploadDocuments(picked);
        }}
      />

      {/* 새 문서 이름 입력 */}
      <AlertDialog
        open={createOpen}
        title="새 엑셀 문서 만들기"
        description="워크스페이스에 만들 파일 이름을 입력하세요."
        confirmLabel="만들기"
        onConfirm={handleCreate}
        onCancel={() => {
          setCreateOpen(false);
          setNewName("");
        }}
      >
        <input
          autoFocus
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
            if (e.key === "Enter") handleCreate();
          }}
          placeholder="예: 8월_매출정리.xlsx"
          className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
        />
      </AlertDialog>

      <AlertDialog
        open={confirmDelete}
        title="문서 삭제"
        description={`선택한 ${selected.size}개 문서를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`}
        confirmLabel="삭제"
        confirmVariant="destructive"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
