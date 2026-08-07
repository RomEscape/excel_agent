/**
 * WorkspacePage — 워크스페이스 파일 탐색기.
 *
 *   - 파일 목록 / 탐색 / 업로드 / 새 엑셀 파일 생성 / 텍스트 미리보기
 *   - 파일 row hover 액션: "텔레그램으로 명령 예시 보내기" — 템플릿 클립보드 복사 + 봇 딥링크
 *
 * 에이전트 채팅은 더 이상 여기 붙어 있지 않다. 채팅이 앱의 주 작업면이 되면서
 * 독립 페이지(components/chat/ChatPage.jsx)로 승격됐고, 그 로직은
 * lib/chatManager.js + store/chatStore.js가 소유한다.
 *
 * 본 페이지는 full-bleed (max-width 적용 안 함) — 데스크탑 와이드스크린 활용.
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Folder,
  File,
  ChevronRight,
  Home,
  RefreshCw,
  Upload,
  Eye,
  X,
  FolderOpen,
  FileSpreadsheet,
  Copy,
  Check,
  MessageCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import EmptyState from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import { toUserMessage } from "@/lib/errorMessages";
import useAppStore from "@/store/appStore";
import {
  workspaceListFiles,
  workspaceReadFile,
  workspaceWriteFile,
  workspaceCreateExcelFile,
  workspaceWriteFileBinary,
  openWorkspaceFolder,
  openWorkspaceFile,
  telegramStatus,
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

// ── 메인 WorkspacePage ──────────────────────────────────────────────────────

export default function WorkspacePage() {
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
      {/* 파일 영역 — 에이전트 채팅은 별도 페이지(components/chat/ChatPage.jsx)로 분리됐다 */}
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
    </div>
  );
}
