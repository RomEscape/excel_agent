/**
 * UpdateNotice — Sprint 5 P0.
 *
 * Tauri Updater plugin(@tauri-apps/plugin-updater)을 사용해
 *   1) 앱 시작 후 5초 뒤 새 버전 확인
 *   2) StatusBar 우측에 작은 dot 아이콘 + 토스트 알림
 *   3) 클릭 시 모달 (현재/새 버전, 변경 사항, 업데이트/나중에)
 *   4) "지금 업데이트" → 진행률 표시 → 자동 재시작
 *   5) localStorage `update.dismissed:{version}` 24h dismiss 기억
 *
 * graceful path:
 *   - 플러그인 미설치: dynamic import 실패 → 조용히 비활성
 *   - 네트워크/서명 오류: 모달 안에서 친절한 에러 + GitHub releases 링크
 */
import React, { useEffect, useState, useCallback, useRef } from "react";
import { Download, ExternalLink, X, Loader2, AlertCircle, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import packageJson from "../../../package.json";

const GITHUB_RELEASES_URL = "https://github.com/officeclaw/office-claw/releases";
const DISMISS_TTL_MS = 24 * 60 * 60 * 1000; // 24h

// Vite/esbuild가 정적 import 분석으로 모듈 부재 에러를 내지 않도록
// 변수 specifier로 dynamic import — 미설치 시 graceful disable.
const UPDATER_MODULE = "@tauri-apps/plugin-updater";

async function loadUpdaterPlugin() {
  try {
    const mod = await import(/* @vite-ignore */ UPDATER_MODULE);
    return mod;
  } catch {
    return null;
  }
}

function isDismissed(version) {
  if (!version) return false;
  try {
    const raw = localStorage.getItem(`update.dismissed:${version}`);
    if (!raw) return false;
    const ts = parseInt(raw, 10);
    if (!Number.isFinite(ts)) return false;
    return Date.now() - ts < DISMISS_TTL_MS;
  } catch {
    return false;
  }
}

function setDismissed(version) {
  try {
    localStorage.setItem(`update.dismissed:${version}`, String(Date.now()));
  } catch {
    // ignore
  }
}

export default function UpdateNotice() {
  // null = 아직 확인 안 함 / 미지원, undefined = 최신, 객체 = 새 버전 있음
  const [updateInfo, setUpdateInfo] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [progress, setProgress] = useState(0); // 0~100
  // 받은 바이트는 진행률 계산용 누산기라 화면에 직접 쓰지 않는다.
  const [, setDownloaded] = useState(0);
  const [contentLength, setContentLength] = useState(0);
  const [error, setError] = useState("");

  const checkedRef = useRef(false);

  // ── 5초 뒤 1회 check ─────────────────────────────────────────────────────
  useEffect(() => {
    if (checkedRef.current) return;
    checkedRef.current = true;

    const t = setTimeout(async () => {
      const plugin = await loadUpdaterPlugin();
      if (!plugin || typeof plugin.check !== "function") {
        // 플러그인 미설치 — graceful disable
        return;
      }
      try {
        const result = await plugin.check();
        if (!result) return; // 최신
        const version = result.version || result?.manifest?.version;
        if (!version) return;
        if (isDismissed(version)) return;
        setUpdateInfo(result);
      } catch {
        // 네트워크 오류 등은 조용히 무시 (사용자가 명시적으로 확인할 때만 노출)
      }
    }, 5000);

    return () => clearTimeout(t);
  }, []);

  // ── 업데이트 다운로드 + 설치 ──────────────────────────────────────────────
  const handleInstall = useCallback(async () => {
    if (!updateInfo) return;
    setError("");
    setInstalling(true);
    setProgress(0);
    setDownloaded(0);
    setContentLength(0);

    try {
      await updateInfo.downloadAndInstall((event) => {
        // event 형식: { event: 'Started'|'Progress'|'Finished', data: {...} }
        if (!event) return;
        if (event.event === "Started") {
          setContentLength(event.data?.contentLength ?? 0);
        } else if (event.event === "Progress") {
          const chunkLen = event.data?.chunkLength ?? 0;
          setDownloaded((prev) => {
            const next = prev + chunkLen;
            if (contentLength > 0 || event.data?.contentLength) {
              const total = contentLength || event.data?.contentLength;
              setProgress(Math.min(100, Math.round((next / total) * 100)));
            }
            return next;
          });
        } else if (event.event === "Finished") {
          setProgress(100);
        }
      });
      // downloadAndInstall은 자동 재시작을 트리거 — 여기까지 도달하면 정상.
    } catch (err) {
      setInstalling(false);
      setError(err?.message || String(err));
    }
  }, [updateInfo, contentLength]);

  const handleDismiss = useCallback(() => {
    if (updateInfo) {
      const version = updateInfo.version || updateInfo?.manifest?.version;
      if (version) setDismissed(version);
    }
    setModalOpen(false);
    setUpdateInfo(null); // 세션 동안 dot 숨김
  }, [updateInfo]);

  if (!updateInfo) return null;

  const newVersion = updateInfo.version || updateInfo?.manifest?.version || "?";
  const releaseNotes = updateInfo.body || updateInfo?.manifest?.body || "";
  const currentVersion = packageJson.version;

  return (
    <>
      {/* StatusBar 우측에 띄우는 작은 dot 알림 (fixed position).
          StatusBar 내부에 마운트하지 않고 fixed로 띄움 → Layout이 어디서든 한 번만 마운트하면 됨. */}
      {!modalOpen && !installing && (
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="fixed right-4 top-12 z-40 flex items-center gap-1.5 rounded-full border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 shadow-md hover:bg-blue-100 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-300 dark:hover:bg-blue-900 animate-in slide-in-from-top-2 fade-in-0"
          title={`새 버전 v${newVersion} 사용 가능`}
        >
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-500" />
          </span>
          <Sparkles className="h-3 w-3" />
          새 버전 v{newVersion}
        </button>
      )}

      {/* 설치 중 인디케이터 — 모달이 닫혀 있어도 진행률 노출 */}
      {installing && !modalOpen && (
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="fixed right-4 top-12 z-40 flex items-center gap-2 rounded-full border border-blue-300 bg-background px-3 py-1.5 text-xs font-medium shadow-md hover:bg-muted"
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          업데이트 다운로드 중 {progress}%
        </button>
      )}

      {/* 모달 */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 overflow-y-auto bg-black/50 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="update-modal-title"
        >
          <div
            className="flex min-h-full items-center justify-center p-4"
            onClick={(e) => {
              if (e.target === e.currentTarget && !installing) setModalOpen(false);
            }}
          >
          <div
            className={cn(
              "relative z-10 w-full max-w-md rounded-lg border bg-background p-6 shadow-lg",
              "animate-in fade-in-0 zoom-in-95"
            )}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-blue-500" />
                <h2 id="update-modal-title" className="text-base font-semibold">
                  새 버전이 준비되었습니다
                </h2>
              </div>
              {!installing && (
                <button
                  onClick={() => setModalOpen(false)}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="닫기"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>

            <div className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2">
                <span className="text-muted-foreground">현재 버전</span>
                <span className="font-mono">v{currentVersion}</span>
              </div>
              <div className="flex items-center justify-between rounded-md border border-blue-300 bg-blue-50 px-3 py-2 dark:border-blue-700 dark:bg-blue-950/40">
                <span className="text-blue-700 dark:text-blue-300">새 버전</span>
                <span className="font-mono font-semibold text-blue-700 dark:text-blue-300">
                  v{newVersion}
                </span>
              </div>

              {releaseNotes && (
                <div className="space-y-1">
                  <p className="text-xs font-semibold text-muted-foreground">변경 사항</p>
                  <div className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-muted/20 p-3 text-xs">
                    {releaseNotes}
                  </div>
                </div>
              )}

              {installing && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      다운로드 중...
                    </span>
                    <span className="font-mono">{progress}%</span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-blue-500 transition-all"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    설치가 완료되면 앱이 자동으로 재시작됩니다.
                  </p>
                </div>
              )}

              {error && (
                <div className="space-y-2 rounded-md border border-red-300 bg-red-50 p-3 text-xs text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
                  <div className="flex items-start gap-1.5">
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <div className="space-y-1">
                      <p className="font-semibold">업데이트에 실패했습니다.</p>
                      <p className="break-words text-[11px] opacity-80">{error}</p>
                    </div>
                  </div>
                  <a
                    href={GITHUB_RELEASES_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs font-medium underline-offset-2 hover:underline"
                  >
                    <ExternalLink className="h-3 w-3" />
                    GitHub에서 직접 다운로드
                  </a>
                </div>
              )}
            </div>

            {!installing && (
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={handleDismiss}>
                  나중에
                </Button>
                <Button size="sm" onClick={handleInstall}>
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  지금 업데이트
                </Button>
              </div>
            )}
          </div>
          </div>
        </div>
      )}
    </>
  );
}
