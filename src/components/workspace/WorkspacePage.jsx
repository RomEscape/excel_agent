/**
 * WorkspacePage — Phase 1 (officeclaw) + R2 변경.
 *
 * R2 변경:
 *   - 우측 사이드 패널: "앱 내 에이전트 채팅" 흡수 (ConversationsPage에서 이전)
 *     · resizable, localStorage persist
 *   - 파일 row hover 액션: "텔레그램으로 명령 예시 보내기" — 템플릿 클립보드 복사 + 봇 딥링크
 *   - EmptyState 컴포넌트 사용 통일
 *
 * 본 페이지는 full-bleed (max-width 적용 안 함) — 데스크탑 와이드스크린 활용.
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Folder,
  File,
  ChevronRight,
  ChevronDown,
  Home,
  RefreshCw,
  Upload,
  Eye,
  X,
  FolderOpen,
  FileSpreadsheet,
  Bot,
  AlertCircle,
  Zap,
  Send as SendIcon,
  PanelRightClose,
  PanelRightOpen,
  Copy,
  Check,
  MessageCircle,
  History,
  Trash2,
  Save,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import EmptyState from "@/components/ui/empty-state";
import AlertDialog from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { toUserMessage } from "@/lib/errorMessages";
import { migrateLsKey } from "@/lib/localStorageMigration";
import useAppStore from "@/store/appStore";
import useStatusStore from "@/store/statusStore";
import {
  workspaceListFiles,
  workspaceReadFile,
  workspaceWriteFile,
  workspaceCreateExcelFile,
  workspaceWriteFileBinary,
  openWorkspaceFolder,
  openWorkspaceFile,
  excelLiveCommand,
  excelLiveSubmitApproval,
  excelLiveSaveWorkbook,
  telegramStatus,
  chatSaveMessage,
  chatListSessions,
  chatGetMessages,
  chatDeleteSession,
} from "@/lib/api";

// 텍스트로 안전하게 읽을 수 있는 확장자 (UTF-8 가정).
// 그 외(.xlsx/.pdf/.docx/.pptx/.png/.jpg)는 base64 binary 업로드.
const TEXT_EXT = new Set([
  "txt", "md", "csv", "json", "py", "js", "ts", "jsx", "tsx",
  "yaml", "yml", "toml", "sh", "html", "css", "log", "xml",
]);

// 파서/테스트로 검증된 Excel Live 예시만 노출한다.
const VERIFIED_EXCEL_EXAMPLES = Object.freeze([
  "A1:C3 범위 읽어줘",
  "B2:D2에 이름,수량,금액 입력",
  "A열 20보다 큰 값 빨간색으로 칠해줘",
  "C1에 A1:A10 합계 수식 넣어줘",
]);

const ACCEPT_ATTR =
  ".txt,.md,.csv,.json,.py,.js,.ts,.jsx,.tsx,.yaml,.yml,.toml,.sh,.html,.css,.log,.xml,.xlsx,.pdf,.docx,.pptx,.png,.jpg,.jpeg";

const EXCEL_EXT = new Set(["xlsx", "xls", "xlsm", "xlsb"]);

// ArrayBuffer → base64 (chunked, 큰 파일에서 stack overflow 방지)
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000; // 32KB 청크
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, chunk);
  }
  return btoa(binary);
}

function getExt(name) {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  return name.slice(dot + 1).toLowerCase();
}

// Microsoft Office(Excel/Word/PowerPoint)가 파일 열림 상태에서 생성하는 잠금 임시 파일.
// 예: ~$text_1.xlsx
function isOfficeLockTempFile(name) {
  return typeof name === "string" && name.startsWith("~$");
}

function extractExcelRangeTag(text) {
  const m = String(text || "").match(/\[\[EXCEL_RANGE:([A-Z0-9:]+)\]\]/i);
  return m ? m[1].toUpperCase() : null;
}

function stripExcelContextBlock(text) {
  return String(text || "")
    .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
    .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
    .trim();
}

function hasExplicitRangeInCommand(cmd) {
  return /\b([A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7}|[A-Z]{1,3}:[A-Z]{1,3}|[A-Z]{1,3}\d{1,7})\b/i.test(cmd);
}

function applyRangeContextToCommand(cmd, rangeRef) {
  const text = String(cmd || "").trim();
  if (!rangeRef || !text || hasExplicitRangeInCommand(text)) return text;
  if (!/(이\s*범위|해당\s*범위|복사한\s*범위|선택한\s*범위|여기)/i.test(text)) return text;
  return `${rangeRef} ${text}`;
}

function stringifyTsv(values, maxRows = 12, maxCols = 8) {
  if (!Array.isArray(values) || values.length === 0) return "";
  const sliced = values.slice(0, maxRows).map((row) => (
    Array.isArray(row) ? row.slice(0, maxCols) : [row]
  ));
  return sliced
    .map((row) => row.map((cell) => (cell == null ? "" : String(cell))).join("\t"))
    .join("\n");
}

function formatExcelLiveResult(action, result = {}) {
  if (!result || typeof result !== "object") return "엑셀 작업이 완료되었습니다.";
  if (action === "excel_live.list_workbooks") {
    const rows = Array.isArray(result.workbooks) ? result.workbooks : [];
    if (rows.length === 0) return "열려 있는 엑셀 통합문서가 없습니다.";
    return `열린 통합문서 ${rows.length}개: ${rows.map((r) => r.name || r.workbook_id).join(", ")}`;
  }
  if (action === "excel_live.read_range") {
    return `${result.address || ""} 범위를 읽었습니다 (${result.row_count || 0}행 × ${result.col_count || 0}열).`;
  }
  if (action === "excel_live.write_range") {
    return `${result.address || ""} 범위에 ${result.written_cells || 0}개 셀을 기록했습니다.`;
  }
  if (action === "excel_live.highlight_by_condition") {
    return `${result.address || ""} 범위에서 ${result.changed_cells || 0}개 셀을 강조했습니다.`;
  }
  if (action === "excel_live.apply_border") {
    return `${result.address || ""} 범위에 경계선을 적용했습니다 (${result.changed_cells || 0}개 셀).`;
  }
  if (action === "excel_live.set_formula") {
    return `${result.address || ""} 범위에 수식을 적용했습니다 (${result.formula_applied_cells || 0}개 셀).`;
  }
  if (action === "excel_live.save_workbook") {
    return `엑셀 파일을 저장했습니다 (${result.name || result.full_path || "현재 통합문서"}).`;
  }
  if (action === "excel_live.calculate_column_stat") {
    return `${result.header || result.column || ""} 열 ${result.stat || ""} = ${result.value}`;
  }
  if (action === "excel_live.filter_rows") {
    return `조건에 맞는 ${result.kept_rows || 0}개 행을 남기고 ${result.removed_rows || 0}개 행을 제거했습니다.`;
  }
  if (action === "excel_live.sort_rows") {
    return `${result.column || ""} 기준으로 ${result.sorted_rows || 0}개 행을 정렬했습니다.`;
  }
  if (action === "excel_live.dedupe_rows") {
    return `중복 ${result.removed_duplicates || 0}개 행을 제거했습니다 (${result.kept_rows || 0}개 유지).`;
  }
  if (action === "excel_live.drop_column") {
    return `'${result.dropped_column || ""}' 열을 삭제했습니다.`;
  }
  if (action === "excel_live.rename_column") {
    return `'${result.old_name || ""}' 열을 '${result.new_name || ""}'로 변경했습니다.`;
  }
  if (action === "excel_live.add_column") {
    return `'${result.name || ""}' 열을 추가했습니다.`;
  }
  if (action === "excel_live.group_by_aggregate") {
    const groups = Array.isArray(result.groups) ? result.groups : [];
    const preview = groups
      .slice(0, 5)
      .map((g) => `${g.key}: ${g.value}`)
      .join(", ");
    return `${result.group_column || ""}별 ${result.agg || ""} — ${preview}${groups.length > 5 ? " …" : ""}`;
  }
  return "엑셀 작업이 완료되었습니다.";
}

// ── localStorage 키 ──────────────────────────────────────────────────────────

const LS_CHAT_OPEN = "officeclaw:workspace:chat-open";
const LS_CHAT_WIDTH = "officeclaw:workspace:chat-width";

// 브랜드 변경에 따른 레거시 키(private-claw:*) → 새 키 1회 이전
migrateLsKey("private-claw:workspace:chat-open", LS_CHAT_OPEN);
migrateLsKey("private-claw:workspace:chat-width", LS_CHAT_WIDTH);

// ── 유틸 ─────────────────────────────────────────────────────────────────────

function formatSize(bytes) {
  if (bytes === 0) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function formatDate(mtime) {
  if (!mtime) return "";
  const d = new Date(mtime * 1000);
  return d.toLocaleDateString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── Breadcrumb ────────────────────────────────────────────────────────────────

function Breadcrumb({ currentPath, onNavigate }) {
  const parts = currentPath ? currentPath.split("/").filter(Boolean) : [];

  return (
    <nav className="flex items-center gap-1 text-sm text-muted-foreground flex-wrap">
      <button
        className="flex items-center gap-1 hover:text-foreground transition-colors"
        onClick={() => onNavigate("")}
      >
        <Home className="h-3.5 w-3.5" />
        Workspace
      </button>
      {parts.map((part, i) => {
        const pathSoFar = parts.slice(0, i + 1).join("/");
        return (
          <React.Fragment key={pathSoFar}>
            <ChevronRight className="h-3 w-3 shrink-0" />
            <button
              className="hover:text-foreground transition-colors truncate max-w-[120px]"
              title={part}
              onClick={() => onNavigate(pathSoFar)}
            >
              {part}
            </button>
          </React.Fragment>
        );
      })}
    </nav>
  );
}

// ── FilePreview ───────────────────────────────────────────────────────────────

function FilePreview({ file, onClose }) {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const ext = getExt(file.name);
  const isTextPreviewable = TEXT_EXT.has(ext);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    if (!isTextPreviewable) {
      setContent(null);
      setLoading(false);
      return () => { cancelled = true; };
    }
    workspaceReadFile(file.path)
      .then((data) => {
        if (!cancelled) {
          setContent(data.content);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(toUserMessage(err, "파일 미리보기를 불러오지 못했습니다."));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [file.path, isTextPreviewable]);

  return (
    <Card className="flex flex-col h-full">
      <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm font-medium truncate flex-1">{file.name}</CardTitle>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="flex-1 overflow-auto">
        {loading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <RefreshCw className="h-4 w-4 animate-spin" />
            읽는 중...
          </div>
        )}
        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}
        {!isTextPreviewable && !loading && !error && (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">미리보기 미지원 형식</p>
            <p className="mt-1">
              `{file.name}` 파일은 바이너리 형식이라 앱 내 텍스트 미리보기를 지원하지 않습니다.
              엑셀/전용 앱에서 열어 편집해 주세요.
            </p>
          </div>
        )}
        {content !== null && !loading && (
          <pre className="text-xs font-mono whitespace-pre-wrap break-all leading-relaxed">
            {content || "(빈 파일)"}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

// ── FileList ──────────────────────────────────────────────────────────────────
//
// hover 시 "텔레그램으로 명령 예시 보내기" 버튼 노출.
// 템플릿: "이 파일 요약해줘 - {name}" → 클립보드 복사 + 봇 딥링크 알림.
//

function FileList({ files, botUsername, onNavigate, onOpenFile }) {
  const [copiedPath, setCopiedPath] = useState(null);

  const handleCopyTemplate = async (e, entry) => {
    e.stopPropagation();
    const template = `워크스페이스의 ${entry.name} 파일 요약해줘`;
    try {
      await navigator.clipboard.writeText(template);
      setCopiedPath(entry.path);
      setTimeout(() => setCopiedPath(null), 1500);
    } catch {
      // ignore
    }
  };

  if (files.length === 0) {
    return (
      <EmptyState
        icon={FolderOpen}
        title="폴더가 비어있습니다."
        description="파일을 업로드하거나 텔레그램에서 파일을 추가하세요."
      />
    );
  }

  return (
    <div className="divide-y divide-border">
      {files.map((entry) => {
        const isCopied = copiedPath === entry.path;
        const deepLink = botUsername ? `https://t.me/${botUsername}` : null;
        return (
          <div
            key={entry.path}
            className="flex items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer group transition-colors"
            onClick={() => {
              if (entry.is_dir) {
                onNavigate(entry.path);
              } else {
                onOpenFile(entry);
              }
            }}
          >
            {entry.is_dir ? (
              <Folder className="h-4 w-4 shrink-0 text-blue-500" />
            ) : (
              <File className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <span className="flex-1 text-sm truncate">{entry.name}</span>
            {!entry.is_dir && (
              <span className="text-xs text-muted-foreground shrink-0">
                {formatSize(entry.size)}
              </span>
            )}
            <span className="text-xs text-muted-foreground shrink-0 hidden sm:block">
              {formatDate(entry.modified)}
            </span>

            {/* hover 액션 — 파일에 한해서 */}
            {!entry.is_dir && (
              <div className="ml-1 flex shrink-0 items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  type="button"
                  onClick={(e) => handleCopyTemplate(e, entry)}
                  className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[11px] font-medium hover:bg-muted"
                  title='템플릿 "워크스페이스의 ... 파일 요약해줘"를 복사'
                >
                  {isCopied ? (
                    <>
                      <Check className="h-3 w-3 text-green-600" />
                      복사됨
                    </>
                  ) : (
                    <>
                      <Copy className="h-3 w-3" />
                      명령 예시 복사
                    </>
                  )}
                </button>
                {deepLink && (
                  <a
                    href={deepLink}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[11px] font-medium hover:bg-muted"
                    title="텔레그램 봇으로 이동"
                  >
                    <MessageCircle className="h-3 w-3 text-blue-500" />
                    봇 열기
                  </a>
                )}
                <Eye className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── 우측 채팅 사이드 패널 ────────────────────────────────────────────────────

// 세션 영속화 fire-and-forget 헬퍼.
// chat_save_message 가 sidecar에 없으면 조용히 무시 (메모리 UX는 유지).
function persistMessageSilent(sessionId, role, text, extra = {}) {
  if (!sessionId) return; // 세션 없이 보낸 user msg는 첫 응답 도착 후 일괄 저장됨
  chatSaveMessage(
    sessionId,
    role,
    text ?? "",
    extra.toolCalls ?? null,
    extra.maskedCount ?? null,
    extra.maskedTypes ?? null,
    extra.errorText ?? null,
  ).catch(() => { /* graceful — sidecar 미지원 */ });
}

function ChatSidePanel({ ollamaState }) {
  const activeSessionId = useAppStore((s) => s.activeSessionId);
  const setActiveSessionId = useAppStore((s) => s.setActiveSessionId);
  const agentMessages = useAppStore((s) => s.agentMessages);
  const addAgentMessage = useAppStore((s) => s.addAgentMessage);
  const setAgentMessages = useAppStore((s) => s.setAgentMessages);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  // 세션 목록 드롭다운 상태
  const [sessionListOpen, setSessionListOpen] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsAvailable, setSessionsAvailable] = useState(true); // sidecar 미지원이면 false
  const [hoveredSession, setHoveredSession] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null); // {session_id, preview} | null
  const [pendingExcelApproval, setPendingExcelApproval] = useState(null);
  const [excelApprovalBusy, setExcelApprovalBusy] = useState(false);
  const [excelSaving, setExcelSaving] = useState(false);
  const [insertingRangeContext, setInsertingRangeContext] = useState(false);
  const [pendingTaskLabel, setPendingTaskLabel] = useState("");

  // textarea auto-grow: 1행 ~ 5행 (최대 ~120px)
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const lineHeight = 18; // 대략 text-xs leading
    const maxRows = 5;
    const newHeight = Math.min(ta.scrollHeight, lineHeight * maxRows + 16);
    ta.style.height = `${newHeight}px`;
  }, [input]);

  // Ollama가 명확히 준비되지 않은 상태(미설치/중지/오류)에서만 채팅 입력을 잠근다.
  // unknown/checking 중에는 허용하고, 실제 미응답이면 전송 시 503 오류로 안내한다.
  const isUnavailable =
    ollamaState === "not_installed" ||
    ollamaState === "installed_stopped" ||
    ollamaState === "error";

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [agentMessages, loading]);

  const refreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const list = await chatListSessions(20);
      setSessions(Array.isArray(list) ? list : list?.sessions || []);
      setSessionsAvailable(true);
    } catch {
      // sidecar 미지원 — 드롭다운 비활성
      setSessions([]);
      setSessionsAvailable(false);
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  // 드롭다운 열릴 때 세션 목록 새로고침
  useEffect(() => {
    if (sessionListOpen) refreshSessions();
  }, [sessionListOpen, refreshSessions]);

  const handleLoadSession = useCallback(async (sid) => {
    setSessionListOpen(false);
    try {
      const msgs = await chatGetMessages(sid);
      const list = Array.isArray(msgs) ? msgs : msgs?.messages || [];
      // sidecar의 snake_case → 프론트 camelCase 정규화
      const normalized = list.map((m) => ({
        role: m.role,
        text: m.text,
        toolCalls: m.tool_calls,
        maskedCount: m.masked_count,
        maskedTypes: m.masked_types,
        error: m.error_text || undefined,
      }));
      setAgentMessages(normalized);
      setActiveSessionId(sid);
    } catch (err) {
      addAgentMessage({
        role: "system",
        text: `세션을 불러올 수 없습니다 — ${toUserMessage(err)}`,
      });
    }
  }, [setAgentMessages, setActiveSessionId, addAgentMessage]);

  const handleDeleteSession = useCallback(async () => {
    const target = confirmDelete;
    setConfirmDelete(null);
    if (!target) return;
    try {
      await chatDeleteSession(target.session_id);
      // 현재 세션을 삭제했다면 화면도 초기화
      if (activeSessionId === target.session_id) {
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
  }, [confirmDelete, activeSessionId, setActiveSessionId, setAgentMessages, refreshSessions, addAgentMessage]);

  // 최근 대화(최대 8턴)를 OpenAI 형식 history로 변환 — 멀티턴 맥락 유지용.
  const buildHistory = useCallback(() => {
    const turns = [];
    for (const m of agentMessages) {
      if (m.error) continue;
      const role = m.role === "user" ? "user" : m.role === "agent" ? "assistant" : null;
      const content = String(m.text ?? "").trim();
      if (!role || !content) continue;
      turns.push({ role, content });
    }
    return turns.slice(-8);
  }, [agentMessages]);

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading || isUnavailable) return;

    // [[EXCEL_RANGE:..]] 태그를 실제 주소로 반영하고 컨텍스트 블록은 제거한다.
    const rangeRef = extractExcelRangeTag(trimmed);
    const cleaned = stripExcelContextBlock(trimmed);
    const message = applyRangeContextToCommand(cleaned, rangeRef) || cleaned || trimmed;

    setInput("");
    // 방금 보낼 user 메시지 이전까지의 맥락을 history로 캡처
    const history = buildHistory();
    addAgentMessage({ role: "user", text: trimmed });

    // 세션 확보 — 없으면 프론트에서 새 ID 생성 (chat_history 영속화 키)
    let sid = activeSessionId;
    if (!sid) {
      sid = crypto.randomUUID();
      setActiveSessionId(sid);
    }
    persistMessageSilent(sid, "user", trimmed);

    setLoading(true);
    setPendingTaskLabel("AI가 처리하는 중...");
    try {
      // 통합 창구 — LLM이 tool-calling으로 엑셀 작업/일반 대화를 판단한다.
      const res = await excelLiveCommand(message, null, null, false, history);
      if (res?.approval_required && res?.pending_approval) {
        setPendingExcelApproval(res.pending_approval);
        const note = res.reason || "엑셀 변경 작업은 승인 후 실행됩니다.";
        addAgentMessage({ role: "agent", text: note });
        persistMessageSilent(sid, "agent", note);
      } else {
        const text = res?.assistant_text || formatExcelLiveResult(res?.action, res?.result);
        addAgentMessage({ role: "agent", text });
        persistMessageSilent(sid, "agent", text);
      }
    } catch (err) {
      const errText = toUserMessage(err, "작업 처리 중 오류가 발생했습니다. 다시 시도해 주세요.");
      addAgentMessage({ role: "agent", text: null, error: errText });
      persistMessageSilent(sid, "agent", "", { errorText: errText });
    } finally {
      setLoading(false);
      setPendingTaskLabel("");
    }
  };

  const handleExcelApprovalConfirm = useCallback(async () => {
    if (!pendingExcelApproval) return;
    setExcelApprovalBusy(true);
    let hasNextPendingApproval = false;
    try {
      const out = await excelLiveSubmitApproval(pendingExcelApproval.approval_id, true, null);
      const text = out?.assistant_text || formatExcelLiveResult(out?.action, out?.result);
      addAgentMessage({ role: "agent", text });
      if (activeSessionId) persistMessageSilent(activeSessionId, "agent", text);

      // B안(승인 후 재개): 승인 실행 결과를 본 LLM이 다음 CONFIRM을 요청하면
      // 승인 다이얼로그를 다시 띄워 복합 명령을 승인 체인으로 이어간다.
      if (out?.approval_required && out?.pending_approval) {
        setPendingExcelApproval(out.pending_approval);
        const note = out.reason || "다음 작업도 승인이 필요합니다.";
        addAgentMessage({ role: "agent", text: note });
        if (activeSessionId) persistMessageSilent(activeSessionId, "agent", note);
        hasNextPendingApproval = true;
      }
    } catch (err) {
      addAgentMessage({
        role: "agent",
        error: toUserMessage(err, "엑셀 승인 처리 중 오류가 발생했습니다. 다시 시도해 주세요."),
      });
    } finally {
      setExcelApprovalBusy(false);
      if (!hasNextPendingApproval) {
        setPendingExcelApproval(null);
      }
      setPendingTaskLabel("");
    }
  }, [pendingExcelApproval, addAgentMessage, activeSessionId]);

  const handleExcelApprovalCancel = useCallback(async () => {
    if (!pendingExcelApproval) return;
    setExcelApprovalBusy(true);
    try {
      await excelLiveSubmitApproval(pendingExcelApproval.approval_id, false, "사용자 거부");
      addAgentMessage({
        role: "system",
        text: "엑셀 작업 실행을 취소했습니다.",
      });
    } catch {
      // ignore
    } finally {
      setExcelApprovalBusy(false);
      setPendingExcelApproval(null);
      setPendingTaskLabel("");
    }
  }, [pendingExcelApproval, addAgentMessage]);

  const handleSaveWorkbook = useCallback(async () => {
    if (loading || excelSaving || isUnavailable) return;
    setExcelSaving(true);
    try {
      const out = await excelLiveSaveWorkbook(null);
      const text = formatExcelLiveResult(out?.action, out?.result);
      addAgentMessage({ role: "system", text });
      if (activeSessionId) {
        persistMessageSilent(activeSessionId, "system", text);
      }
    } catch (err) {
      addAgentMessage({
        role: "agent",
        error: toUserMessage(err, "엑셀 저장 중 오류가 발생했습니다. 다시 시도해 주세요."),
      });
    } finally {
      setExcelSaving(false);
    }
  }, [loading, excelSaving, isUnavailable, addAgentMessage, activeSessionId]);

  const handleInsertExcelRangeContext = useCallback(async () => {
    if (loading || isUnavailable || insertingRangeContext) return;
    setInsertingRangeContext(true);
    try {
      const out = await excelLiveCommand("지금 선택한 범위 읽어줘", null, null, false);
      const result = out?.result || {};
      const address = String(result.address || "").toUpperCase();
      if (!address) {
        throw new Error("선택 범위 주소를 가져오지 못했습니다.");
      }
      const rows = Number(result.row_count || 0);
      const cols = Number(result.col_count || 0);
      const tsv = stringifyTsv(result.values, 12, 8);
      const hasMore = rows > 12 || cols > 8;
      const block = [
        `[[EXCEL_RANGE:${address}]]`,
        "[[EXCEL_VALUES_TSV]]",
        tsv || "(빈 범위)",
        hasMore ? `... (미리보기 제한: 최대 12행 x 8열, 실제 범위 ${rows}행 x ${cols}열)` : "",
        "[[/EXCEL_VALUES_TSV]]",
      ]
        .filter(Boolean)
        .join("\n");
      setInput((prev) => (prev ? `${prev}\n\n${block}\n` : `${block}\n`));
      addAgentMessage({
        role: "system",
        text: `선택 범위 ${address} (${rows}행 × ${cols}열) 참조가 입력창에 삽입되었습니다.`,
      });
    } catch (err) {
      addAgentMessage({
        role: "agent",
        error: toUserMessage(err, "엑셀 선택 범위를 가져오지 못했습니다. 먼저 Excel에서 범위를 선택해 주세요."),
      });
    } finally {
      setInsertingRangeContext(false);
    }
  }, [loading, isUnavailable, insertingRangeContext, addAgentMessage]);

  const handleKeyDown = (e) => {
    // IME 조합 중 Enter는 변환 확정 — 전송하지 않는다.
    // 한글/일본어/중국어 사용자 필수 가드.
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleNewSession = () => {
    setActiveSessionId(null);
    setAgentMessages([]);
    // 새 대화 시작 후 세션 목록 갱신 예약
    setTimeout(() => refreshSessions(), 100);
  };

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Bot className="h-4 w-4 text-primary" />
          앱 내 에이전트
        </div>
        <div className="flex items-center gap-1">
          {/* 최근 대화 토글 (sidecar 지원 시에만 노출) */}
          {sessionsAvailable && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs"
              onClick={() => setSessionListOpen((v) => !v)}
              title="최근 대화"
            >
              <History className="h-3 w-3" />
              최근 대화
              <ChevronDown
                className={cn(
                  "h-3 w-3 transition-transform",
                  sessionListOpen && "rotate-180"
                )}
              />
            </Button>
          )}
          {(activeSessionId || agentMessages.length > 0) && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={handleNewSession}
            >
              새 대화
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={handleInsertExcelRangeContext}
            disabled={isUnavailable || loading || insertingRangeContext}
            title="현재 선택한 엑셀 범위를 입력창에 참조로 삽입"
          >
            <Copy className="h-3 w-3" />
            {insertingRangeContext ? "범위 읽는 중..." : "범위 참조 삽입"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={handleSaveWorkbook}
            disabled={isUnavailable || loading || excelSaving}
            title="현재 열려 있는 엑셀 파일 저장"
          >
            <Save className="h-3 w-3" />
            {excelSaving ? "저장 중..." : "엑셀 저장"}
          </Button>
        </div>
      </div>

      {/* 최근 대화 드롭다운 */}
      {sessionListOpen && sessionsAvailable && (
        <div className="rounded-md border border-border bg-muted/30 p-1.5 text-xs">
          {sessionsLoading ? (
            <div className="flex items-center gap-1.5 px-2 py-2 text-muted-foreground">
              <RefreshCw className="h-3 w-3 animate-spin" />
              불러오는 중...
            </div>
          ) : sessions.length === 0 ? (
            <div className="px-2 py-2 text-center text-muted-foreground">
              저장된 대화가 없습니다.
            </div>
          ) : (
            <div className="max-h-48 space-y-0.5 overflow-y-auto">
              {sessions.map((s) => {
                const sid = s.session_id;
                const isActive = sid === activeSessionId;
                const isHovered = hoveredSession === sid;
                return (
                  <div
                    key={sid}
                    onMouseEnter={() => setHoveredSession(sid)}
                    onMouseLeave={() => setHoveredSession(null)}
                    className={cn(
                      "group flex items-center gap-1.5 rounded px-2 py-1.5 cursor-pointer transition-colors",
                      isActive
                        ? "bg-primary/15 text-foreground"
                        : "hover:bg-muted/60"
                    )}
                    onClick={() => handleLoadSession(sid)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleLoadSession(sid);
                    }}
                  >
                    <MessageCircle className="h-3 w-3 shrink-0 text-muted-foreground" />
                    <div className="flex-1 min-w-0">
                      <div className="truncate text-[12px]">
                        {s.preview || "(빈 대화)"}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {s.message_count ?? 0}개 메시지 ·{" "}
                        {s.last_message_at
                          ? new Date(s.last_message_at).toLocaleString("ko-KR", {
                              month: "2-digit",
                              day: "2-digit",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : ""}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmDelete({ session_id: sid, preview: s.preview });
                      }}
                      className={cn(
                        "shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/15 hover:text-destructive transition-opacity",
                        isHovered ? "opacity-100" : "opacity-0"
                      )}
                      title="이 대화 삭제"
                      aria-label="대화 삭제"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        파일을 보면서 즉석에서 질문하세요. 메신저 노출 없이 PC에서만 처리됩니다.
      </p>

      {/* 세션 삭제 확인 */}
      <AlertDialog
        open={!!confirmDelete}
        title="대화 삭제"
        description={
          confirmDelete
            ? `"${confirmDelete.preview || "(빈 대화)"}" 대화를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`
            : ""
        }
        confirmLabel="삭제"
        confirmVariant="destructive"
        onConfirm={handleDeleteSession}
        onCancel={() => setConfirmDelete(null)}
      />
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
        onConfirm={handleExcelApprovalConfirm}
        onCancel={handleExcelApprovalCancel}
      />

      {/* 메시지 영역 */}
      <div className="flex-1 overflow-y-auto rounded-lg border border-border bg-muted/20 p-3">
        {agentMessages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-center text-xs text-muted-foreground">
            <div>
              <Bot className="mx-auto mb-2 h-8 w-8 opacity-30" />
              <p>검증된 명령 예시</p>
              <div className="mt-2 flex flex-wrap items-center justify-center gap-1.5">
                {VERIFIED_EXCEL_EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setInput(ex)}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] hover:bg-muted"
                    title="클릭하면 입력창에 채워집니다"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {agentMessages.map((msg, idx) => {
              if (msg.role === "system") {
                return (
                  <div key={idx} className="flex justify-center">
                    <span className="rounded-full bg-muted/60 px-3 py-1 text-[11px] text-muted-foreground">
                      {msg.text}
                    </span>
                  </div>
                );
              }
              if (msg.role === "agent" && msg.error) {
                return (
                  <div key={idx} className="text-left text-sm">
                    <span className="inline-flex max-w-full items-start gap-1.5 rounded-lg border border-red-300 bg-red-50 px-3 py-1.5 text-xs text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
                      <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                      {msg.error}
                    </span>
                  </div>
                );
              }
              const hasMasking = msg.role === "agent" && msg.maskedCount > 0;
              return (
                <div
                  key={idx}
                  className={cn(
                    "text-sm",
                    msg.role === "user" ? "text-right" : "text-left text-muted-foreground"
                  )}
                >
                  <span
                    className={cn(
                      "inline-block max-w-full break-words rounded-lg px-3 py-1.5 text-xs",
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "border border-border bg-background"
                    )}
                  >
                    {msg.text}
                  </span>
                  {hasMasking && (
                    <span
                      className="ml-1.5 inline-block cursor-default align-middle"
                      title={`${msg.maskedTypes?.join(", ")} ${msg.maskedCount}건이 자동 마스킹되어 AI에 전달되지 않았습니다`}
                    >
                      🛡
                    </span>
                  )}
                  {msg.toolCalls && msg.toolCalls.length > 0 && (
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      <Zap className="mr-1 inline h-3 w-3" />
                      {msg.toolCalls.length}개 도구 실행됨
                    </div>
                  )}
                </div>
              );
            })}
            {loading && (
              <div className="text-left text-sm text-muted-foreground">
                <span className="inline-flex max-w-full items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-xs shadow-sm">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-primary" />
                  <span className="flex flex-col gap-0.5">
                    <span className="font-medium text-foreground">작업 처리 중</span>
                    <span className="text-[11px] text-muted-foreground">
                      {pendingTaskLabel || "요청을 처리하고 있습니다..."}
                    </span>
                  </span>
                </span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* 입력 영역 */}
      {isUnavailable ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Ollama(로컬 AI 엔진)가 실행되지 않아 사용할 수 없습니다.
        </div>
      ) : (
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="에이전트에게 메시지... (Shift+Enter 줄바꿈)"
            disabled={loading}
            className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-1.5 text-xs leading-[18px] placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 max-h-[110px] overflow-y-auto"
          />
          <Button size="sm" onClick={handleSend} disabled={!input.trim() || loading}>
            <SendIcon className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}

// ── 메인 WorkspacePage ──────────────────────────────────────────────────────

export default function WorkspacePage() {
  const ollamaState = useStatusStore((s) => s.modules.ollama.state);
  const workspacePath = useAppStore((s) => s.workspacePath);
  const [currentPath, setCurrentPath] = useState("");
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [previewFile, setPreviewFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [creatingExcel, setCreatingExcel] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [botUsername, setBotUsername] = useState(null);

  // 채팅 사이드 패널 상태 — localStorage persist
  const [chatOpen, setChatOpen] = useState(() => {
    try {
      const v = localStorage.getItem(LS_CHAT_OPEN);
      return v == null ? true : v === "true";
    } catch {
      return true;
    }
  });
  const [chatWidth, setChatWidth] = useState(() => {
    try {
      const v = parseInt(localStorage.getItem(LS_CHAT_WIDTH) ?? "", 10);
      return Number.isFinite(v) && v >= 280 && v <= 720 ? v : 360;
    } catch {
      return 360;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(LS_CHAT_OPEN, String(chatOpen));
    } catch {}
  }, [chatOpen]);
  useEffect(() => {
    try {
      localStorage.setItem(LS_CHAT_WIDTH, String(chatWidth));
    } catch {}
  }, [chatWidth]);

  // resize handle drag
  const draggingRef = useRef(false);
  useEffect(() => {
    const onMove = (e) => {
      if (!draggingRef.current) return;
      const right = window.innerWidth - e.clientX;
      const clamped = Math.max(280, Math.min(720, right));
      setChatWidth(clamped);
    };
    const onUp = () => {
      draggingRef.current = false;
      document.body.style.cursor = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const loadFiles = useCallback(async (path = "") => {
    setLoading(true);
    setError("");
    try {
      const data = await workspaceListFiles(path);
      const rows = Array.isArray(data.files) ? data.files : [];
      setFiles(rows.filter((entry) => !isOfficeLockTempFile(entry?.name)));
    } catch (err) {
      setError(toUserMessage(err, "파일 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles(currentPath);
  }, [currentPath, loadFiles]);

  // bot_username 1회 로드
  useEffect(() => {
    telegramStatus()
      .then((s) => setBotUsername(s?.bot_username ?? s?.username ?? null))
      .catch(() => {});
  }, []);

  const handleNavigate = useCallback((path) => {
    setCurrentPath(path);
    setPreviewFile(null);
  }, []);

  const handleOpenFile = useCallback(async (file) => {
    const ext = getExt(file?.name || "");
    if (EXCEL_EXT.has(ext)) {
      try {
        await openWorkspaceFile(file.path);
      } catch (err) {
        setError(`파일 열기 실패: ${toUserMessage(err)}`);
      }
      return;
    }
    setPreviewFile(file);
  }, []);

  const handleOpenFolder = useCallback(async () => {
    try {
      await openWorkspaceFolder();
    } catch (err) {
      setError(`폴더 열기 실패: ${toUserMessage(err)}`);
    }
  }, []);

  const handleCreateExcelFile = useCallback(async () => {
    if (loading || uploading || creatingExcel) return;

    const now = new Date();
    const pad2 = (n) => String(n).padStart(2, "0");
    const defaultName = `새_엑셀_${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}_${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}.xlsx`;
    const inputName = window.prompt("새 엑셀 파일 이름을 입력하세요 (.xlsx)", defaultName);
    if (inputName === null) return;

    let fileName = String(inputName).trim();
    if (!fileName) {
      setError("파일 이름을 입력해 주세요.");
      return;
    }
    if (!fileName.toLowerCase().endsWith(".xlsx")) {
      fileName = `${fileName}.xlsx`;
    }

    const targetPath = currentPath ? `${currentPath}/${fileName}` : fileName;
    setCreatingExcel(true);
    setError("");
    try {
      await workspaceCreateExcelFile(targetPath, "Sheet1");
      setUploadMessage(`새 엑셀 파일 생성 완료: ${fileName}`);
      await loadFiles(currentPath);
      await openWorkspaceFile(targetPath);
    } catch (err) {
      setError(`엑셀 파일 생성 실패: ${toUserMessage(err)}`);
    } finally {
      setCreatingExcel(false);
    }
  }, [loading, uploading, creatingExcel, currentPath, loadFiles]);

  const fileInputRef = useRef(null);
  const [uploadProgress, setUploadProgress] = useState(null); // {current, total, name} | null

  const handleUpload = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  // 단일 파일 업로드 — 확장자에 따라 text vs binary 분기.
  // 바이너리 endpoint 호출이 실패하면 silent fallback(텍스트로 base64 저장) 대신
  // 명확한 에러를 throw해서 사용자가 깨진 PDF를 받기 전에 알도록 한다.
  const uploadOneFile = useCallback(async (file, targetPath) => {
    const ext = getExt(file.name);
    const isText = TEXT_EXT.has(ext);

    if (isText) {
      const text = await file.text();
      await workspaceWriteFile(targetPath, text);
      return;
    }

    // 바이너리 — Tauri command `workspace_write_file_binary` 정식 path만 사용.
    // 실패 시 사용자에게 명확하게 안내하고 그 외 fallback은 하지 않는다.
    const buf = await file.arrayBuffer();
    const b64 = arrayBufferToBase64(buf);
    try {
      await workspaceWriteFileBinary(targetPath, b64);
    } catch (err) {
      const rawMsg = String(err?.message ?? err);
      const msg = toUserMessage(err);
      const looksLikeMissingCommand =
        rawMsg.includes("workspace_write_file_binary") ||
        rawMsg.toLowerCase().includes("not found") ||
        rawMsg.toLowerCase().includes("unknown") ||
        rawMsg.toLowerCase().includes("not allowed");
      if (looksLikeMissingCommand) {
        // 비개발자 친화적 카피: "Tauri" 같은 개발자 용어 노출 회피.
        // 개발 모드에서는 dev.sh 재실행, 프로덕션에서는 앱 종료 후 재실행으로 안내.
        // import.meta.env.DEV로 분기해 각 사용자에게 의미있는 액션만 보여준다.
        const isDev = import.meta.env?.DEV;
        const restartHint = isDev
          ? "개발 모드에서는 ./dev.sh를 다시 실행해 주세요"
          : "앱을 완전히 종료한 뒤 다시 실행해 주세요";
        throw new Error(`${file.name} — 이 형식의 파일을 처리하려면 앱을 다시 시작해 주세요. (${restartHint}. 임시 우회: "폴더 열기"로 직접 복사)`);
      }
      throw new Error(`${file.name} — 업로드 실패: ${msg}`);
    }
  }, []);

  const handleFileSelected = useCallback(async (e) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);

    setUploading(true);
    setUploadMessage("");
    const failures = [];

    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      setUploadProgress({ current: i + 1, total: files.length, name: file.name });
      const targetPath = currentPath ? `${currentPath}/${file.name}` : file.name;
      try {
        await uploadOneFile(file, targetPath);
      } catch (err) {
        failures.push(toUserMessage(err));
      }
    }

    setUploadProgress(null);
    setUploading(false);
    e.target.value = "";

    const success = files.length - failures.length;
    const parts = [];
    if (success > 0) parts.push(`${success}개 업로드 완료`);
    if (failures.length > 0) parts.push(`${failures.length}개 실패 — ${failures[0]}${failures.length > 1 ? ` 외 ${failures.length - 1}건` : ""}`);
    setUploadMessage(parts.join(" · ") || "업로드 결과 없음");

    await loadFiles(currentPath);
  }, [currentPath, loadFiles, uploadOneFile]);

  return (
    <div className="flex h-full">
      {/* 메인 영역 */}
      <div className="flex flex-col flex-1 min-w-0 gap-4">
        {/* 툴바 */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-lg font-semibold">워크스페이스</h1>
            <Breadcrumb currentPath={currentPath} onNavigate={handleNavigate} />
            <p className="mt-1 text-xs text-muted-foreground">
              업로드 위치: `{workspacePath}`{currentPath ? `/${currentPath}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={handleOpenFolder}>
              <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
              폴더 열기
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleUpload}
              disabled={uploading || creatingExcel}
            >
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              {uploading ? "업로드 중..." : "파일 업로드"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleCreateExcelFile}
              disabled={loading || uploading || creatingExcel}
              title="현재 폴더에 새 엑셀 파일 생성"
            >
              <FileSpreadsheet className="mr-1.5 h-3.5 w-3.5" />
              {creatingExcel ? "생성 중..." : "새 엑셀 파일"}
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileSelected}
              accept={ACCEPT_ATTR}
            />
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => loadFiles(currentPath)}
              disabled={loading}
              title="새로고침"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => {
                setChatOpen((v) => !v);
              }}
              title={chatOpen ? "에이전트 채팅 닫기" : "에이전트 채팅 열기"}
            >
              {chatOpen ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRightOpen className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>

        {/* 업로드 진행률 (다중 파일 시) */}
        {uploadProgress && (
          <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
            <div className="flex items-center justify-between text-xs">
              <span className="truncate">
                업로드 중 ({uploadProgress.current}/{uploadProgress.total})
                {": "}
                <span className="font-medium">{uploadProgress.name}</span>
              </span>
              <span className="font-mono text-muted-foreground">
                {Math.round((uploadProgress.current / uploadProgress.total) * 100)}%
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{
                  width: `${(uploadProgress.current / uploadProgress.total) * 100}%`,
                }}
              />
            </div>
          </div>
        )}

        {/* 업로드 결과 메시지 — 실패가 포함되면 amber로 시각 강조 */}
        {uploadMessage && (
          <div
            className={cn(
              "flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm",
              uploadMessage.includes("실패")
                ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
                : "border-border bg-muted"
            )}
          >
            <span className="flex-1 break-words">{uploadMessage}</span>
            <button
              onClick={() => setUploadMessage("")}
              className="shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="메시지 닫기"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* 오류 */}
        {error && (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* 파일 목록 + 미리보기 (좌우 split) */}
        <div className="flex flex-1 gap-4 min-h-0">
          <Card className="flex-1 overflow-auto">
            <CardContent className="p-0">
              {loading ? (
                <div className="flex items-center gap-2 p-6 text-muted-foreground text-sm">
                  <RefreshCw className="h-4 w-4 animate-spin" />
                  불러오는 중...
                </div>
              ) : (
                <FileList
                  files={files}
                  botUsername={botUsername}
                  onNavigate={handleNavigate}
                  onOpenFile={handleOpenFile}
                />
              )}
            </CardContent>
          </Card>

          {/* 파일 미리보기 패널 */}
          {previewFile && (
            <div className="w-80 shrink-0 flex flex-col">
              <FilePreview file={previewFile} onClose={() => setPreviewFile(null)} />
            </div>
          )}
        </div>
      </div>

      {/* resize handle + 채팅 사이드 패널 */}
      {chatOpen && (
        <>
          <div
            role="separator"
            aria-orientation="vertical"
            onMouseDown={() => {
              draggingRef.current = true;
              document.body.style.cursor = "col-resize";
            }}
            className="ml-3 mr-1 w-1 cursor-col-resize bg-transparent hover:bg-primary/40 transition-colors"
          />
          <aside
            className="flex shrink-0 flex-col border-l border-border pl-3"
            style={{ width: `${chatWidth}px` }}
            aria-label="에이전트 채팅"
          >
            <ChatSidePanel ollamaState={ollamaState} />
          </aside>
        </>
      )}
    </div>
  );
}
