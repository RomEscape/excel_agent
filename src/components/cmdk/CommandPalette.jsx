/**
 * CommandPalette — Cmd/Ctrl+K 명령 팔레트.
 *
 * 직접 구현 (cmdk/headlessui 같은 외부 패키지 사용 안 함).
 * - 페이지 4개 + Settings 7탭 + 액션 (로컬 AI 설정, 봇 재시작, 워크스페이스 폴더 열기, 실행 기록 초기화)
 * - 250ms debounce, fuzzy 한/영 검색
 * - 키보드: ↑↓ Enter Esc, Tab 그룹 jump
 *
 * Layout이 글로벌 이벤트 listener로 토글한다 (window 'officeclaw:open-cmdk').
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Command as CmdIcon,
  TextSearch,
  FolderOpen,
  FileText,
  MessagesSquare,
  Settings as SettingsIcon,
  Bot,
  KeyRound,
  ClipboardList,
  ShieldCheck,
  ShieldAlert,
  SlidersHorizontal,
  RefreshCw,
  Trash2,
  AlertCircle,
  CornerDownLeft,
  SunMoon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toggleTheme } from "@/lib/themeManager";
import useAppStore from "@/store/appStore";
import useToast from "@/hooks/useToast";
import {
  openWorkspaceFolder,
  clearCommandAuditLogs,
} from "@/lib/api";

// ── 명령 카탈로그 ───────────────────────────────────────────────────────────
//
// 각 항목: { id, group, label, hint, icon, danger, run(ctx) }
// ctx: { setCurrentPage, requestConfirm, close, notify }
//

const buildCommands = () => [
  // 그룹: 페이지
  { id: "nav.home", group: "페이지", label: "홈", hint: "문서 목록 / 김대리에게 명령", icon: Bot, run: ({ setCurrentPage, close }) => { setCurrentPage("chat"); close(); } },
  { id: "nav.activity", group: "페이지", label: "작업 기록", hint: "작업 요약 / 최근 활동 검색", icon: TextSearch, run: ({ setCurrentPage, close }) => { setCurrentPage("activity"); close(); } },
  { id: "nav.conversations", group: "페이지", label: "대화목록", hint: "지난 대화 요일별 / 파일별", icon: MessagesSquare, run: ({ setCurrentPage, close }) => { setCurrentPage("conversations"); close(); } },
  { id: "nav.preferences", group: "페이지", label: "환경 설정", hint: "요금제 / 디바이스 / 테마 / 글자 크기", icon: SettingsIcon, run: ({ setCurrentPage, close }) => { setCurrentPage("preferences"); close(); } },
  // `파일 목록`은 페이지가 아니라 사이드바 확장 목록이라 setCurrentPage로 못 간다.
  // 사이드바가 듣는 커스텀 이벤트로 연다 (내비 4개 중 유일하게 빠져 있던 항목).
  { id: "nav.files", group: "페이지", label: "파일 목록", hint: "워크스페이스 문서 (사이드바 확장)", icon: FileText, run: ({ close }) => { window.dispatchEvent(new CustomEvent("officeclaw:open-file-list")); close(); } },
  // 아래 둘은 최종안 사이드바에서 빠진 화면이다 — 여기가 유일한 진입 경로이므로 지우지 말 것.
  { id: "nav.workspace", group: "페이지", label: "파일 탐색기", hint: "폴더 탐색 / 미리보기 (내비에 없음)", icon: FolderOpen, run: ({ setCurrentPage, close }) => { setCurrentPage("workspace"); close(); } },
  { id: "nav.settings", group: "페이지", label: "설정 허브", hint: "보안 / 허용 범위 / 실행 기록", icon: SettingsIcon, run: ({ setCurrentPage, close }) => { setCurrentPage("settings"); close(); } },

  // 그룹: 설정
  { id: "settings.guide", group: "설정", label: "로컬 AI 설정", icon: Bot, run: ({ setCurrentPage, close }) => { setCurrentPage("guide"); close(); } },
  { id: "settings.general", group: "설정", label: "일반", icon: SlidersHorizontal, run: ({ setCurrentPage, close }) => { setCurrentPage("settings"); close(); } },
  { id: "settings.security", group: "설정", label: "보안", icon: ShieldCheck, run: ({ setCurrentPage, close }) => { setCurrentPage("security"); close(); } },
  { id: "settings.permissions", group: "설정", label: "에이전트 허용 범위", icon: ShieldAlert, run: ({ setCurrentPage, close }) => { setCurrentPage("permissions"); close(); } },
  { id: "settings.audit", group: "설정", label: "실행 기록", icon: ClipboardList, run: ({ setCurrentPage, close }) => { setCurrentPage("audit"); close(); } },

  // 그룹: 액션
  {
    id: "action.toggle_theme",
    group: "액션",
    label: "화면 테마 전환 (라이트 ↔ 다크)",
    hint: "Cmd/Ctrl+Shift+L",
    icon: SunMoon,
    run: ({ close }) => {
      toggleTheme();
      close();
    },
  },
  {
    id: "action.local_ai_settings",
    group: "액션",
    label: "로컬 AI 설정 가이드 열기",
    hint: "Ollama 설치/실행/모델 다운로드 안내",
    icon: RefreshCw,
    run: ({ setCurrentPage, close }) => {
      setCurrentPage("guide");
      close();
    },
  },
  {
    id: "action.open_workspace",
    group: "액션",
    label: "워크스페이스 폴더 열기",
    hint: "OS 파일 탐색기로 엽니다",
    icon: FolderOpen,
    run: async ({ notify, close }) => {
      try {
        await openWorkspaceFolder();
      } catch (err) {
        notify(`폴더 열기 실패: ${err}`);
      }
      close();
    },
  },
  {
    id: "action.clear_audit",
    group: "액션",
    label: "실행 기록 초기화",
    hint: "모든 명령 감사 기록을 삭제합니다",
    icon: Trash2,
    danger: true,
    run: async ({ requestConfirm, notify, close }) => {
      const ok = await requestConfirm({
        title: "실행 기록 초기화",
        description: "모든 실행 기록이 영구 삭제됩니다. 보안 감사 목적으로 보관 중인 기록도 함께 사라집니다. 계속할까요?",
        confirmLabel: "삭제",
      });
      if (!ok) return;
      try {
        await clearCommandAuditLogs();
        notify("실행 기록이 초기화되었습니다.");
      } catch (err) {
        notify(`초기화 실패: ${err}`);
      }
      close();
    },
  },
];

// ── Fuzzy 검색 (한/영) ─────────────────────────────────────────────────────
//
// 단순한 가중치 기반: 정확한 부분 일치 + 토큰별 부분 일치.
// 한글은 그대로 substring 매칭 (ko-KR는 자모 분해까지 안 해도 충분).
//
function score(query, item) {
  if (!query) return 1;
  const q = query.trim().toLowerCase();
  const haystack = `${item.label} ${item.hint ?? ""} ${item.group} ${item.id}`.toLowerCase();
  if (haystack.includes(q)) return 10;
  // 토큰별 부분 일치
  const tokens = q.split(/\s+/).filter(Boolean);
  let hits = 0;
  for (const t of tokens) {
    if (haystack.includes(t)) hits += 1;
  }
  return hits / Math.max(tokens.length, 1);
}

// ── ConfirmInline 다이얼로그 ───────────────────────────────────────────────

function ConfirmInline({ open, title, description, confirmLabel, onConfirm, onCancel }) {
  if (!open) return null;
  return (
    <div className="border-t border-border bg-amber-50 dark:bg-amber-950/30 p-4">
      <div className="flex items-start gap-3">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">{title}</p>
          <p className="mt-0.5 text-xs text-amber-800 dark:text-amber-200">{description}</p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-border bg-background px-3 py-1 text-xs hover:bg-muted"
            >
              취소
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="rounded bg-destructive px-3 py-1 text-xs font-medium text-destructive-foreground hover:bg-destructive/90"
            >
              {confirmLabel ?? "확인"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 메인 ───────────────────────────────────────────────────────────────────

export default function CommandPalette({ open, onClose }) {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const [confirmState, setConfirmState] = useState(null); // { title, description, confirmLabel, resolve }
  // 토스트 상태 + 자동 dismiss(2500ms)는 useToast 훅이 소유
  const { toast, showToast, dismissToast } = useToast(2500);

  const inputRef = useRef(null);
  const listRef = useRef(null);


  // 250ms debounce
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  // 열림 시 포커스 + 상태 초기화 + 봇 상태 fetch
  useEffect(() => {
    if (open) {
      setQuery("");
      setDebounced("");
      setActiveIdx(0);
      setConfirmState(null);
      dismissToast();
      // 다음 tick에 input focus
      setTimeout(() => inputRef.current?.focus(), 0);

    }
  }, [open]);

  const commands = useMemo(() => buildCommands(), []);

  const filtered = useMemo(() => {
    const scored = commands
      .map((c) => ({ ...c, _score: score(debounced, c) }))
      .filter((c) => c._score > 0);
    // 정렬: 점수 높은 순, 동점은 원 순서 유지
    scored.sort((a, b) => b._score - a._score);
    return scored;
  }, [commands, debounced]);

  // 그룹별 묶기
  const grouped = useMemo(() => {
    const map = new Map();
    for (const c of filtered) {
      if (!map.has(c.group)) map.set(c.group, []);
      map.get(c.group).push(c);
    }
    // 그룹 순서 고정
    const order = ["페이지", "설정", "액션"];
    return order
      .filter((g) => map.has(g))
      .map((g) => ({ group: g, items: map.get(g) }));
  }, [filtered]);

  // flat list — 키보드 nav용
  const flat = useMemo(() => grouped.flatMap((g) => g.items), [grouped]);

  // activeIdx 범위 보정
  useEffect(() => {
    if (activeIdx >= flat.length) setActiveIdx(0);
  }, [flat.length, activeIdx]);

  const requestConfirm = (opts) =>
    new Promise((resolve) => {
      setConfirmState({ ...opts, resolve });
    });

  const notify = (message) => showToast({ message });

  const runItem = (item) => {
    if (!item) return;
    if (item.disabled) {
      notify(item.hint ?? "이 명령은 사용할 수 없습니다.");
      return;
    }
    item.run({
      setCurrentPage,
      requestConfirm,
      notify,
      close: onClose,
    });
  };

  const onKeyDown = (e) => {
    // IME 조합 중에는 모든 단축키 무시 — 한글 검색 중 Enter가 항목 실행으로 흘러가는 것 방지.
    // (확정 Enter는 isComposing이 false일 때 들어옴)
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;

    if (confirmState) {
      // confirm 모달이 떠 있으면 Esc만 처리
      if (e.key === "Escape") {
        e.preventDefault();
        confirmState.resolve(false);
        setConfirmState(null);
      } else if (e.key === "Enter") {
        e.preventDefault();
        confirmState.resolve(true);
        setConfirmState(null);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(flat.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runItem(flat[activeIdx]);
    } else if (e.key === "Tab") {
      // 그룹 jump: 다음 그룹의 첫 항목으로 이동
      e.preventDefault();
      const cur = flat[activeIdx];
      if (!cur) return;
      const curGroupIdx = grouped.findIndex((g) => g.group === cur.group);
      if (curGroupIdx < 0) return;
      const next = e.shiftKey
        ? grouped[(curGroupIdx - 1 + grouped.length) % grouped.length]
        : grouped[(curGroupIdx + 1) % grouped.length];
      const targetItem = next.items[0];
      const idx = flat.findIndex((it) => it.id === targetItem.id);
      if (idx >= 0) setActiveIdx(idx);
    }
  };

  // active 항목이 보이도록 스크롤
  useEffect(() => {
    if (!listRef.current) return;
    const node = listRef.current.querySelector(`[data-idx="${activeIdx}"]`);
    if (node) node.scrollIntoView({ block: "nearest" });
  }, [activeIdx]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[1000] overflow-y-auto bg-black/40">
      <div
        className="flex min-h-full items-start justify-center p-4 pt-[14vh]"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="명령 팔레트"
        className="w-[640px] max-w-[90vw] overflow-hidden rounded-lg border border-border bg-popover shadow-2xl"
        onKeyDown={onKeyDown}
      >
        {/* 검색 입력 */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <CmdIcon className="h-4 w-4 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIdx(0);
            }}
            placeholder="명령 또는 페이지 검색..."
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <span className="text-[10px] text-muted-foreground">ESC</span>
        </div>

        {/* 결과 리스트 */}
        <div ref={listRef} className="max-h-[420px] overflow-y-auto py-1">
          {grouped.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-muted-foreground">
              일치하는 명령이 없습니다.
            </div>
          ) : (
            grouped.map((g) => (
              <div key={g.group} className="py-1">
                <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.group}
                </div>
                {g.items.map((it) => {
                  const idx = flat.findIndex((x) => x.id === it.id);
                  const active = idx === activeIdx;
                  const Icon = it.icon ?? CmdIcon;
                  return (
                    <button
                      key={it.id}
                      type="button"
                      data-idx={idx}
                      onClick={() => runItem(it)}
                      onMouseEnter={() => setActiveIdx(idx)}
                      aria-disabled={it.disabled || undefined}
                      className={cn(
                        "flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors",
                        active ? "bg-accent text-accent-foreground" : "hover:bg-muted/60",
                        it.disabled && "opacity-50"
                      )}
                    >
                      <Icon
                        className={cn(
                          "h-4 w-4 shrink-0",
                          it.danger ? "text-destructive" : "text-muted-foreground"
                        )}
                      />
                      <span className="flex-1 truncate">
                        <span className="font-medium">{it.label}</span>
                        {it.hint && (
                          <span className="ml-2 text-xs text-muted-foreground">{it.hint}</span>
                        )}
                      </span>
                      {it.disabled && (
                        <span className="rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          미설정
                        </span>
                      )}
                      {active && !it.disabled && <CornerDownLeft className="h-3.5 w-3.5 text-muted-foreground" />}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        {/* 하단 hint */}
        <div className="flex items-center justify-between border-t border-border bg-muted/30 px-4 py-2 text-[11px] text-muted-foreground">
          <span>↑↓ 이동 · ⏎ 실행 · Tab 그룹 이동 · Esc 닫기</span>
          <span>Cmd/Ctrl+K</span>
        </div>

        {/* 위험 액션 confirm */}
        <ConfirmInline
          open={!!confirmState}
          title={confirmState?.title}
          description={confirmState?.description}
          confirmLabel={confirmState?.confirmLabel}
          onConfirm={() => {
            confirmState?.resolve(true);
            setConfirmState(null);
          }}
          onCancel={() => {
            confirmState?.resolve(false);
            setConfirmState(null);
          }}
        />
      </div>
      </div>

      {/* 토스트 */}
      {toast && (
        <div className="pointer-events-none fixed bottom-8 left-1/2 -translate-x-1/2">
          <div className="rounded-md border border-border bg-popover px-4 py-2 text-xs shadow-md">
            {toast.message}
          </div>
        </div>
      )}
    </div>
  );
}
