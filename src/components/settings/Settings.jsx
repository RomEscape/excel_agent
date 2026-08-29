import React, { useState, useEffect, useCallback, useRef } from "react";
import { Cpu, Save, Loader2, CheckCircle2, FolderOpen, Trash2, Info, ChevronDown, ChevronUp, ExternalLink, Monitor, Apple, Archive, Upload, Download, AlertTriangle, Palette } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import AlertDialog from "@/components/ui/dialog";
import OllamaModelPicker from "@/components/settings/OllamaModelPicker";
import ThemePicker from "@/components/settings/ThemePicker";
import useAppStore from "@/store/appStore";
import { getLLMSettings, saveLLMSettings, maintenanceCleanup, backupExport, backupImport } from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";
import packageJson from "../../../package.json";

export default function Settings() {
  const llmConfig = useAppStore((s) => s.llmConfig);
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);

  // LLM settings local state (uncommitted until Save)
  const [model, setModel] = useState(llmConfig.model);
  const [savingLLM, setSavingLLM] = useState(false);
  const [llmMsg, setLLMMsg] = useState("");
  const [llmMsgError, setLLMMsgError] = useState(false);

  // App info / maintenance state
  const [cleaning, setCleaning] = useState(false);
  const [cleanupMsg, setCleanupMsg] = useState("");
  const [cleanupError, setCleanupError] = useState(false);

  // 백업 / 복원 state
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState(null); // { file_path, size_bytes } | null
  const [exportError, setExportError] = useState("");
  const [importing, setImporting] = useState(false);
  const [pendingImportFile, setPendingImportFile] = useState(null); // 선택된 파일 (확인 대기)
  const [importResult, setImportResult] = useState(null); // { restored, warnings } | null
  const [importError, setImportError] = useState("");
  const importInputRef = useRef(null);

  const loadLLM = useCallback(async () => {
    try {
      const cfg = await getLLMSettings();
      if (cfg?.provider) {
        const resolvedModel = cfg.model ?? "qwen3:4b";
        setModel(resolvedModel);

        // Sync Zustand store with the server's authoritative value
        setLLMConfig({ provider: "ollama", model: resolvedModel });
      }
    } catch {
      // Endpoint not yet deployed — use Zustand defaults silently
    }
  }, [setLLMConfig]);

  useEffect(() => {
    loadLLM();
  }, [loadLLM]);

  const handleSaveLLM = async () => {
    setSavingLLM(true);
    setLLMMsg("");
    setLLMMsgError(false);
    try {
      const config = { provider: "ollama", model };
      await saveLLMSettings(config);
      setLLMConfig(config);
      setLLMMsg("저장 완료!");
    } catch (e) {
      setLLMMsg(toUserMessage(e, "AI 설정을 저장하지 못했어요."));
      setLLMMsgError(true);
    } finally {
      setSavingLLM(false);
    }
  };

  const handleCleanup = async () => {
    setCleaning(true);
    setCleanupMsg("");
    setCleanupError(false);
    try {
      const result = await maintenanceCleanup();
      const freedMB = ((result?.freed_bytes ?? 0) / (1024 * 1024)).toFixed(1);
      setCleanupMsg(
        `정리 완료: ${result?.deleted_count ?? 0}개 파일 삭제, ${freedMB} MB 확보`
      );
    } catch (e) {
      setCleanupMsg(toUserMessage(e, "임시 파일 정리에 실패했습니다."));
      setCleanupError(true);
    } finally {
      setCleaning(false);
    }
  };

  const handleOpenDataFolder = async () => {
    try {
      // Use Tauri shell plugin to open the app data directory
      const { open } = await import("@tauri-apps/plugin-shell");
      // The data dir path is managed by the Python sidecar; fall back to a
      // cross-platform user data path using Tauri's path API.
      const { appDataDir } = await import("@tauri-apps/api/path");
      const dir = await appDataDir();
      await open(dir);
    } catch {
      // If the plugin isn't available or the dir doesn't exist, silently ignore
    }
  };

  // ── 백업 / 복원 핸들러 ────────────────────────────────────────────────────
  const handleExport = async () => {
    setExporting(true);
    setExportError("");
    setExportResult(null);
    try {
      const result = await backupExport();
      setExportResult(result);
    } catch (e) {
      setExportError(toUserMessage(e, "백업 파일 생성에 실패했습니다."));
    } finally {
      setExporting(false);
    }
  };

  const handleOpenBackupFolder = async (filePath) => {
    if (!filePath) return;
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      // 파일 경로의 부모 디렉터리만 열기 (Finder/Explorer)
      const lastSep = Math.max(filePath.lastIndexOf("/"), filePath.lastIndexOf("\\"));
      const dir = lastSep > 0 ? filePath.slice(0, lastSep) : filePath;
      await open(dir);
    } catch {
      // 미지원 환경 — 무시
    }
  };

  const handlePickImportFile = () => {
    importInputRef.current?.click();
  };

  const handleImportFileSelected = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    // <input type=file>은 path를 직접 노출하지 않으므로 Tauri에서는 file.path 사용 (v2 webview 확장).
    // 일부 환경에서는 file.path가 없어 graceful fallback 필요.
    const filePath = file.path || file.webkitRelativePath || null;
    if (!filePath) {
      setImportError(
        "브라우저 보안 정책으로 파일 경로를 가져올 수 없습니다. " +
        "데이터 폴더에 백업 파일을 직접 복사한 뒤 다시 시도해 주세요."
      );
      return;
    }
    setImportError("");
    setImportResult(null);
    setPendingImportFile({ name: file.name, path: filePath });
  };

  const handleConfirmImport = async () => {
    const target = pendingImportFile;
    setPendingImportFile(null);
    if (!target) return;
    setImporting(true);
    setImportError("");
    try {
      const result = await backupImport(target.path);
      setImportResult(result);
    } catch (e) {
      setImportError(toUserMessage(e, "백업 복원에 실패했습니다."));
    } finally {
      setImporting(false);
    }
  };

  const formatSize = (bytes) => {
    if (!bytes && bytes !== 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">설정</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          AI 엔진과 앱 환경을 설정해요
        </p>
      </div>

      {/* 화면 테마 — 와이어프레임 라이트/다크 두 벌에 대응 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Palette className="h-4 w-4 text-primary" />
            <CardTitle className="text-base">화면 테마</CardTitle>
          </div>
          <CardDescription>
            앱 전체의 밝기를 정해요. 운영체제 설정을 따르게 두면 시스템이 다크로
            바뀔 때 앱도 같이 바뀌어요.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ThemePicker />
        </CardContent>
      </Card>

      {/* AI Engine settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-primary" />
            <CardTitle className="text-base">AI 엔진 설정</CardTitle>
          </div>
          <CardDescription>
            김대리는 Ollama 로컬 모델로만 동작해요 — 자료가 이 컴퓨터를 떠나지
            않습니다. 사용할 모델만 고르면 돼요.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="llm-model">모델</Label>
            {/* 실제 설치된 모델만 선택 가능 (자유 입력 제거).
                목록·상태는 OllamaModelPicker가 중앙 statusStore에서 구독한다. */}
            <OllamaModelPicker id="llm-model" value={model} onChange={setModel} />
          </div>

          <div className="flex items-center gap-3">
            <Button size="sm" onClick={handleSaveLLM} disabled={savingLLM}>
              {savingLLM ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Save className="mr-1 h-3 w-3" />
              )}
              저장
            </Button>
            {llmMsg && (
              <span
                className={`flex items-center gap-1 text-xs ${
                  llmMsgError ? "text-destructive" : "text-muted-foreground"
                }`}
              >
                {!llmMsgError && llmMsg.includes("완료") && (
                  <CheckCircle2 className="h-3 w-3 text-green-500" />
                )}
                {llmMsg}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Separator />

      {/* App info */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Info className="h-4 w-4 text-primary" />
            <CardTitle className="text-base">앱 정보</CardTitle>
          </div>
          <CardDescription>
            버전 정보 및 임시 데이터 관리
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">버전</span>
            <span className="text-sm font-mono font-medium">
              v{packageJson.version}
            </span>
          </div>

          <Separator />

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">데이터 폴더</p>
              <p className="text-xs text-muted-foreground">
                앱 설정 및 임시 파일이 저장된 위치
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={handleOpenDataFolder}
            >
              <FolderOpen className="mr-1 h-3.5 w-3.5" />
              데이터 폴더 열기
            </Button>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">임시 파일 정리</p>
              <p className="text-xs text-muted-foreground">
                분석용 업로드 파일 및 내보내기 임시 파일을 모두 삭제합니다.
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={handleCleanup}
              disabled={cleaning}
            >
              {cleaning ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="mr-1 h-3.5 w-3.5" />
              )}
              임시 파일 정리
            </Button>
          </div>

          {cleanupMsg && (
            <p
              className={`text-xs ${
                cleanupError ? "text-destructive" : "text-muted-foreground"
              }`}
            >
              {cleanupMsg}
            </p>
          )}
        </CardContent>
      </Card>

      <Separator />

      {/* 백업 및 복원 — Sprint 5 P1 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Archive className="h-4 w-4 text-primary" />
            <CardTitle className="text-base">백업 및 복원</CardTitle>
          </div>
          <CardDescription>
            앱 설정과 감사 로그를 파일로 내보내거나, 다른 PC에서 만든 백업으로 복원합니다.
            보안을 위해 keyring(API 키)는 백업에 포함되지 않습니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* 내보내기 row */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">내보내기</p>
              <p className="text-xs text-muted-foreground">
                현재 설정과 감사 로그를 백업합니다.
              </p>
              {exportResult && (
                <div className="mt-2 rounded-md border border-green-300 bg-green-50 px-3 py-2 text-xs dark:border-green-800 dark:bg-green-950/40">
                  <div className="flex items-center gap-1.5 text-green-800 dark:text-green-300">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    <span className="font-medium">백업 저장됨</span>
                    {typeof exportResult.size_bytes === "number" && (
                      <span className="text-muted-foreground">
                        ({formatSize(exportResult.size_bytes)})
                      </span>
                    )}
                  </div>
                  <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                    {exportResult.file_path}
                  </p>
                  <button
                    type="button"
                    onClick={() => handleOpenBackupFolder(exportResult.file_path)}
                    className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-primary underline-offset-2 hover:underline"
                  >
                    <FolderOpen className="h-3 w-3" />
                    폴더 열기
                  </button>
                </div>
              )}
              {exportError && (
                <p className="mt-2 text-xs text-destructive">{exportError}</p>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={handleExport}
              disabled={exporting}
              className="shrink-0"
            >
              {exporting ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="mr-1.5 h-3.5 w-3.5" />
              )}
              백업 파일 만들기
            </Button>
          </div>

          <Separator />

          {/* 가져오기 row */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">가져오기</p>
              <p className="text-xs text-muted-foreground">
                다른 PC에서 만든 백업을 복원합니다. 기존 설정이 덮어써집니다.
              </p>
              {importResult && (
                <div className="mt-2 space-y-2 rounded-md border border-blue-300 bg-blue-50 px-3 py-2 text-xs dark:border-blue-800 dark:bg-blue-950/40">
                  <div className="flex items-center gap-1.5 text-blue-800 dark:text-blue-300">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    <span className="font-medium">복원 완료</span>
                  </div>
                  {Array.isArray(importResult.restored) && importResult.restored.length > 0 && (
                    <div>
                      <p className="font-medium text-foreground">복원된 항목</p>
                      <ul className="mt-0.5 ml-3 list-disc text-muted-foreground">
                        {importResult.restored.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {Array.isArray(importResult.warnings) && importResult.warnings.length > 0 && (
                    <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 dark:border-amber-700 dark:bg-amber-950/40">
                      <div className="flex items-center gap-1 text-amber-800 dark:text-amber-300">
                        <AlertTriangle className="h-3 w-3" />
                        <span className="font-medium">주의</span>
                      </div>
                      <ul className="mt-0.5 ml-3 list-disc text-amber-900 dark:text-amber-200">
                        {importResult.warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                      <p className="mt-1 text-amber-900 dark:text-amber-200">
                        보안상 OS 키체인에 저장된 값은 복원되지 않습니다.
                      </p>
                    </div>
                  )}
                  <p className="font-medium text-foreground">
                    변경 사항 적용을 위해 앱을 재시작해 주세요.
                  </p>
                </div>
              )}
              {importError && (
                <p className="mt-2 text-xs text-destructive">{importError}</p>
              )}
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={handlePickImportFile}
              disabled={importing}
              className="shrink-0"
            >
              {importing ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="mr-1.5 h-3.5 w-3.5" />
              )}
              백업 파일 선택
            </Button>
            <input
              ref={importInputRef}
              type="file"
              accept=".zip,.tar,.tar.gz,.tgz,.json,.bak,application/zip,application/x-tar,application/json"
              className="hidden"
              onChange={handleImportFileSelected}
            />
          </div>
        </CardContent>
      </Card>

      {/* 가져오기 확인 모달 */}
      <AlertDialog
        open={!!pendingImportFile}
        title="백업 복원 확인"
        description={
          pendingImportFile
            ? `"${pendingImportFile.name}" 파일로 복원합니다. 현재 앱의 설정과 감사 로그가 백업의 내용으로 덮어써집니다. 진행하시겠습니까?`
            : ""
        }
        confirmLabel="복원 진행"
        confirmVariant="destructive"
        onConfirm={handleConfirmImport}
        onCancel={() => setPendingImportFile(null)}
      />
    </div>
  );
}
