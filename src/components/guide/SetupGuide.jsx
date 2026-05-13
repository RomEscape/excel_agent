import React, { useState, useRef, useEffect, useCallback } from "react";
import {
  Mail,
  MessageCircle,
  MessagesSquare,
  Cpu,
  Bot,
  ChevronRight,
  ExternalLink,
  Monitor,
  Apple,
  Terminal,
  Copy,
  Check,
  Sparkles,
  Zap,
  X,
  AlertCircle,
  Loader2,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import useAppStore from "@/store/appStore";
import { openclawStatus, openclawEnsureRunning, openclawInstalled } from "@/lib/api";

/**
 * 탭 순서 (PIVOT_PLAN v3.0 정렬):
 *   1) OpenClaw 설치 (NEW, 첫 활성)
 *   2) Ollama 설치
 *   3) 텔레그램 봇
 *   4) Slack/Discord 봇
 *   5) Gmail 안내 (격하 — Open-CLAW 스킬 안내)
 *
 * Designer R1 / Planner R1 P0-3 합의에 따라 Gmail은 마지막으로 격하.
 */
const TABS = [
  { id: "openclaw", label: "OpenClaw 설치", icon: Bot, badge: "NEW" },
  { id: "ollama",   label: "Ollama 설치",   icon: Cpu },
  { id: "telegram", label: "텔레그램 봇",   icon: MessageCircle },
  { id: "slack",    label: "Slack/Discord", icon: MessagesSquare },
  { id: "gmail",    label: "Gmail 안내",    icon: Mail },
  { id: "claude",   label: "Claude API",    icon: Bot },
];

// ── 공통 building blocks ─────────────────────────────────────────────────────

function Step({ number, title, children }) {
  return (
    <div className="flex gap-4">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary mt-0.5">
        {number}
      </div>
      <div className="flex-1 pb-5">
        <p className="text-sm font-semibold mb-1">{title}</p>
        <div className="text-sm text-muted-foreground space-y-1">{children}</div>
      </div>
    </div>
  );
}

function CodeBlock({ children }) {
  return (
    <code className="inline-block rounded bg-muted px-2 py-0.5 font-mono text-xs text-foreground">
      {children}
    </code>
  );
}

function Note({ children }) {
  return (
    <p className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-400">
      {children}
    </p>
  );
}

function LinkBadge({ href, children }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/5 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/10 transition-colors"
    >
      {children}
      <ExternalLink className="h-3 w-3" />
    </a>
  );
}

function NavButton({ onClick, label }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
    >
      {label} <ChevronRight className="h-3 w-3" />
    </button>
  );
}

/**
 * 1-click 클립보드 복사 코드 블록.
 * 비개발자가 터미널 명령을 그대로 복사할 수 있도록 큰 hit area + 시각 피드백 제공.
 */
function CopyableCommand({ command, label }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore — 사용자에게는 텍스트가 보이므로 수동 복사 가능
    }
  };
  return (
    <div className="mt-1.5 flex items-center gap-2 rounded-md border border-border bg-muted/60 px-3 py-2">
      <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <code className="flex-1 select-all font-mono text-xs">{command}</code>
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[11px] font-medium hover:bg-muted"
        title={label ?? "복사"}
      >
        {copied ? (
          <>
            <Check className="h-3 w-3 text-green-600" />
            복사됨
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" />
            복사
          </>
        )}
      </button>
    </div>
  );
}

// ── OpenClaw 자동 설치 + 자동 시작 모달 ──────────────────────────────────────
//
// 단계: confirm → installing(옵션) → starting → done | error | cancelled
//
// 1) installing: `npm install -g openclaw@latest` (skipInstall=true면 건너뜀)
// 2) starting: Tauri의 openclaw_ensure_running 호출 → `openclaw gateway --port 18789` 자식
//    프로세스로 spawn 후 ready까지 대기
// 3) done: ocStatus를 갱신하고 onOnline 콜백 호출
//
// `@tauri-apps/plugin-shell`의 Command를 사용해 npm install을 실행. stdout/stderr 라인을
// 실시간으로 스트리밍 표시. capabilities/default.json의 shell:allow-spawn에 npm이 등록되어
// 있어야 한다.
//
// Props:
//   - open: boolean
//   - onClose: () => void
//   - onOnline: () => void  — 게이트웨이 ready 도달 시 호출
//   - skipInstall: boolean  — true면 npm install을 건너뛰고 바로 starting으로
//   - onInstalled: () => void  — (deprecated) onOnline alias, 하위 호환을 위해 유지
export function AutoInstallModal({ open, onClose, onOnline, onInstalled, skipInstall = false }) {
  const [phase, setPhase] = useState("confirm"); // confirm | installing | starting | done | error | cancelled
  const [logs, setLogs] = useState([]);
  const [exitCode, setExitCode] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  const childRef = useRef(null);
  const logBoxRef = useRef(null);
  const setOpenClawStatus = useAppStore((s) => s.setOpenClawStatus);

  // 모달 open 시 상태 초기화
  useEffect(() => {
    if (!open) return;
    setPhase("confirm");
    setLogs([]);
    setExitCode(null);
    setErrorMsg("");
    childRef.current = null;
  }, [open]);

  // 자동 스크롤 — 새 로그 라인이 추가되면 콘솔을 맨 아래로
  useEffect(() => {
    if (logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [logs]);

  const installCmdLabel = "npm install -g openclaw@latest";
  const startCmdLabel = "openclaw gateway --port 18789";

  // 게이트웨이 시작 단계 — Tauri 백엔드의 openclaw_ensure_running 호출
  const runStartPhase = useCallback(async () => {
    setPhase("starting");
    setLogs((prev) => [
      ...prev,
      { kind: "info", text: `$ ${startCmdLabel}` },
      { kind: "info", text: "게이트웨이 시작 + ready 대기 중 (최대 30초)..." },
    ]);
    try {
      const result = await openclawEnsureRunning();
      setOpenClawStatus({
        state: result?.state ?? "stopped",
        message: result?.message ?? "",
        port: result?.port,
      });
      if (result?.state === "running") {
        setPhase("done");
        setLogs((prev) => [
          ...prev,
          { kind: "info", text: `게이트웨이 온라인 — 포트 ${result?.port ?? 18789}.` },
        ]);
        onOnline?.();
        onInstalled?.();
      } else {
        setPhase("error");
        setErrorMsg(result?.message || "게이트웨이가 응답하지 않습니다");
        setLogs((prev) => [
          ...prev,
          { kind: "err", text: result?.message || "게이트웨이 ready 실패" },
        ]);
      }
    } catch (err) {
      setPhase("error");
      const msg = String(err?.message ?? err);
      setErrorMsg(msg);
      setLogs((prev) => [...prev, { kind: "err", text: msg }]);
    }
  }, [onOnline, onInstalled, setOpenClawStatus]);

  // npm install 단계 — 종료 후 자동으로 starting 으로 전환
  const runInstallPhase = useCallback(async () => {
    setPhase("installing");
    setLogs([{ kind: "info", text: `$ ${installCmdLabel}` }]);
    try {
      const { Command } = await import("@tauri-apps/plugin-shell");
      const cmd = Command.create("npm", ["install", "-g", "openclaw@latest"]);

      cmd.stdout.on("data", (line) => {
        setLogs((prev) => [...prev, { kind: "out", text: String(line).trimEnd() }]);
      });
      cmd.stderr.on("data", (line) => {
        // npm은 진행 상황을 stderr로도 많이 흘림 — 모두 표시
        setLogs((prev) => [...prev, { kind: "err", text: String(line).trimEnd() }]);
      });
      cmd.on("close", async (data) => {
        const code = data?.code ?? 0;
        setExitCode(code);
        childRef.current = null;
        if (code === 0) {
          setLogs((prev) => [...prev, { kind: "info", text: "설치 완료. 자동으로 게이트웨이를 시작합니다." }]);
          // 설치 성공 → starting 단계로 자동 전환
          runStartPhase();
        } else {
          setPhase("error");
          setLogs((prev) => [
            ...prev,
            { kind: "info", text: `npm 종료 코드 ${code} — 설치 실패.` },
          ]);
        }
      });
      cmd.on("error", (err) => {
        setErrorMsg(String(err));
        setPhase("error");
        childRef.current = null;
      });

      const child = await cmd.spawn();
      childRef.current = child;
    } catch (err) {
      setErrorMsg(String(err?.message ?? err));
      setPhase("error");
    }
  }, [installCmdLabel, runStartPhase]);

  // 사용자가 confirm에서 [실행] 클릭 — skipInstall에 따라 분기
  const startFlow = useCallback(() => {
    if (skipInstall) {
      // 설치 건너뛰고 바로 시작
      setLogs([{ kind: "info", text: "OpenClaw가 이미 설치되어 있어 설치 단계를 건너뜁니다." }]);
      runStartPhase();
    } else {
      runInstallPhase();
    }
  }, [skipInstall, runInstallPhase, runStartPhase]);

  const cancelFlow = useCallback(async () => {
    if (childRef.current) {
      try {
        await childRef.current.kill();
      } catch {
        // ignore — 이미 종료됐을 수 있음
      }
      childRef.current = null;
    }
    setPhase("cancelled");
    setLogs((prev) => [...prev, { kind: "info", text: "사용자가 작업을 중단했습니다." }]);
  }, []);

  const copyManualCommand = async () => {
    try {
      await navigator.clipboard.writeText(`${installCmdLabel} && ${startCmdLabel}`);
    } catch {
      // ignore
    }
  };

  if (!open) return null;

  const isWorking = phase === "installing" || phase === "starting";
  const canCloseSafely = !isWorking;

  const headerTitle = skipInstall ? "OpenClaw 게이트웨이 시작" : "OpenClaw 자동 설치 + 시작";
  const confirmCmd = skipInstall ? startCmdLabel : `${installCmdLabel}\n${startCmdLabel}`;
  const confirmDesc = skipInstall
    ? "OpenClaw는 이미 설치되어 있어 시작 단계만 실행합니다."
    : "이 명령은 OpenClaw를 전역 설치한 뒤 게이트웨이를 자식 프로세스로 시작합니다.";

  return (
    <div className="fixed inset-0 z-[1100] overflow-y-auto bg-black/50">
      <div
        className="flex min-h-full items-center justify-center p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget && canCloseSafely) onClose();
        }}
      >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="OpenClaw 자동 설치 및 시작"
        className="w-full max-w-xl overflow-hidden rounded-lg border border-border bg-popover shadow-2xl"
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">{headerTitle}</h2>
          </div>
          <button
            type="button"
            onClick={canCloseSafely ? onClose : undefined}
            disabled={!canCloseSafely}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent"
            aria-label="닫기"
            title={canCloseSafely ? "닫기" : "진행 중에는 닫을 수 없습니다"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 p-5">
          {/* 권한 확인 단계 */}
          {phase === "confirm" && (
            <>
              <p className="text-sm">
                ajou-ai가 다음 명령을 PC에서 실행합니다. 진행하시겠습니까?
              </p>
              <div className="flex items-start gap-2 rounded-md border border-border bg-muted/60 px-3 py-2">
                <Terminal className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <code className="flex-1 select-all whitespace-pre-line break-all font-mono text-xs">{confirmCmd}</code>
              </div>
              <div className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
                <p className="flex items-start gap-1.5">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{confirmDesc}</span>
                </p>
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={onClose}>
                  취소
                </Button>
                <Button size="sm" onClick={startFlow}>
                  <Zap className="mr-1.5 h-3.5 w-3.5" />
                  실행
                </Button>
              </div>
            </>
          )}

          {/* 실행 중 / 결과 단계 — 콘솔 표시 */}
          {phase !== "confirm" && (
            <>
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-1.5 font-medium">
                  {phase === "installing" && (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      설치 중...
                    </>
                  )}
                  {phase === "starting" && (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      게이트웨이 시작 중...
                    </>
                  )}
                  {phase === "done" && (
                    <>
                      <Check className="h-3.5 w-3.5 text-green-600" />
                      온라인
                    </>
                  )}
                  {phase === "error" && (
                    <>
                      <AlertCircle className="h-3.5 w-3.5 text-destructive" />
                      실패{exitCode != null ? ` (code ${exitCode})` : ""}
                    </>
                  )}
                  {phase === "cancelled" && (
                    <>
                      <X className="h-3.5 w-3.5 text-muted-foreground" />
                      취소됨
                    </>
                  )}
                </span>
                {isWorking && phase === "installing" && (
                  <button
                    type="button"
                    onClick={cancelFlow}
                    className="rounded border border-border bg-background px-2 py-0.5 text-[11px] hover:bg-muted"
                  >
                    중단
                  </button>
                )}
              </div>

              <div
                ref={logBoxRef}
                className="max-h-[280px] overflow-y-auto rounded-md border border-border bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-100"
              >
                {logs.length === 0 ? (
                  <span className="text-zinc-500">출력 대기 중...</span>
                ) : (
                  logs.map((l, i) => (
                    <div
                      key={i}
                      className={
                        l.kind === "err"
                          ? "text-amber-300"
                          : l.kind === "info"
                          ? "text-zinc-400"
                          : "text-zinc-100"
                      }
                    >
                      {l.text}
                    </div>
                  ))
                )}
              </div>

              {phase === "error" && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
                  <p className="font-semibold">자동 처리에 실패했습니다.</p>
                  {errorMsg && <p className="mt-1 break-all">{errorMsg}</p>}
                  <p className="mt-2">
                    아래 명령을 복사해 터미널에서 직접 실행하세요.
                  </p>
                  <div className="mt-2 flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1 font-mono text-foreground">
                    <code className="flex-1 select-all break-all">{installCmdLabel} && {startCmdLabel}</code>
                    <button
                      type="button"
                      onClick={copyManualCommand}
                      className="inline-flex items-center gap-1 rounded border border-border bg-muted px-2 py-0.5 text-[10px] hover:bg-muted/80"
                    >
                      <Copy className="h-3 w-3" />
                      복사
                    </button>
                  </div>
                </div>
              )}

              <div className="flex justify-end gap-2">
                <Button
                  variant={phase === "done" ? "default" : "outline"}
                  size="sm"
                  onClick={onClose}
                  disabled={isWorking}
                >
                  {phase === "done" ? "완료" : "닫기"}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}

// ── OpenClaw 설치 가이드 (P0-3 신규) ─────────────────────────────────────────

function OpenClawGuide() {
  const [os, setOs] = useState("mac");
  const [installModalOpen, setInstallModalOpen] = useState(false);
  // N-4: sudo는 GUI 환경에서 stdin hang 위험 → sudo 옵션 제거, permission denied 시 수동 안내로 fallback
  const [showSudoFallback, setShowSudoFallback] = useState(false);

  return (
    <div>
      <div className="mb-4 rounded-md border border-primary/20 bg-primary/5 p-3">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="text-sm">
            <p className="font-semibold text-foreground">왜 OpenClaw가 필요한가요?</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              ajou-ai는 OpenClaw 게이트웨이를 통해 PC 작업(파일/메일/문서)을 안전하게
              수행합니다. 모든 명령은 OpenClaw가 실행하기 전에 보안 정책으로 검사됩니다.
            </p>
          </div>
        </div>
      </div>

      {/* 자동 설치 카드 — 자동/수동 선택 */}
      <div className="mb-5 rounded-md border border-primary/30 bg-gradient-to-r from-primary/5 to-transparent p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <p className="flex items-center gap-1.5 text-sm font-semibold">
              <Zap className="h-3.5 w-3.5 text-primary" />
              한 번에 설치하기 (권장)
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              터미널을 직접 열지 않고 ajou-ai가 npm install을 실행합니다.
              설치 중 진행 로그가 실시간으로 표시되며 언제든 중단할 수 있습니다.
            </p>
          </div>
          <div className="flex shrink-0 flex-col gap-1.5">
            <Button size="sm" onClick={() => setInstallModalOpen(true)}>
              <Zap className="mr-1.5 h-3.5 w-3.5" />
              자동 설치 실행
            </Button>
            {os === "mac" && (
              // N-4: sudo는 GUI stdin hang 위험 — 클릭 시 수동 명령 안내로 대체
              <button
                type="button"
                onClick={() => setShowSudoFallback(true)}
                className="text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                title="permission denied 오류 시 터미널에서 직접 실행"
              >
                관리자 권한이 필요한 경우
              </button>
            )}
          </div>
        </div>
        {/* sudo fallback 안내 — permission denied 시 수동 복사 */}
        {showSudoFallback && (
          <div className="mt-3 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-300">
            <p className="mb-1.5 flex items-start gap-1.5">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              앱 내에서는 관리자 비밀번호 입력이 불가합니다. 터미널을 열어 아래 명령을 직접 실행해 주세요.
            </p>
            <CopyableCommand command="sudo npm install -g openclaw@latest" />
            <p className="mt-1.5 text-[11px] text-amber-700 dark:text-amber-400">
              또는 npm 전역 디렉토리를 사용자 홈으로 변경하면 sudo 없이 설치할 수 있습니다: <code className="font-mono">npm config set prefix ~/.npm-global</code>
            </p>
          </div>
        )}
      </div>

      <p className="mb-4 text-sm text-muted-foreground">
        또는 아래 단계에 따라 터미널에서 직접 명령을 실행해 설치할 수도 있습니다.
      </p>

      {/* OS Selector */}
      <div className="mb-5 flex gap-2">
        <button
          onClick={() => setOs("mac")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "mac"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Apple className="h-3.5 w-3.5" /> macOS
        </button>
        <button
          onClick={() => setOs("win")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "win"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Monitor className="h-3.5 w-3.5" /> Windows
        </button>
      </div>

      <div className="divide-y">
        <Step number={1} title="Node.js 설치 확인 (이미 있으면 건너뛰세요)">
          <p>터미널을 열고 아래 명령으로 버전을 확인합니다.</p>
          <CopyableCommand command="node -v" />
          <p>
            <CodeBlock>v18</CodeBlock> 이상이 표시되면 다음 단계로. 표시되지 않거나 오래된 버전이라면 아래에서 설치합니다.
          </p>
          <div className="mt-2">
            <LinkBadge href="https://nodejs.org/ko/download">nodejs.org/ko/download</LinkBadge>
          </div>
        </Step>

        {os === "mac" ? (
          <Step number={2} title="OpenClaw 전역 설치 (macOS)">
            <p>아래 명령을 복사해 터미널에 붙여넣고 Enter를 누르세요.</p>
            <CopyableCommand command="npm install -g openclaw@latest" />
            <Note>
              "permission denied" 오류가 발생하면 <CodeBlock>sudo</CodeBlock>를 앞에 붙여 다시 실행하세요.
            </Note>
          </Step>
        ) : (
          <Step number={2} title="OpenClaw 전역 설치 (Windows)">
            <p><strong>관리자 권한으로</strong> PowerShell 또는 cmd를 열고 아래 명령을 붙여넣으세요.</p>
            <CopyableCommand command="npm install -g openclaw@latest" />
            <Note>
              관리자 권한이 없으면 npm이 글로벌 설치를 거부할 수 있습니다.
              시작 메뉴에서 PowerShell을 검색해 "관리자 권한으로 실행"을 선택하세요.
            </Note>
          </Step>
        )}

        <Step number={3} title="OpenClaw 실행">
          <p>설치가 끝나면 아래 명령으로 게이트웨이를 시작합니다.</p>
          <CopyableCommand command="openclaw start" />
          <p>
            기본 포트 <CodeBlock>18789</CodeBlock>에서 게이트웨이가 시작됩니다.
            ajou-ai 앱 우측 상단의 <strong>OpenClaw</strong> 표시가 녹색으로 바뀌면 정상입니다.
          </p>
        </Step>

        <Step number={4} title="자동 시작 설정 (선택)">
          <p>PC를 켤 때마다 자동 실행되도록 하려면 아래 명령을 사용합니다.</p>
          <CopyableCommand command="openclaw install-service" />
          <Note>
            서비스 등록은 OS 권한을 요구합니다. 잘 모르겠다면 이 단계는 건너뛰고, 필요할 때 수동으로 <CodeBlock>openclaw start</CodeBlock>를 실행해도 됩니다.
          </Note>
        </Step>
      </div>

      {/* 자동 설치 모달 — N-4: useSudo 제거, 항상 일반 npm으로 실행 */}
      <AutoInstallModal
        open={installModalOpen}
        onClose={() => setInstallModalOpen(false)}
        onInstalled={() => {
          // 설치 성공 시 추가 후속 처리 (StatusBar 폴링이 곧 상태 갱신)
        }}
      />
    </div>
  );
}

// ── Ollama 가이드 (기존 유지) ────────────────────────────────────────────────

function OllamaGuide() {
  const [os, setOs] = useState("mac");

  return (
    <div>
      <p className="mb-4 text-sm text-muted-foreground">
        Ollama는 AI 모델을 인터넷 없이 내 컴퓨터에서 실행할 수 있는 도구입니다. 설치 후 앱과 자동으로 연결됩니다.
      </p>

      <div className="mb-5 flex gap-2">
        <button
          onClick={() => setOs("mac")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "mac"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Apple className="h-3.5 w-3.5" /> macOS
        </button>
        <button
          onClick={() => setOs("win")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
            os === "win"
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <Monitor className="h-3.5 w-3.5" /> Windows
        </button>
      </div>

      <div className="divide-y">
        {os === "mac" ? (
          <>
            <Step number={1} title="공식 홈페이지 접속">
              <p>Ollama 공식 다운로드 페이지에 접속합니다.</p>
              <div className="mt-2">
                <LinkBadge href="https://ollama.com/download">ollama.com/download</LinkBadge>
              </div>
            </Step>
            <Step number={2} title="다운로드">
              <p><strong>Download for Mac</strong> 버튼을 클릭해 <CodeBlock>Ollama-darwin.zip</CodeBlock> 파일을 받습니다.</p>
            </Step>
            <Step number={3} title="설치">
              <p>다운로드된 압축 파일을 풀면 Ollama 앱 아이콘이 나타납니다.</p>
              <p>아이콘을 <strong>응용 프로그램(Applications)</strong> 폴더로 드래그 앤 드롭합니다.</p>
            </Step>
            <Step number={4} title="실행">
              <p>응용 프로그램 폴더에서 Ollama를 실행합니다.</p>
              <p>"인터넷에서 다운로드된 앱" 확인 창이 뜨면 <strong>열기</strong>를 누릅니다.</p>
              <p>상단 메뉴 바에 라마 아이콘이 생기면 실행된 것입니다.</p>
            </Step>
            <Step number={5} title="모델 다운로드">
              <p>터미널을 열고 아래 명령어를 입력합니다.</p>
              <CopyableCommand command="ollama pull llama3.2" />
              <p className="mt-1">다운로드가 완료되면 앱에서 Ollama를 바로 사용할 수 있습니다.</p>
              <Note>다른 모델을 사용하려면 <CodeBlock>ollama pull 모델명</CodeBlock> 형식으로 입력하고, 설정에서 모델명을 변경하세요.</Note>
            </Step>
          </>
        ) : (
          <>
            <Step number={1} title="공식 홈페이지 접속">
              <p>Ollama 공식 다운로드 페이지에 접속합니다.</p>
              <div className="mt-2">
                <LinkBadge href="https://ollama.com/download">ollama.com/download</LinkBadge>
              </div>
            </Step>
            <Step number={2} title="다운로드">
              <p><strong>Download for Windows</strong> 버튼을 클릭해 <CodeBlock>OllamaSetup.exe</CodeBlock> 파일을 받습니다.</p>
            </Step>
            <Step number={3} title="설치">
              <p>다운로드한 설치 파일을 실행합니다.</p>
              <p>별도 설정 없이 <strong>Install</strong> 버튼만 누르면 자동으로 설치됩니다.</p>
            </Step>
            <Step number={4} title="실행 확인">
              <p>설치가 끝나면 Ollama가 자동으로 실행됩니다.</p>
              <p>우측 하단 <strong>시스템 트레이</strong>(작은 아이콘 모음)에 라마 아이콘이 나타나면 준비 완료입니다.</p>
            </Step>
            <Step number={5} title="모델 다운로드">
              <p>명령 프롬프트(cmd) 또는 PowerShell을 열고 아래 명령어를 입력합니다.</p>
              <CopyableCommand command="ollama pull llama3.2" />
              <p className="mt-1">다운로드가 완료되면 앱에서 Ollama를 바로 사용할 수 있습니다.</p>
              <Note>다른 모델을 사용하려면 <CodeBlock>ollama pull 모델명</CodeBlock> 형식으로 입력하고, 설정에서 모델명을 변경하세요.</Note>
            </Step>
          </>
        )}
      </div>
    </div>
  );
}

// ── 텔레그램 (기존 유지) ────────────────────────────────────────────────────

function TelegramGuide({ onGoToCredentials }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        텔레그램 봇을 만들고 Chat ID를 등록하면 앱에서 알림을 받거나 명령을 내릴 수 있습니다.
      </p>
      <div className="divide-y">
        <Step number={1} title="BotFather에서 봇 생성">
          <p>텔레그램 앱에서 <CodeBlock>@BotFather</CodeBlock>를 검색해 채팅을 시작합니다.</p>
          <p><CodeBlock>/newbot</CodeBlock> 명령을 입력하고 안내에 따라 봇 이름과 사용자명을 입력합니다.</p>
          <p>사용자명은 반드시 <CodeBlock>bot</CodeBlock>으로 끝나야 합니다. (예: <CodeBlock>my_office_bot</CodeBlock>)</p>
        </Step>
        <Step number={2} title="Bot Token 저장">
          <p>봇 생성이 완료되면 BotFather가 <strong>토큰</strong>을 발급해줍니다.</p>
          <p>형식: <CodeBlock>1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ</CodeBlock></p>
          <p>이 토큰을 자격증명 관리의 <CodeBlock>telegram_bot_token</CodeBlock>에 저장합니다.</p>
        </Step>
        <Step number={3} title="Chat ID 확인">
          <p>텔레그램에서 <CodeBlock>@userinfobot</CodeBlock>을 검색해 채팅을 시작합니다.</p>
          <p>아무 메시지나 보내면 본인의 <strong>Chat ID</strong>를 알려줍니다.</p>
          <p>이 숫자를 자격증명 관리의 <CodeBlock>telegram_chat_id</CodeBlock>에 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToCredentials} label="자격증명 관리로 이동" />
          </div>
        </Step>
        <Step number={4} title="봇 시작">
          <p>설정 > 메신저에서 <strong>봇 시작</strong> 버튼을 누릅니다.</p>
          <p>텔레그램 앱에서 생성한 봇에게 <CodeBlock>/start</CodeBlock>를 입력하면 연결이 완료됩니다.</p>
        </Step>
      </div>
    </div>
  );
}

// ── Slack / Discord 통합 가이드 (간단 안내) ─────────────────────────────────

function SlackDiscordGuide({ onGoToMessenger }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        Slack 또는 Discord 봇을 추가하면 같은 명령을 여러 메신저에서 사용할 수 있습니다.
        설정값은 모두 <strong>설정 > 메신저</strong>에서 입력합니다.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border border-border p-4">
          <div className="mb-2 flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-[#4A154B]" />
            <p className="text-sm font-semibold">Slack 봇</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Slack 워크스페이스에 봇을 추가하려면 Bot Token + App Token을 준비하세요.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <LinkBadge href="https://api.slack.com/apps">api.slack.com/apps</LinkBadge>
          </div>
        </div>

        <div className="rounded-md border border-border p-4">
          <div className="mb-2 flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-[#5865F2]" />
            <p className="text-sm font-semibold">Discord 봇</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Discord Developer Portal에서 봇을 만들고 Token을 발급받습니다.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <LinkBadge href="https://discord.com/developers/applications">discord.com/developers</LinkBadge>
          </div>
        </div>
      </div>

      <div className="mt-5">
        <NavButton onClick={onGoToMessenger} label="설정 / 메신저로 이동" />
      </div>
    </div>
  );
}

// ── Gmail 안내 (격하 — Open-CLAW 스킬 안내) ──────────────────────────────────

function GmailGuide() {
  return (
    <div>
      <div className="rounded-md border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-900/40 dark:bg-amber-950/30">
        <div className="flex items-start gap-2">
          <Mail className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="text-sm">
            <p className="font-semibold text-amber-900 dark:text-amber-100">
              Gmail은 OpenClaw 스킬로 제공됩니다
            </p>
            <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
              ajou-ai v3.0부터 Gmail 연동은 ajou-ai가 직접 관리하지 않고
              OpenClaw의 외부 스킬 패키지로 이관되었습니다. 메신저에서 "메일 확인해줘"
              명령을 사용하면 OpenClaw가 자동으로 Gmail 스킬을 호출합니다.
            </p>
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
              Gmail 스킬을 처음 사용할 때 OpenClaw가 OAuth 인증 페이지로 안내합니다.
              앱 내에서 별도 자격증명을 등록할 필요가 없습니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Claude API ───────────────────────────────────────────────────────────────

function ClaudeGuide({ onGoToCredentials, onGoToSettings }) {
  return (
    <div>
      <p className="mb-5 text-sm text-muted-foreground">
        Claude API는 Anthropic에서 제공하는 클라우드 AI입니다. API 키를 발급받아 등록하면 바로 사용할 수 있습니다.
      </p>
      <div className="divide-y">
        <Step number={1} title="Anthropic Console 접속">
          <p>Anthropic Console에 접속해 계정을 만들거나 로그인합니다.</p>
          <div className="mt-2">
            <LinkBadge href="https://console.anthropic.com">console.anthropic.com</LinkBadge>
          </div>
        </Step>
        <Step number={2} title="API 키 발급">
          <p>왼쪽 메뉴에서 <strong>API Keys</strong>를 선택합니다.</p>
          <p><strong>Create Key</strong> 버튼을 클릭하고 이름을 입력합니다. (예: <CodeBlock>private-claw</CodeBlock>)</p>
          <p>생성된 키를 복사합니다. 키는 이 화면에서 한 번만 표시되므로 바로 저장하세요.</p>
          <Note>API 키는 <CodeBlock>sk-ant-api03-</CodeBlock>로 시작합니다. 크레딧이 있어야 API를 사용할 수 있습니다.</Note>
        </Step>
        <Step number={3} title="앱에 API 키 저장">
          <p>자격증명 관리의 <CodeBlock>claude_api_key</CodeBlock>에 복사한 키를 붙여넣고 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToCredentials} label="자격증명 관리로 이동" />
          </div>
        </Step>
        <Step number={4} title="LLM 엔진을 Claude로 변경">
          <p>설정 메뉴에서 <strong>LLM 엔진</strong>을 <strong>Claude API</strong>로 변경하고 저장합니다.</p>
          <div className="mt-2">
            <NavButton onClick={onGoToSettings} label="설정으로 이동" />
          </div>
        </Step>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SetupGuide() {
  // 첫 진입 활성 탭 = openclaw (Planner R1 P0-3)
  const [activeTab, setActiveTab] = useState("openclaw");
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const content = {
    openclaw: <OpenClawGuide />,
    ollama:   <OllamaGuide />,
    telegram: <TelegramGuide onGoToCredentials={() => setCurrentPage("credentials")} />,
    slack:    <SlackDiscordGuide onGoToMessenger={() => setCurrentPage("messenger_settings")} />,
    gmail:    <GmailGuide />,
    claude:   <ClaudeGuide
                onGoToCredentials={() => setCurrentPage("credentials")}
                onGoToSettings={() => setCurrentPage("settings")}
              />,
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">설치 가이드</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          OpenClaw 게이트웨이 설치부터 메신저 봇 연결까지 단계별로 안내합니다.
        </p>
      </div>

      <Card>
        {/* Tab bar */}
        <div className="flex border-b overflow-x-auto">
          {TABS.map(({ id, label, icon: Icon, badge }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                activeTab === id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{label}</span>
              {badge && (
                <Badge variant="default" className="ml-0.5 h-4 px-1.5 text-[10px]">
                  {badge}
                </Badge>
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <CardContent className="pt-6">
          {content[activeTab]}
        </CardContent>
      </Card>
    </div>
  );
}
