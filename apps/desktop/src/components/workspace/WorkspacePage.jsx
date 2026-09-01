/**
 * WorkspacePage — Phase 1 (Private-Claw) + R2 변경.
 *
 * R2 변경:
 *   - 우측 사이드 패널: "앱 내 에이전트 채팅" 흡수 (ConversationsPage에서 이전)
 *     · resizable, localStorage persist
 *   - 파일 row hover 액션: "텔레그램으로 명령 예시 보내기" — 템플릿 클립보드 복사 + 봇 딥링크
 *   - EmptyState 컴포넌트 사용 통일
 *
 * 본 페이지는 full-bleed (max-width 적용 안 함) — 데스크탑 와이드스크린 활용.
 *
 * 2026-08-19: 창 크기를 따라간다.
 *   - 채팅 패널 폭은 컨테이너 폭에 상대적으로 깎는다(파일 목록에 최소 420px).
 *   - 컨테이너가 760px보다 좁으면 좌우 대신 위(파일)·아래(채팅)로 쌓는다.
 *   규칙은 `lib/workspaceLayout.js`가 갖고, 여기서는 그 값을 읽어 배치만 한다.
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import { ExcelTargetBar } from "./ExcelTargetBar.jsx";
import { buildPasteBlock, displayMessageText, isExcelSelectionPaste, pasteHasValues } from "@/lib/excelPaste.js";
// 범위 판정·"여기" 접두는 순수 함수로 뺐다(테스트 가능, 러너와 규칙 공유).
import { applyRangeContextToCommand, hasExplicitRangeInCommand } from "@/lib/excelRangeContext.js";
import { useExcelTarget } from "@/hooks/useExcelTarget.js";
import { useElementWidth } from "@/hooks/useElementWidth.js";
import {
  STACKED_CHAT_HEIGHT_RATIO,
  STACKED_CHAT_MIN_HEIGHT,
  clampChatWidth,
  readStoredChatWidth,
  resolveWorkspaceLayout,
} from "@/lib/workspaceLayout.js";
import { noteExcelTargetFromResult } from "@/lib/excelTargetManager.js";
import {
  decideExcelRoute,
  isChatFallbackResponse,
  isSafetyStopResponse,
} from "@/lib/excelRouting.js";
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
  RotateCcw,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import EmptyState from "@/components/ui/empty-state";
import AlertDialog from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { toUserMessage } from "@/lib/errorMessages";
import { splitExcelCompositeCommand } from "@/lib/excelCommandUtils";
import {
  CHAT_REQUEST_TIMEOUT_MS,
  EXCEL_REQUEST_TIMEOUT_MS,
  runWithPolicy,
} from "@/lib/requestPolicy";
import { answerMacroFollowUp, startMacroPlan } from "@/lib/excelMacroManager";
import ExcelMacroCard from "@/components/workspace/ExcelMacroCard";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import useExcelMacroStore from "@/store/excelMacroStore";
import {
  workspaceListFiles,
  workspaceReadFile,
  workspaceWriteFile,
  workspaceCreateExcelFile,
  workspaceWriteFileBinary,
  openWorkspaceFolder,
  openWorkspaceFile,
  agentChat,
  excelLiveCommand,
  excelLiveSelection,
  traceClientEvent,
  excelLiveStatus,
  excelLiveSubmitApproval,
  excelLiveSaveWorkbook,
  excelLiveListBackups,
  excelLiveRestoreLastBackup,
  harnessFeedback,
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
  ".txt,.md,.csv,.json,.py,.js,.ts,.jsx,.tsx,.yaml,.yml,.toml,.sh,.html,.css,.log,.xml,.xlsx,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.zip";

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


// hasLikelyExcelActionIntent는 2026-08-16에 지웠다. 두 번째 키워드 목록이 첫 번째와
// 부분 중복된 채 따로 관리되고 있었는데, 이제 라우팅이 "워크북이 열려 있는가"로
// 결정되므로 약한 키워드 판정 자체가 필요 없다. 판정은 사이드카가 한다.

function isTransientUploadError(err) {
  const msg = String(err?.message ?? err ?? "").toLowerCase();
  return (
    msg.includes("connection refused") ||
    msg.includes("error sending request") ||
    msg.includes("failed to fetch") ||
    msg.includes("econnrefused") ||
    msg.includes("timed out") ||
    msg.includes("timeout") ||
    msg.includes("요청 타임아웃") ||
    msg.includes("응답 읽기 실패")
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toUploadUserMessage(err) {
  const mapped = toUserMessage(err);
  const raw = String(err?.message ?? err ?? "").trim();
  if (mapped === "오류가 발생했습니다. 다시 시도해 주세요." && raw) {
    return raw;
  }
  return mapped;
}

function extractExcelRangeTag(text) {
  const m = String(text || "").match(/\[\[EXCEL_RANGE:([A-Z0-9:]+)\]\]/i);
  return m ? m[1].toUpperCase() : null;
}

function stripExcelContextBlock(text) {
  return String(text || "")
    .replace(/\[\[EXCEL_RANGE:[A-Z0-9:]+\]\]/gi, "")
    .replace(/\[\[EXCEL_VALUES_TSV\]\][\s\S]*?\[\[\/EXCEL_VALUES_TSV\]\]/gi, "")
    // 붙여넣기 안내 문구. 사용자에게 보여 줄 말이지 모델에게 보낼 말이 아니다 —
    // 안 지우면 명령문에 섞이고, 그 안의 범위가 "문장에 범위가 있다"로 잡혀
    // context_range 전달까지 막는다(2026-08-17 실측).
    .replace(/\[\[EXCEL_PASTE_NOTE\]\][\s\S]*?\[\[\/EXCEL_PASTE_NOTE\]\]/gi, "")
    .trim();
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

function formatExcelLiveResult(action, result = {}, reason = "") {
  if (!result || typeof result !== "object") return "엑셀 작업이 완료되었습니다.";
  // 실행은 성공했지만 사용자 눈에 보이는 변화가 없는 턴. 사이드카가 reason에
  // 그 사실을 적어 보낸다("지울 값이 없는 범위였습니다…"). 여기서 범용 "완료"로
  // 덮으면 사용자는 명령이 씹혔다고 생각한다(2026-08-17 GUI 실측 — 두 번).
  if (result.no_matching_cells && String(reason || "").trim()) {
    const honest = String(reason).trim();
    return result.execution_report ? `${honest}\n${result.execution_report}` : honest;
  }
  // 단계별 실행 보고 — "어떤 방식으로 수정했는지"를 매 실행마다 그대로 보여준다
  // (2026-08-18 사용자 요구: 화면 정확성 최대치). 사이드카가 실제 실행된 단계·
  // 대상 범위·규모·재시도/검증 예외까지 조립해 보낸다.
  if (String(result.execution_report || "").trim()) {
    return String(result.execution_report).trim();
  }
  if (action === "excel_live.clear_range") {
    const emptied = result.emptied_values;
    const suffix = typeof emptied === "number" ? ` (값 ${emptied}개 삭제)` : "";
    return `${result.address || ""} 범위를 비웠습니다${suffix}.`;
  }
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
  if (action === "excel_live.create_table") {
    return `${result.address || ""} 범위에 ${result.rows || 0}행 × ${result.cols || 0}열 표를 생성했습니다.`;
  }
  if (action === "excel_live.highlight_by_condition") {
    return `${result.address || ""} 범위에서 ${result.changed_cells || 0}개 셀을 강조했습니다.`;
  }
  if (action === "excel_live.fill_range") {
    return `${result.address || ""} 범위의 배경색을 변경했습니다 (${result.changed_cells || 0}개 셀).`;
  }
  if (action === "excel_live.apply_border") {
    return `${result.address || ""} 범위에 경계선을 적용했습니다 (${result.changed_cells || 0}개 셀).`;
  }
  if (action === "excel_live.set_formula") {
    return `${result.address || ""} 범위에 수식을 적용했습니다 (${result.formula_applied_cells || 0}개 셀).`;
  }
  if (action === "excel_live.verify_formula_result") {
    return `${result.address || ""} 검증 결과: 비어있지 않은 셀 ${result.non_empty_cells || 0}개, 숫자 셀 ${result.numeric_cells || 0}개, 합계 ${result.sum ?? 0}, 평균 ${result.average ?? 0}`;
  }
  if (action === "excel_live.sort_range") {
    return `${result.address || ""} 범위를 ${result.order === "desc" ? "내림차순" : "오름차순"}으로 정렬했습니다.`;
  }
  if (action === "excel_live.filter_rows") {
    return `${result.address || ""} 범위에서 조건에 맞는 ${result.filtered_rows || 0}개 행을 필터링했습니다.`;
  }
  if (action === "excel_live.dedupe_rows") {
    return `${result.address || ""} 범위에서 중복 ${result.removed_rows || 0}개 행을 제거했습니다.`;
  }
  if (action === "excel_live.find_replace") {
    // "완료"라고만 하면 0건 치환도 성공처럼 읽힌다(2026-08-17 실측).
    return `${result.address || ""} 범위에서 ${result.replaced_cells || 0}개 셀을 바꿨습니다.`;
  }
  if (action === "excel_live.pivot_table") {
    return `${result.sheet_name || ""} 시트에 집계표를 생성했습니다 (${result.rows || 0}행 × ${result.cols || 0}열).`;
  }
  if (action === "excel_live.create_chart") {
    return `${result.sheet_name || ""} 시트에 ${result.chart_type || "line"} 차트를 생성했습니다.`;
  }
  if (action === "excel_live.validate_data") {
    return `${result.address || ""} 범위 검증 결과, 이슈 ${result.total_issues || 0}건을 찾았습니다.`;
  }
  if (action === "excel_live.save_workbook") {
    return `엑셀 파일을 저장했습니다 (${result.name || result.full_path || "현재 통합문서"}).`;
  }
  if (action === "excel_live.restore_last_backup") {
    return `최근 백업으로 복구했습니다 (${result.name || result.full_path || "현재 통합문서"}).`;
  }
  return "엑셀 작업이 완료되었습니다.";
}

// 사이드카는 실행/검증 실패를 예외가 아니라 **HTTP 200 + ok:false + reason**으로 알린다.
// api.js의 parseResponse는 최상위 error/detail 문자열만 throw하므로 이 응답은 그냥 통과한다.
// 여기서 ok를 명시적으로 읽지 않으면, 파일이 하나도 안 바뀐 턴에도
// formatExcelLiveResult의 폴백인 "엑셀 작업이 완료되었습니다."가 찍힌다(2026-08-16 실측).
function formatExcelLiveFailure(payload = {}) {
  const reason = String(payload?.reason || "").trim();
  const result = payload?.result || {};
  const steps = Array.isArray(result.failed_steps)
    ? result.failed_steps.filter(Boolean).map(String)
    : [];
  const detail = String(result.failure_detail || "").trim();
  const body = [reason, ...steps, detail]
    .filter(Boolean)
    // 같은 문장이 reason과 failed_steps에 중복으로 실려 오는 경우가 있다.
    .filter((line, idx, all) => all.indexOf(line) === idx)
    .join("\n");
  return body ? `엑셀 작업에 실패했습니다.\n${body}` : "엑셀 작업에 실패했습니다.";
}

// ── localStorage 키 ──────────────────────────────────────────────────────────

const LS_CHAT_OPEN = "private-claw:workspace:chat-open";
const LS_CHAT_WIDTH = "private-claw:workspace:chat-width";

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

function FileList({ files, botUsername, onNavigate, onOpenFile, compact = false }) {
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
            // min-w-0: 행 자체가 부모보다 좁아질 수 있어야 이름 칸이 말줄임된다.
            // 이게 없으면 좁은 창에서 행이 카드 밖으로 넘치거나 글자가 세로로 흐른다.
            className="flex min-w-0 items-center gap-3 px-3 py-2.5 hover:bg-muted/40 cursor-pointer group transition-colors"
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
            <span className="flex-1 min-w-0 text-sm truncate" title={entry.name}>
              {entry.name}
            </span>
            {!entry.is_dir && (
              <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">
                {formatSize(entry.size)}
              </span>
            )}
            {/* 파일 패널이 좁으면(나란히 배치의 좁은 창) 날짜를 숨겨 이름에 폭을 준다.
                sm: 같은 뷰포트 브레이크포인트로는 안 된다 — 창은 넓어도 이 패널은 좁을 수 있다. */}
            {!compact && (
              <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">
                {formatDate(entry.modified)}
              </span>
            )}

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

function ChatSidePanel({ sidecarState }) {
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
  const [excelRestoreBusy, setExcelRestoreBusy] = useState(false);
  const [pendingExcelRestore, setPendingExcelRestore] = useState(null);
  const [insertingRangeContext, setInsertingRangeContext] = useState(false);
  const [pendingTaskLabel, setPendingTaskLabel] = useState("");
  const [pendingExcelComposite, setPendingExcelComposite] = useState(null);
  const [lastExcelRangeRef, setLastExcelRangeRef] = useState(null);
  // 직전 턴이 엑셀 흐름 한가운데였는지. 되묻기·승인 대기·취소 뒤에 오는 답변은
  // "일별로 만들어줄래?", "다시 제안해줄래?"처럼 엑셀 키워드가 없다. 그걸 일반 대화로
  // 보내면 모델이 문맥 없이 지어낸다 — 2026-08-16 실측에서 승인 취소 뒤 "다시 제안해줄래?"에
  // 도시 교통 정책 에세이가 돌아왔다.
  const excelFollowUpRef = useRef(false);
  // 엑셀 경로 전용 세션 키. activeSessionId는 일반 채팅(/agent/chat)이 만들어 주는데,
  // 엑셀만 쓰는 대화에서는 끝까지 null이다. 그러면 사이드카가 매 턴 stateless 키를 새로
  // 발급해 **되묻기 슬롯이 다음 턴에 통째로 사라진다** — 2026-08-16 실측에서
  // "출석부 표 만들어줘" → "일별/월별?" → "일별로"가 정렬 명령으로 재해석됐다.
  const excelTarget = useExcelTarget();
  const excelSessionIdRef = useRef(null);
  // 렌더 중이 아니라 **명령을 보낼 때** 한 번 발급한다(ref는 렌더에서 읽으면 안 된다).
  // activeSessionId를 섞어 쓰면 대화 도중 채팅 세션이 생기는 순간 키가 바뀌어
  // 슬롯이 또 사라진다. 페이지가 사는 동안 고정된 키 하나만 쓴다.
  const excelSessionKey = useCallback(() => {
    if (excelSessionIdRef.current == null) {
      const rand = globalThis.crypto?.randomUUID?.() ?? `${performance.now()}`.replace(".", "");
      excelSessionIdRef.current = `excel-live::ui::${rand}`;
    }
    return excelSessionIdRef.current;
  }, []);
  const [messageFeedback, setMessageFeedback] = useState({});
  const [feedbackBusyKey, setFeedbackBusyKey] = useState("");
  const pendingUserMsgRef = useRef(null); // session 발급 전 첫 user msg 임시 보관

  // 언제까지 기다리고 언제 다시 보내도 되는지는 `lib/requestPolicy`가 정한다.
  // 편집 명령은 `repeatable: false`라 타임아웃 뒤에 다시 보내지 않는다.
  const runWithRetry = useCallback(
    (fn, label, options = {}) =>
      runWithPolicy(fn, {
        label,
        ...options,
        onSlow: () => setPendingTaskLabel(`${label} 실행 중... (오래 걸리고 있습니다)`),
        onRetry: () => setPendingTaskLabel(`${label} 재시도 중...`),
      }),
    []
  );

  const buildFeedbackKey = useCallback(
    (idx, msg) => {
      const sessionKey = activeSessionId || "no_session";
      const routeKey = String(msg?.sourceRoute || "");
      return `${sessionKey}:${idx}:${routeKey}`;
    },
    [activeSessionId]
  );

  const findPreviousUserText = useCallback(
    (idx) => {
      for (let i = idx - 1; i >= 0; i -= 1) {
        if (agentMessages[i]?.role === "user") {
          return String(agentMessages[i]?.text || "").trim();
        }
      }
      return "";
    },
    [agentMessages]
  );

  const handleMessageFeedback = useCallback(
    async (idx, rating) => {
      const msg = agentMessages[idx];
      if (!msg || msg.role !== "agent") return;

      const key = buildFeedbackKey(idx, msg);
      if (feedbackBusyKey === key) return;

      let reason = "";
      if (rating === "bad") {
        const typed = window.prompt("아쉬운 점을 짧게 남겨주세요. (선택)", "");
        if (typed === null) return; // 취소
        reason = String(typed || "").trim();
      }

      const route =
        String(msg.sourceRoute || "").trim() ||
        (msg.toolCalls?.length ? "/agent/chat" : "/excel-live/command");
      const userMessage = findPreviousUserText(idx);
      const targetMessage = userMessage || String(msg.text || "").trim();

      setFeedbackBusyKey(key);
      try {
        await harnessFeedback({
          sessionId: activeSessionId,
          route,
          message: targetMessage,
          rating,
          reason:
            reason ||
            (rating === "good"
              ? "workspace_like_button"
              : "workspace_dislike_button"),
        });
        setMessageFeedback((prev) => ({
          ...prev,
          [key]: rating,
        }));
      } catch {
        // 피드백 저장 실패는 사용자 작업 흐름을 막지 않는다.
      } finally {
        setFeedbackBusyKey("");
      }
    },
    [
      activeSessionId,
      agentMessages,
      buildFeedbackKey,
      feedbackBusyKey,
      findPreviousUserText,
    ]
  );

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

  // 사이드카가 끊기면 채팅(/agent/chat)도 엑셀 명령(/excel-live/command)도 전부 실패한다.
  // 예전에는 OpenClaw 게이트웨이 상태를 봤는데, LLM 경로에서 OpenClaw가 빠진 뒤로
  // 게이트웨이 실행 여부는 이 패널과 아무 상관이 없어졌다.
  // "checking"은 기동 직후 잠깐 거치는 값이라 막지 않는다.
  const isUnavailable = sidecarState === "error";

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
        sourceRoute:
          m.source_route ||
          (Array.isArray(m.tool_calls) && m.tool_calls.length > 0
            ? "/agent/chat"
            : "/excel-live/command"),
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

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading || isUnavailable) return;
    setInput("");
    // 말풍선·저장에는 사람용 문구를 쓴다. `[[EXCEL_RANGE:…]]` 마크업은 모델과의
    // 약속이라 원문(trimmed)에만 남기고, 화면에 그대로 노출하지 않는다(2026-08-17 실측).
    const displayText = displayMessageText(trimmed);
    addAgentMessage({ role: "user", text: displayText });

    // 매크로가 되묻는 중이면 이 문장은 새 명령이 아니라 그 질문에 대한 답이다.
    // 여기서 갈라내지 않으면 답변이 별개 명령으로 실행되고 매크로는 멈춘 채 남는다.
    if (useExcelMacroStore.getState().status === "waiting_input") {
      setLoading(true);
      setPendingTaskLabel("매크로 이어서 진행 중...");
      try {
        await answerMacroFollowUp(trimmed);
      } catch (err) {
        addAgentMessage({ role: "agent", text: null, error: toUserMessage(String(err?.message ?? err)) });
      } finally {
        setLoading(false);
        setPendingTaskLabel("");
      }
      return;
    }

    // 이미 세션이 있으면 즉시 저장. 없으면 응답 후 session_id 받고 일괄 저장.
    // 저장도 사람용 문구로 한다 — 다시 열었을 때 마크업이 되살아나면 같은 문제다.
    if (activeSessionId) {
      persistMessageSilent(activeSessionId, "user", displayText);
    } else {
      pendingUserMsgRef.current = displayText;
    }

    setLoading(true);
    try {
      // 직전 턴이 되묻기·승인 대기·취소로 끝났으면 이 문장은 그 답변이다.
      // 키워드가 없다고 일반 대화로 보내면 문맥이 끊겨 엉뚱한 답이 나온다.
      const wasExcelFollowUp = excelFollowUpRef.current;
      excelFollowUpRef.current = false;

      // 라우팅 기본값을 뒤집는다 (2026-08-16).
      //
      // 예전에는 키워드 화이트리스트가 통과시킨 문장만 엑셀로 갔다. 실측으로
      // 자연스러운 엑셀 요청 22건 중 18건이, 평가셋 154건 중 83건(53.9%)이
      // 일반 채팅으로 샜다 — "지역별로 묶어서 합계 내줘", "피벗으로 요약해줘",
      // "이름순으로 정렬해줘"가 전부 구현된 액션인데 입구에서 막혔다. 그리고
      // 새면 시스템 프롬프트 없는 모델을 만나 엉뚱한 답이 돌아왔다.
      //
      // 이제는 **워크북이 열려 있으면 엑셀 경로가 기본**이고, "엑셀 일이 아니다"는
      // 판정은 사이드카가 응답 본문으로 돌려준다(excel_live.not_excel_request).
      // 단어 목록을 늘리는 방식은 같은 결함을 미룰 뿐이라 버렸다.
      let workbookAvailable = false;
      try {
        const status = await excelLiveStatus();
        workbookAvailable = Boolean(
          status?.available && Array.isArray(status?.workbooks) && status.workbooks.length > 0
        );
      } catch {
        // 상태를 모르면 엑셀로 보내지 않는다 — 사이드카가 죽었을 때 모든 문장이
        // 실패로 끝나는 것보다 일반 채팅으로 답하는 편이 낫다.
      }
      const routeToExcel = decideExcelRoute({
        message: trimmed,
        wasExcelFollowUp,
        workbookAvailable,
      });

      // 엑셀 경로로 보냈는데 사이드카가 "엑셀 일이 아니다"라고 판정하면 이 깃발이
      // 서고, 아래에서 일반 채팅으로 다시 태운다.
      let routedToChatFallback = false;
      // 이 전송 한 건을 묶는 id — 사이드카 로그에서 조각(N개 줄)과 재시도를 원문에 잇는 열쇠.
      const clientRequestId = `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      if (!routeToExcel) {
        // 엑셀로 안 보낸 결정도 기록한다 — "엑셀이 왜 안 잡혔나"를 사후에 답하려면 이게 있어야 한다.
        traceClientEvent({
          kind: "route_chat",
          session_id: excelSessionKey(),
          message: displayText,
          detail: { why: workbookAvailable ? "키워드·후속 판정 없음" : "열린 통합문서 없음/상태 조회 실패", workbook_available: workbookAvailable, was_excel_follow_up: wasExcelFollowUp, client_request_id: clientRequestId },
        });
      }
      if (routeToExcel) {
        const rangeRef = extractExcelRangeTag(trimmed);
        const cleanedInput = stripExcelContextBlock(trimmed);
        const commands = splitExcelCompositeCommand(cleanedInput)
          .map((cmd) => applyRangeContextToCommand(cmd, rangeRef));
        for (let i = 0; i < commands.length; i += 1) {
          const cmd = commands[i];
          const contextRangeForCmd = hasExplicitRangeInCommand(cmd) ? null : lastExcelRangeRef;
          // 사이드카 로그 전용 문맥 — 사용자가 친 원문(마크업 포함), 조각 번호, 붙여넣기 범위,
          // 직전 결과 주소, 후속 판정. 사이드카 판단에는 쓰이지 않는다(2026-08-19 로그 감사).
          const clientContext = {
            raw_message: trimmed,
            display_text: displayText,
            part_index: i + 1,
            part_count: commands.length,
            paste_ref: rangeRef,
            last_excel_range_ref: lastExcelRangeRef,
            was_excel_follow_up: wasExcelFollowUp,
            client_request_id: clientRequestId,
          };
          setPendingTaskLabel(`엑셀 명령 ${i + 1}/${commands.length} 실행 중...`);
          const excelResult = await runWithRetry(
            () => excelLiveCommand(cmd, null, null, excelSessionKey(), false, contextRangeForCmd, clientContext),
            "엑셀 명령",
            // 워크북을 편집할 수 있다. 두 번 실행되면 값이 두 번 들어간다.
            { repeatable: false, timeoutMs: EXCEL_REQUEST_TIMEOUT_MS }
          );
          // 사이드카가 "이건 엑셀 일이 아니다"라고 판정했다. 라우팅 기본값이
          // 엑셀이라 잡담·업무 외 질의도 일단 여기로 오는데, 그걸 엑셀 오류로
          // 보여 주면 안 된다 — 일반 채팅으로 넘겨 정상적으로 답하게 한다.
          if (isChatFallbackResponse(excelResult)) {
            routedToChatFallback = true;
            traceClientEvent({
              kind: "route_chat",
              session_id: excelSessionKey(),
              message: displayText,
              detail: { why: "사이드카가 업무 외로 판정 → 일반 채팅으로", client_request_id: clientRequestId },
            });
            break;
          }
          // 자해·고통 호소. 엑셀 되묻기로 받지 않고 사이드카가 만든 문구를 그대로 쓴다.
          if (isSafetyStopResponse(excelResult)) {
            const safetyText = String(excelResult?.reason || "").trim();
            addAgentMessage({
              role: "agent",
              text: safetyText,
              sourceRoute: "/excel-live/command",
            });
            if (activeSessionId) persistMessageSilent(activeSessionId, "agent", safetyText);
            break;
          }
          // 사이드카가 "이건 한 번에 못 한다"고 판단해 하위 명령으로 펼쳐 보냈다.
          // 승인은 카드에서 받으므로 여기서는 계획만 올리고 이 턴을 끝낸다.
          if (excelResult?.action === "excel_live.macro_plan") {
            excelFollowUpRef.current = true;
            startMacroPlan(excelResult.result || {}, cmd);
            const planText = String(
              excelResult?.reason || "작업을 여러 단계로 나눴습니다. 확인 후 실행해 주세요."
            );
            addAgentMessage({ role: "agent", text: planText, sourceRoute: "/excel-live/command" });
            if (activeSessionId) persistMessageSilent(activeSessionId, "agent", planText);
            break;
          }
          if (excelResult?.result?.ask_follow_up) {
            excelFollowUpRef.current = true;
            const followText = String(
              excelResult?.result?.follow_up_question ||
                excelResult?.reason ||
                "작업을 진행하려면 추가 정보가 필요합니다."
            ).trim();
            addAgentMessage({ role: "agent", text: followText, sourceRoute: "/excel-live/command" });
            if (activeSessionId) persistMessageSilent(activeSessionId, "agent", followText);
            break;
          }
          if (excelResult?.approval_required && excelResult?.pending_approval) {
            excelFollowUpRef.current = true;
            setPendingExcelApproval(excelResult.pending_approval);
            setPendingExcelComposite({
              commands,
              currentIndex: i,
            });
            addAgentMessage({
              role: "agent",
              sourceRoute: "/excel-live/command",
              text:
                commands.length > 1
                  ? `복합 작업 ${i + 1}/${commands.length} 단계는 승인 후 실행됩니다.`
                  : "엑셀 변경 작업은 승인 후 실행됩니다.",
            });
            break;
          }
          // 실패를 성공으로 보고하지 않는다. 남은 단계도 멈춘다 — 앞 단계가 실패한 채
          // 뒤를 계속 밀면 사용자가 의도하지 않은 상태로 통합문서가 흘러간다.
          if (excelResult?.ok === false) {
            excelFollowUpRef.current = true;
            const failure = formatExcelLiveFailure(excelResult);
            const failText =
              commands.length > 1 ? `[${i + 1}/${commands.length}] ${failure}` : failure;
            addAgentMessage({ role: "agent", text: failText, sourceRoute: "/excel-live/command" });
            if (activeSessionId) persistMessageSilent(activeSessionId, "agent", failText);
            break;
          }

          const addr = String(excelResult?.result?.address || "").toUpperCase();
          if (addr) setLastExcelRangeRef(addr);
          noteExcelTargetFromResult(excelResult);

          const answer = formatExcelLiveResult(excelResult?.action, excelResult?.result, excelResult?.reason);
          const text = commands.length > 1 ? `[${i + 1}/${commands.length}] ${answer}` : answer;
          addAgentMessage({ role: "agent", text, sourceRoute: "/excel-live/command" });
          if (activeSessionId) persistMessageSilent(activeSessionId, "agent", text);
        }

        if (!routedToChatFallback && activeSessionId && pendingUserMsgRef.current) {
          persistMessageSilent(activeSessionId, "user", pendingUserMsgRef.current);
          pendingUserMsgRef.current = null;
        }
      }

      // 엑셀로 보냈지만 사이드카가 "엑셀 일이 아니다"라고 돌려준 경우에도 여기로 온다.
      if (!routeToExcel || routedToChatFallback) {
        setPendingTaskLabel("AI가 답변을 생성하는 중...");
        const result = await runWithRetry(
          () => agentChat(trimmed, activeSessionId),
          "AI 대화",
          // 대화는 워크북을 건드리지 않는다. 두 번 물어도 손해가 없다.
          { repeatable: true, timeoutMs: CHAT_REQUEST_TIMEOUT_MS }
        );
        const newSessionId = result.session_id;
        addAgentMessage({
          role: "agent",
          text: result.response,
          toolCalls: result.tool_calls,
          maskedCount: result.masked_count,
          maskedTypes: result.masked_types,
          sourceRoute: "/agent/chat",
        });
        if (newSessionId) setActiveSessionId(newSessionId);

        // 영속화 — 세션 ID가 새로 발급된 경우 user 메시지도 함께 저장
        const sid = newSessionId || activeSessionId;
        if (sid) {
          if (pendingUserMsgRef.current) {
            persistMessageSilent(sid, "user", pendingUserMsgRef.current);
            pendingUserMsgRef.current = null;
          }
          persistMessageSilent(sid, "agent", result.response, {
            toolCalls: result.tool_calls,
            maskedCount: result.masked_count,
            maskedTypes: result.masked_types,
          });
        }
      }
    } catch (err) {
      const fallbackMsg = "작업 처리 중 오류가 발생했습니다. 다시 시도해 주세요.";
      const rawErr = String(err?.message ?? err ?? "");
      const errText = toUserMessage(rawErr, fallbackMsg);
      // 사용자가 실제로 본 오류 문구와 원문 오류를 사이드카 로그에도 — "로그는 성공, 화면은 실패"를 잡는 유일한 길.
      traceClientEvent({
        kind: /timeout/i.test(rawErr) ? "timeout" : "ui_error",
        session_id: excelSessionKey(),
        message: trimmed,
        detail: { error_text: errText, raw_error: rawErr.slice(0, 400), why: rawErr.slice(0, 120) },
      });
      addAgentMessage({
        role: "agent",
        text: null,
        error: errText === fallbackMsg ? `${errText} (${rawErr.slice(0, 120)})` : errText,
      });
      const sid = activeSessionId;
      if (sid) {
        persistMessageSilent(sid, "agent", "", { errorText: errText });
      }
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
      // 승인 뒤 실행이 실패하는 경로가 실제로 있다(검증 실패 → 보정 → 재계획 → 실패).
      // ok를 안 보면 그 턴이 "완료되었습니다"로 끝나 사용자가 파일이 바뀐 줄 안다.
      const approvalStepText =
        out?.ok === false
          ? formatExcelLiveFailure(out)
          : formatExcelLiveResult(out?.action, out?.result, out?.reason);
      const isComposite = !!pendingExcelComposite?.commands?.length;
      const approvalStepLabel = isComposite
        ? `[${pendingExcelComposite.currentIndex + 1}/${pendingExcelComposite.commands.length}] ${approvalStepText}`
        : approvalStepText;
      addAgentMessage({
        role: "agent",
        sourceRoute: "/excel-live/command",
        text: approvalStepLabel,
      });
      if (activeSessionId) {
        persistMessageSilent(activeSessionId, "agent", approvalStepLabel);
      }

      // 복합 명령의 승인 단계였다면 남은 단계를 이어서 실행한다.
      if (
        pendingExcelComposite?.commands?.length &&
        pendingExcelComposite.currentIndex + 1 < pendingExcelComposite.commands.length
      ) {
        const { commands } = pendingExcelComposite;
        for (let i = pendingExcelComposite.currentIndex + 1; i < commands.length; i += 1) {
          const contextRangeForCmd = hasExplicitRangeInCommand(commands[i]) ? null : lastExcelRangeRef;
          setPendingTaskLabel(`엑셀 명령 ${i + 1}/${commands.length} 실행 중...`);
          const excelResult = await runWithRetry(
            () => excelLiveCommand(commands[i], null, null, excelSessionKey(), false, contextRangeForCmd, {
              part_index: i + 1,
              part_count: commands.length,
              last_excel_range_ref: lastExcelRangeRef,
              continued_after_approval: true,
            }),
            "엑셀 명령",
            { repeatable: false, timeoutMs: EXCEL_REQUEST_TIMEOUT_MS }
          );
          if (excelResult?.result?.ask_follow_up) {
            excelFollowUpRef.current = true;
            const followText = String(
              excelResult?.result?.follow_up_question ||
                excelResult?.reason ||
                "작업을 진행하려면 추가 정보가 필요합니다."
            ).trim();
            addAgentMessage({ role: "agent", text: followText, sourceRoute: "/excel-live/command" });
            if (activeSessionId) {
              persistMessageSilent(activeSessionId, "agent", followText);
            }
            return;
          }
          if (excelResult?.approval_required && excelResult?.pending_approval) {
            excelFollowUpRef.current = true;
            setPendingExcelApproval(excelResult.pending_approval);
            setPendingExcelComposite({ commands, currentIndex: i });
            addAgentMessage({
              role: "agent",
              sourceRoute: "/excel-live/command",
              text: `복합 작업 ${i + 1}/${commands.length} 단계는 승인 후 실행됩니다.`,
            });
            hasNextPendingApproval = true;
            return;
          }
          if (excelResult?.ok === false) {
            const failText = `[${i + 1}/${commands.length}] ${formatExcelLiveFailure(excelResult)}`;
            addAgentMessage({ role: "agent", text: failText, sourceRoute: "/excel-live/command" });
            if (activeSessionId) {
              persistMessageSilent(activeSessionId, "agent", failText);
            }
            return;
          }
          const addr = String(excelResult?.result?.address || "").toUpperCase();
          if (addr) setLastExcelRangeRef(addr);
          const text = `[${i + 1}/${commands.length}] ${formatExcelLiveResult(
            excelResult?.action,
            excelResult?.result,
            excelResult?.reason,
          )}`;
          addAgentMessage({ role: "agent", text, sourceRoute: "/excel-live/command" });
          if (activeSessionId) {
            persistMessageSilent(activeSessionId, "agent", text);
          }
        }
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
        setPendingExcelComposite(null);
      }
      setPendingTaskLabel("");
    }
  }, [pendingExcelApproval, pendingExcelComposite, addAgentMessage, activeSessionId, lastExcelRangeRef, runWithRetry]);

  const handleExcelApprovalCancel = useCallback(async () => {
    if (!pendingExcelApproval) return;
    setExcelApprovalBusy(true);
    try {
      await excelLiveSubmitApproval(pendingExcelApproval.approval_id, false, "사용자 거부");
      // 취소했다고 대화가 끝난 게 아니다. "다시 제안해줄래?"가 곧바로 온다.
      excelFollowUpRef.current = true;
      addAgentMessage({
        role: "system",
        text: "엑셀 작업 실행을 취소했습니다.",
      });
    } catch {
      // ignore
    } finally {
      setExcelApprovalBusy(false);
      setPendingExcelApproval(null);
      setPendingExcelComposite(null);
      setPendingTaskLabel("");
    }
  }, [pendingExcelApproval, addAgentMessage]);

  const handleSaveWorkbook = useCallback(async () => {
    if (loading || excelSaving || isUnavailable) return;
    setExcelSaving(true);
    try {
      const out = await excelLiveSaveWorkbook(null);
      const text = formatExcelLiveResult(out?.action, out?.result, out?.reason);
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

  const handleRequestRestoreWorkbook = useCallback(async () => {
    if (loading || excelSaving || excelRestoreBusy || isUnavailable) return;
    setExcelRestoreBusy(true);
    try {
      const listed = await excelLiveListBackups(null, 10);
      const backups = Array.isArray(listed?.backups) ? listed.backups : [];
      if (backups.length === 0) {
        addAgentMessage({
          role: "system",
          text: "복구 가능한 백업이 없습니다. 먼저 편집 작업을 실행해 백업을 생성해 주세요.",
        });
        return;
      }
      const latest = backups[0];
      setPendingExcelRestore({
        workbookId: listed?.workbook_id || null,
        backupPath: latest.backup_path || null,
        backupName: latest.backup_name || latest.backup_path || "(이름 없음)",
        modifiedAt: latest.modified_at || "",
      });
    } catch (err) {
      addAgentMessage({
        role: "agent",
        error: toUserMessage(err, "복구용 백업 목록 조회에 실패했습니다."),
      });
    } finally {
      setExcelRestoreBusy(false);
    }
  }, [loading, excelSaving, excelRestoreBusy, isUnavailable, addAgentMessage]);

  const handleRestoreWorkbookConfirm = useCallback(async () => {
    if (!pendingExcelRestore) return;
    setExcelRestoreBusy(true);
    try {
      const out = await excelLiveRestoreLastBackup(
        pendingExcelRestore.workbookId,
        pendingExcelRestore.backupPath,
      );
      const text = formatExcelLiveResult(out?.action, out?.result, out?.reason);
      addAgentMessage({ role: "system", text });
      if (activeSessionId) {
        persistMessageSilent(activeSessionId, "system", text);
      }
    } catch (err) {
      addAgentMessage({
        role: "agent",
        error: toUserMessage(err, "엑셀 복구 중 오류가 발생했습니다. 다시 시도해 주세요."),
      });
    } finally {
      setExcelRestoreBusy(false);
      setPendingExcelRestore(null);
    }
  }, [pendingExcelRestore, addAgentMessage, activeSessionId, persistMessageSilent]);

  const handleRestoreWorkbookCancel = useCallback(() => {
    if (excelRestoreBusy) return;
    setPendingExcelRestore(null);
  }, [excelRestoreBusy]);

  const handleInsertExcelRangeContext = useCallback(async () => {
    if (loading || isUnavailable || insertingRangeContext) return;
    setInsertingRangeContext(true);
    try {
      const out = await excelLiveCommand("지금 선택한 범위 읽어줘", null, null, excelSessionKey(), false);
      const result = out?.result || {};
      const address = String(result.address || "").toUpperCase();
      if (!address) {
        throw new Error("선택 범위 주소를 가져오지 못했습니다.");
      }
      setLastExcelRangeRef(address);
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
  }, [loading, isUnavailable, insertingRangeContext, addAgentMessage, activeSessionId]);

  // Excel에서 복사한 셀을 붙여넣으면 범위 참조로 바꾼다.
  //
  // 사람은 좌표를 타이핑하지 않는다 — 표를 긁어서 붙인다. 예전에는 그 탭투성이
  // 텍스트가 그대로 명령이 돼 읽힐 리가 없었다. Ctrl+C 한 순간 그 범위가 곧 현재
  // 선택이므로, Excel에게 주소만 물어 바꿔 준다. 값은 백엔드가 워크북에서 직접 읽는다.
  const handlePaste = useCallback(
    async (event) => {
      const pasted = event.clipboardData?.getData("text/plain") ?? "";
      // 평범한 붙여넣기는 건드리지 않는다. 단 **빈 범위**를 복사하면 Excel이 클립보드에
      // `\r\n`만 넣으므로(2026-08-25 실측) 공백뿐인 붙여넣기도 선택 주소를 물어본다.
      if (!isExcelSelectionPaste(pasted)) return;

      event.preventDefault();
      // 주소 조회는 전용 경량 엔드포인트로 한다. 예전엔 전체 명령 파이프라인
      // ("지금 선택한 범위 읽어줘")을 탔는데, LLM이 바쁘면 수십 초가 걸리고
      // 사이드카 재시작 창과 겹치면 통째로 실패했다(2026-08-17 실측 — 사용자가
      // "복사했는데 뭐가 안 올라가네"를 봤다).
      let address = "";
      let selectionEmpty = null;
      try {
        const out = await excelLiveSelection();
        address = String(out?.address || "").toUpperCase();
        selectionEmpty = typeof out?.empty === "boolean" ? out.empty : null;
      } catch {
        // Excel이 꺼져 있거나 다른 앱에서 복사한 경우다. 아래에서 처리한다.
      }
      if (address) setLastExcelRangeRef(address);
      if (!address && !pasted.trim()) {
        // 빈 셀 복사 + 주소 실패: 넣을 것이 공백뿐이다. 조용히 아무것도 안 넣으면
        // 사용자는 앱이 고장 났다고 생각한다 — 이유를 말한다.
        addAgentMessage({
          role: "system",
          text: "엑셀 선택 범위를 읽지 못했습니다. Excel 연결을 확인하고 다시 복사해 주세요.",
        });
        return;
      }
      // 같은 통합문서에서 복사했으면 값은 그 범위에 이미 있으니 주소만 남긴다.
      // 선택 영역은 비어 있는데 붙여넣은 표에는 값이 있다 = 다른 앱·통합문서에서
      // 가져온 데이터다 — 값을 살려 보내야 "입력해줘"가 그 자리에 쓴다
      // (2026-08-19: 전에는 값이 통째로 사라져 "복붙한 값이 안 들어간다"가 됐다).
      const keepValues = selectionEmpty === true && pasteHasValues(pasted);
      // 붙여넣기 사고("복붙했는데 값이 안 들어간다")는 이 세 값이 있어야 재현된다.
      traceClientEvent({
        kind: "paste_probe",
        session_id: excelSessionKey(),
        detail: { address: address || "(없음)", selection_empty: selectionEmpty, keep_values: keepValues, pasted_chars: pasted.length, why: address ? "선택 주소 인식" : "선택 주소 없음" },
      });
      const block = buildPasteBlock(pasted, address, { keepValues });
      setInput((prev) => {
        if (!prev) return block;
        return prev.endsWith("\n") ? `${prev}${block}` : `${prev}\n${block}`;
      });
    },
    [excelSessionKey, addAgentMessage]
  );

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
    pendingUserMsgRef.current = null;
    // 새 대화 시작 후 세션 목록 갱신 예약
    setTimeout(() => refreshSessions(), 100);
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-2">
      {/* 헤더 — 좁으면(≈280~470px) 버튼 줄이 제목 아래로 내려가고, 그 안에서도 줄바꿈된다.
          예전엔 nowrap이라 폭 280px에서 버튼 5개가 제목 위로 겹쳐 그려졌다. */}
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <div className="flex shrink-0 items-center gap-2 whitespace-nowrap text-sm font-semibold">
          <Bot className="h-4 w-4 text-primary" />
          앱 내 에이전트
        </div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1">
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
            disabled={isUnavailable || loading || excelSaving || excelRestoreBusy}
            title="현재 열려 있는 엑셀 파일 저장"
          >
            <Save className="h-3 w-3" />
            {excelSaving ? "저장 중..." : "엑셀 저장"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={handleRequestRestoreWorkbook}
            disabled={isUnavailable || loading || excelSaving || excelRestoreBusy}
            title="최근 백업으로 마지막 변경 되돌리기"
          >
            <RotateCcw className="h-3 w-3" />
            {excelRestoreBusy ? "복구 준비..." : "되돌리기"}
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

      {/* 지금 무엇을 편집 중인지 보여 준다 — 어긋나면 여기서 바로 드러난다. */}
      <ExcelTargetBar target={excelTarget} />

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
        title={
          pendingExcelApproval?.interpretation
            ? "이렇게 이해했어요 — 맞나요?"
            : pendingExcelApproval?.tool_display_name || "엑셀 작업 승인"
        }
        description={
          pendingExcelApproval
            ? pendingExcelApproval.interpretation
              ? pendingExcelApproval.summary
              : `${pendingExcelApproval.summary}\n\n정말 실행하시겠습니까?`
            : ""
        }
        cancelLabel={pendingExcelApproval?.interpretation ? "아니에요" : "취소"}
        confirmLabel={
          excelApprovalBusy
            ? "처리 중..."
            : pendingExcelApproval?.interpretation
              ? "맞아요, 실행"
              : "승인 후 실행"
        }
        confirmVariant="default"
        // 배경 클릭·Escape가 취소로 이어지면, 팝업 뒤 파일 목록을 무심코 누른 것만으로
        // 승인 대기 계획이 버려진다(2026-08-18 실측). 버튼으로만 답하게 한다.
        requireExplicitChoice
        onConfirm={handleExcelApprovalConfirm}
        onCancel={handleExcelApprovalCancel}
      />
      <AlertDialog
        open={!!pendingExcelRestore}
        title="마지막 변경 되돌리기"
        description={
          pendingExcelRestore
            ? `가장 최근 백업으로 복구합니다.\n\n백업: ${pendingExcelRestore.backupName}\n시각: ${pendingExcelRestore.modifiedAt || "알 수 없음"}\n\n복구 직전 상태는 pre_restore 백업으로 한 번 더 저장됩니다.`
            : ""
        }
        confirmLabel={excelRestoreBusy ? "복구 중..." : "복구 실행"}
        confirmVariant="destructive"
        onConfirm={handleRestoreWorkbookConfirm}
        onCancel={handleRestoreWorkbookCancel}
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
              const feedbackKey = buildFeedbackKey(idx, msg);
              const feedbackValue = messageFeedback[feedbackKey];
              const feedbackBusy = feedbackBusyKey === feedbackKey;
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
                  {msg.role === "agent" && msg.text && !msg.error && (
                    <div className="mt-1 flex items-center gap-1.5 text-[11px]">
                      <button
                        type="button"
                        className={cn(
                          "inline-flex items-center gap-1 rounded border px-2 py-0.5 transition-colors",
                          feedbackValue === "good"
                            ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300"
                            : "border-border bg-background text-muted-foreground hover:bg-muted"
                        )}
                        disabled={feedbackBusy}
                        onClick={() => handleMessageFeedback(idx, "good")}
                        title="좋은 응답으로 피드백 보내기"
                      >
                        <ThumbsUp className="h-3 w-3" />
                        좋아요
                      </button>
                      <button
                        type="button"
                        className={cn(
                          "inline-flex items-center gap-1 rounded border px-2 py-0.5 transition-colors",
                          feedbackValue === "bad"
                            ? "border-amber-400 bg-amber-50 text-amber-700 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-300"
                            : "border-border bg-background text-muted-foreground hover:bg-muted"
                        )}
                        disabled={feedbackBusy}
                        onClick={() => handleMessageFeedback(idx, "bad")}
                        title="아쉬운 응답으로 피드백 보내기"
                      >
                        <ThumbsDown className="h-3 w-3" />
                        아쉬워요
                      </button>
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

      {/* 매크로 계획·진행·실패 카드 */}
      <ExcelMacroCard />

      {/* 입력 영역 */}
      {isUnavailable ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          앱 서버에 연결할 수 없어 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.
        </div>
      ) : (
        <div className="flex min-w-0 items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder="에이전트에게 메시지... (엑셀 셀을 붙여넣어도 됩니다)"
            disabled={loading}
            className="flex-1 min-w-0 resize-none rounded-md border border-input bg-background px-3 py-1.5 text-xs leading-[18px] placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 max-h-[110px] overflow-y-auto"
          />
          <Button size="sm" className="shrink-0" onClick={handleSend} disabled={!input.trim() || loading}>
            <SendIcon className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}

// ── 메인 WorkspacePage ──────────────────────────────────────────────────────

export default function WorkspacePage() {
  // 채팅·엑셀 명령은 둘 다 사이드카를 거친다. appStore.sidecarStatus는 StatusBar가
  // 헬스 체크로 계속 갱신하므로 여기서 따로 fetch하지 않고 구독만 한다.
  const sidecarState = useAppStore((s) => s.sidecarStatus?.state);
  const workspacePath = useAppStore((s) => s.workspacePath);
  const setWorkspacePath = useAppStore((s) => s.setWorkspacePath);
  const [currentPath, setCurrentPath] = useState("");
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [previewFile, setPreviewFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [creatingExcel, setCreatingExcel] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [botUsername] = useState(null); // 텔레그램 제거(dev 병합)로 항상 null — 딥링크 UI는 자연히 숨는다

  // 채팅 사이드 패널 상태 — localStorage persist
  // 우측 ChatPanel(도킹/플로팅)이 떠 있으면 인라인 채팅은 숨긴다 — 같은 대화가
  // 두 군데 렌더돼 "대화 창이 두 개 중복"으로 보였다(2026-09-01 사용자 실측).
  const chatPanelOpen = useChatStore((st) => st.panelOpen);
  const [chatOpen, setChatOpen] = useState(() => {
    try {
      const v = localStorage.getItem(LS_CHAT_OPEN);
      return v == null ? true : v === "true";
    } catch {
      return true;
    }
  });
  // 저장하는 건 "선호 폭"이다. 실제로 그리는 폭은 아래에서 컨테이너 폭으로 한 번 더
  // 깎는다(appliedChatWidth) — 창을 줄였다 다시 넓히면 선호 폭으로 돌아와야 한다.
  const [chatWidth, setChatWidth] = useState(() => {
    try {
      return readStoredChatWidth(localStorage.getItem(LS_CHAT_WIDTH));
    } catch {
      return readStoredChatWidth(null);
    }
  });

  // 컨테이너(이 페이지 루트) 폭을 재서 배치를 정한다. window 폭이 아니라 컨테이너
  // 폭인 이유: 좌측 앱 사이드바를 접고 펴면 창은 그대로인데 가용 폭이 바뀐다.
  const containerRef = useRef(null);
  const containerWidth = useElementWidth(containerRef);
  const layoutMode = resolveWorkspaceLayout(containerWidth);
  const stacked = layoutMode === "stacked";
  const appliedChatWidth = clampChatWidth(chatWidth, containerWidth);

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
      // 패널의 오른쪽 끝은 컨테이너의 오른쪽 끝이다(창 끝이 아니다 — 페이지 여백 24px).
      // 상한도 저장 폭과 같은 규칙(clampChatWidth)을 써서, 드래그로도 파일 목록을
      // 420px 아래로 못 밀게 한다.
      const rect = containerRef.current?.getBoundingClientRect();
      const rightEdge = rect ? rect.right : window.innerWidth;
      const width = rect ? rect.width : window.innerWidth;
      setChatWidth(clampChatWidth(rightEdge - e.clientX, width));
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
      if (data.workspace) setWorkspacePath(data.workspace);
    } catch (err) {
      setError(toUserMessage(err, "파일 목록을 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, [setWorkspacePath]);

  useEffect(() => {
    loadFiles(currentPath);
  }, [currentPath, loadFiles]);

  // 창이 다시 앞으로 오면 목록을 다시 읽는다. 탐색기·다른 도구로 폴더를 정리해도
  // 화면은 옛 목록을 들고 있어 "안 지워졌다"로 보였다(2026-08-19 스크린샷 —
  // 디스크에는 9개, 화면에는 30개). 새로고침 버튼을 누르지 않아도 맞춰 준다.
  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") loadFiles(currentPath);
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [currentPath, loadFiles]);

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
    const runWithRetry = async (task) => {
      let lastError = null;
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          return await task();
        } catch (err) {
          lastError = err;
          if (attempt >= 3 || !isTransientUploadError(err)) {
            throw err;
          }
          await sleep(350 * attempt);
        }
      }
      throw lastError || new Error("업로드 재시도 중 알 수 없는 오류가 발생했습니다.");
    };

    if (isText) {
      try {
        const text = await file.text();
        await runWithRetry(() => workspaceWriteFile(targetPath, text));
      } catch (err) {
        throw new Error(`${file.name} — 업로드 실패: ${toUploadUserMessage(err)}`);
      }
      return;
    }

    // 바이너리 — Tauri command `workspace_write_file_binary` 정식 path만 사용.
    // 실패 시 사용자에게 명확하게 안내하고 그 외 fallback은 하지 않는다.
    const buf = await file.arrayBuffer();
    const b64 = arrayBufferToBase64(buf);
    try {
      await runWithRetry(() => workspaceWriteFileBinary(targetPath, b64));
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
        const isWindows = typeof navigator !== "undefined" && /win/i.test(String(navigator.platform || ""));
        const restartHint = isDev
          ? (isWindows
              ? "개발 모드에서는 npm run tauri:dev를 다시 실행해 주세요"
              : "개발 모드에서는 ./dev.sh를 다시 실행해 주세요")
          : "앱을 완전히 종료한 뒤 다시 실행해 주세요";
        throw new Error(`${file.name} — 이 형식의 파일을 처리하려면 앱을 다시 시작해 주세요. (${restartHint}. 임시 우회: "폴더 열기"로 직접 복사)`);
      }
      throw new Error(`${file.name} — 업로드 실패: ${msg}`);
    }
  }, []);

  const handleFileSelected = useCallback(async (e) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    const selectedFiles = Array.from(fileList);
    const skippedLockFiles = selectedFiles.filter((file) => isOfficeLockTempFile(file?.name));
    const files = selectedFiles.filter((file) => !isOfficeLockTempFile(file?.name));

    if (files.length === 0) {
      e.target.value = "";
      setUploadProgress(null);
      setUploadMessage(
        skippedLockFiles.length > 0
          ? `${skippedLockFiles.length}개 건너뜀 — 엑셀 임시 잠금 파일(~$...)은 업로드하지 않습니다.`
          : "업로드 가능한 파일이 없습니다."
      );
      await loadFiles(currentPath);
      return;
    }

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
        console.error("[workspace-upload] file upload failed:", file.name, err);
        failures.push(toUploadUserMessage(err));
      }
    }

    setUploadProgress(null);
    setUploading(false);
    e.target.value = "";

    const success = files.length - failures.length;
    const parts = [];
    if (success > 0) parts.push(`${success}개 업로드 완료`);
    if (failures.length > 0) parts.push(`${failures.length}개 실패 — ${failures[0]}${failures.length > 1 ? ` 외 ${failures.length - 1}건` : ""}`);
    if (skippedLockFiles.length > 0) parts.push(`${skippedLockFiles.length}개 건너뜀 (엑셀 임시 잠금 파일)`);
    setUploadMessage(parts.join(" · ") || "업로드 결과 없음");

    await loadFiles(currentPath);
  }, [currentPath, loadFiles, uploadOneFile]);

  const uploadLocation = workspacePath
    ? `${workspacePath}${currentPath ? `/${currentPath}` : ""}`
    : "확인 중...";

  return (
    <div
      ref={containerRef}
      // 760px 미만이면 세로 쌓기(flex-col). 그 이상이면 좌우.
      className={cn("flex h-full min-w-0", stacked && "flex-col")}
      data-layout={layoutMode}
    >
      {/* 메인 영역 */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0 gap-4">
        {/* 툴바 — 제목 블록은 240px부터 시작해 남는 폭을 먹고(경로는 말줄임),
            버튼 묶음이 안 들어가면 다음 줄로 내려가며 그 안에서도 줄바꿈된다. */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="min-w-0 grow basis-60">
            <h1 className="text-lg font-semibold whitespace-nowrap">워크스페이스</h1>
            <Breadcrumb currentPath={currentPath} onNavigate={handleNavigate} />
            <p className="mt-1 text-xs text-muted-foreground truncate" title={uploadLocation}>
              업로드 위치: {uploadLocation}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
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
        <div className="flex flex-1 gap-4 min-h-0 min-w-0">
          <Card className="flex-1 min-w-0 overflow-auto">
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
                  compact={!stacked && chatOpen && containerWidth - appliedChatWidth < 560}
                />
              )}
            </CardContent>
          </Card>

          {/* 파일 미리보기 패널 — 파일 목록을 절반 아래로 밀지 않는다 */}
          {previewFile && (
            <div className="w-80 max-w-[45%] min-w-0 shrink-0 flex flex-col">
              <FilePreview file={previewFile} onClose={() => setPreviewFile(null)} />
            </div>
          )}
        </div>
      </div>

      {/* resize handle + 채팅 사이드 패널.
          <aside>는 두 배치에서 같은 자리(프래그먼트의 두 번째 자식)에 두어 배치가 바뀌어도
          ChatSidePanel이 다시 마운트되지 않게 한다 — 입력 중인 글·승인 대기가 살아남아야 한다. */}
      {chatOpen && !chatPanelOpen && (
        <>
          {!stacked && (
            <div
              role="separator"
              aria-orientation="vertical"
              onMouseDown={() => {
                draggingRef.current = true;
                document.body.style.cursor = "col-resize";
              }}
              className="ml-3 mr-1 w-1 shrink-0 cursor-col-resize bg-transparent hover:bg-primary/40 transition-colors"
            />
          )}
          <aside
            className={cn(
              "flex shrink-0 flex-col min-w-0 min-h-0 border-border",
              stacked ? "mt-4 border-t pt-3" : "border-l pl-3",
            )}
            style={
              stacked
                ? {
                    // 아래쪽 45%, 최소 260px. 나머지는 위 파일 목록이 쓴다.
                    flexBasis: `${Math.round(STACKED_CHAT_HEIGHT_RATIO * 100)}%`,
                    minHeight: `${STACKED_CHAT_MIN_HEIGHT}px`,
                  }
                : { width: `${appliedChatWidth}px` }
            }
            aria-label="에이전트 채팅"
          >
            <ChatSidePanel sidecarState={sidecarState} />
          </aside>
        </>
      )}
    </div>
  );
}
