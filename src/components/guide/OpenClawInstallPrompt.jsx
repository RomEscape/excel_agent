/**
 * OpenClawInstallPrompt.jsx — 앱이 먼저 알아채고 사용자에게 권한만 받으면
 * AutoInstallModal로 곧장 (필요 시) 설치 + 자동 시작까지 진행하는 상위 prompt.
 *
 * 트리거: 게이트웨이가 18789에서 응답하지 않을 때.
 *   - 바이너리 미설치 → npm install + spawn
 *   - 바이너리는 있지만 게이트웨이 꺼짐 → spawn만
 *
 * 흐름:
 *   1) 앱 마운트 후 게이트웨이 응답을 검사. 살아있으면 아무 것도 안 함
 *   2) 죽어있으면 confirm prompt → [지금 시작] → AutoInstallModal
 *   3) 모달이 자동으로 (설치 필요 시) 설치 → openclaw_ensure_running 호출 → 온라인 전환
 *   4) [나중에] 클릭 → 이번 세션 동안 다시 안 띄움 (session-only)
 *
 * 사용자가 OnboardingWizard를 보고 있으면 prompt를 띄우지 않는다 (중복 방지).
 */
import React, { useEffect, useState, useCallback } from "react";
import { Bot, Sparkles, Zap, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import useAppStore from "@/store/appStore";
import { openclawStatus, openclawInstalled } from "@/lib/api";
import { AutoInstallModal } from "./SetupGuide";

/** Tauri 초기화 + sidecar의 첫 health check를 잠깐 기다림 (ms). */
const INITIAL_DELAY_MS = 1500;

export default function OpenClawInstallPrompt() {
  const onboardingComplete = useAppStore((s) => s.onboardingComplete);
  const llmProvider = useAppStore((s) => s.llmConfig?.provider);
  const setOpenClawStatus = useAppStore((s) => s.setOpenClawStatus);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  // session-only — 새로고침/재실행 시 다시 노출
  const [dismissed, setDismissed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [installModalOpen, setInstallModalOpen] = useState(false);
  // 진단 결과: gateway running + binary installed 두 축
  // null = 미확인, 'online' = 게이트웨이 살아있음, 'needs_start' = 설치O 실행X, 'needs_install' = 둘 다 X
  const [diag, setDiag] = useState(null);

  // 게이트웨이 + 바이너리 둘 다 검사하여 어떤 행동을 해야 할지 결정
  const runDiagnosis = useCallback(async () => {
    let gatewayRunning = false;
    try {
      const oc = await openclawStatus();
      gatewayRunning = oc?.state === "running";
      // store도 최신 상태로 동기화
      setOpenClawStatus({
        state: oc?.state ?? "stopped",
        message: oc?.message ?? "",
        port: oc?.port,
      });
    } catch {
      gatewayRunning = false;
    }

    if (gatewayRunning) {
      setDiag("online");
      return;
    }

    // 게이트웨이 죽어있음 → 바이너리 설치 여부 추가 확인
    let binaryInstalled = false;
    try {
      const inst = await openclawInstalled();
      binaryInstalled = Boolean(inst?.installed);
    } catch {
      binaryInstalled = false;
    }
    setDiag(binaryInstalled ? "needs_start" : "needs_install");
  }, [setOpenClawStatus]);

  // 앱 시작 직후 약간 대기한 뒤 1회 검사
  useEffect(() => {
    const timer = setTimeout(runDiagnosis, INITIAL_DELAY_MS);
    return () => clearTimeout(timer);
  }, [runDiagnosis]);

  // 미실행이면 confirm prompt 자동 노출 (한 번만)
  // provider=ollama 사용자에게는 LocalAISetupWizard가 더 포괄적이므로 *자동* 노출은 양보한다.
  // 단 수동 트리거(dispatchEvent)는 그대로 동작 — 다른 useEffect에서 처리.
  useEffect(() => {
    if (diag !== "needs_start" && diag !== "needs_install") return;
    if (!onboardingComplete) return;
    if (dismissed) return;
    if (installModalOpen) return;
    if (llmProvider === "ollama") return; // LocalAISetupWizard에 위임 — 중복 모달 방지
    if (!confirmOpen) {
      setConfirmOpen(true);
    }
  }, [diag, onboardingComplete, dismissed, confirmOpen, installModalOpen, llmProvider]);

  // 외부에서 prompt 재개 요청 (예: Dashboard 배너의 "지금 시작" 버튼)
  useEffect(() => {
    const handleOpen = () => {
      setDismissed(false);
      // 최신 상태 다시 확인
      runDiagnosis();
      setConfirmOpen(true);
    };
    window.addEventListener("private-claw:open-openclaw-install", handleOpen);
    return () => window.removeEventListener("private-claw:open-openclaw-install", handleOpen);
  }, [runDiagnosis]);

  const handleStart = () => {
    setConfirmOpen(false);
    setInstallModalOpen(true);
  };

  const handleLater = () => {
    setConfirmOpen(false);
    setDismissed(true);
  };

  const handleOpenGuide = () => {
    setConfirmOpen(false);
    setDismissed(true);
    setCurrentPage("guide");
  };

  const handleInstallModalClose = () => {
    setInstallModalOpen(false);
  };

  const handleOnline = async () => {
    // 모달이 ensure_running 성공을 보고하면 호출됨 → 진단 다시
    await runDiagnosis();
    setDismissed(true);
  };

  const needsInstall = diag === "needs_install";
  const headerTitle = needsInstall ? "OpenClaw 설치가 필요합니다" : "OpenClaw 게이트웨이를 시작할까요?";
  const bodyTitle = needsInstall ? "지금 자동으로 설치하고 시작할까요?" : "지금 게이트웨이를 시작할까요?";
  const bodyDesc = needsInstall ? (
    <>
      터미널을 직접 열 필요 없이 ajou-ai가{" "}
      <code className="rounded bg-muted px-1 font-mono">npm install -g openclaw@latest</code>
      를 실행한 뒤 <code className="rounded bg-muted px-1 font-mono">openclaw gateway --port 18789</code>
      을 자동으로 띄웁니다. 약 1~2분 소요.
    </>
  ) : (
    <>
      OpenClaw는 이미 설치되어 있어요. ajou-ai가{" "}
      <code className="rounded bg-muted px-1 font-mono">openclaw gateway --port 18789</code>
      를 자식 프로세스로 시작합니다. 앱이 종료되면 함께 종료됩니다.
    </>
  );
  const ctaLabel = needsInstall ? "지금 설치" : "지금 시작";

  return (
    <>
      {/* 권한 확인 prompt */}
      {confirmOpen && (
        <div
          className="fixed inset-0 z-[1050] flex items-center justify-center bg-black/50 px-4"
          role="dialog"
          aria-modal="true"
          aria-label="OpenClaw 시작 안내"
        >
          <div className="w-full max-w-md overflow-hidden rounded-lg border border-border bg-popover shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <div className="flex items-center gap-2">
                <Bot className="h-4 w-4 text-primary" />
                <h2 className="text-sm font-semibold">{headerTitle}</h2>
              </div>
              <button
                type="button"
                onClick={handleLater}
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label="닫기"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3 p-5">
              <p className="text-sm">
                ajou-ai가 동작하려면 <strong>OpenClaw 게이트웨이(18789)</strong>가 실행 중이어야 합니다.
              </p>

              <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs">
                <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                <div className="space-y-1">
                  <p className="font-medium text-foreground">{bodyTitle}</p>
                  <p className="text-muted-foreground">{bodyDesc}</p>
                </div>
              </div>

              <div className="flex items-center justify-between gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleOpenGuide}
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  수동 설치 안내 보기
                </button>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handleLater}>
                    나중에
                  </Button>
                  <Button size="sm" onClick={handleStart}>
                    <Zap className="mr-1.5 h-3.5 w-3.5" />
                    {ctaLabel}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 실제 진행 모달 — (필요 시) 설치 → ensure_running → 온라인 */}
      <AutoInstallModal
        open={installModalOpen}
        onClose={handleInstallModalClose}
        onOnline={handleOnline}
        skipInstall={diag === "needs_start"}
      />
    </>
  );
}
