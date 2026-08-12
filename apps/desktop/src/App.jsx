import React, { useEffect, useRef, useState, useCallback } from "react";
import Layout from "@/components/layout/Layout";
import OnboardingWizard from "@/components/onboarding/OnboardingWizard";
import LocalAISetupWizard from "@/components/guide/LocalAISetupWizard";
import ApprovalDialog from "@/components/security/ApprovalDialog";
import useAppStore from "@/store/appStore";
import { useStatusPoller } from "@/hooks/useStatusPoller";
import { initTheme } from "@/lib/themeManager";
import { telegramStatus, telegramStart, securityGetPendingApprovals, securityRespondApproval } from "@/lib/api";

/**
 * Root application component.
 * All routing is handled by Zustand store (currentPage), not a URL router,
 * because this is a Tauri desktop app with no URL bar.
 *
 * Shows the OnboardingWizard on first launch (onboardingComplete === false).
 *
 * 앱 시작 시 자동 봇 시작:
 * - onboardingComplete가 true이고 telegramConnected가 true인 경우
 * - sidecar 응답 대기 후 봇이 미실행 상태이면 자동으로 /telegram/start 호출
 *
 * Phase 2: 보안 승인 폴링
 * - 텔레그램 봇이 연결되지 않은 경우 앱 UI에서 HITL 승인을 처리한다.
 * - 5초 간격으로 /security/approval/pending 폴링
 * - pendingSecurityApproval 상태에 승인 요청을 저장 → ApprovalDialog 렌더링
 */

/** 보안 승인 폴링 간격 (ms) */
const SECURITY_POLL_INTERVAL_MS = 5000;

export default function App() {
  const onboardingComplete = useAppStore((s) => s.onboardingComplete);
  const telegramConnected = useAppStore((s) => s.telegramConnected);
  const autoStartAttempted = useRef(false);

  // 시스템 상태 중앙 폴러 — ollama 모듈 상태를 30초마다 자동 갱신.
  // Dashboard/StatusBar/LocalAISetupWizard는 모두 statusStore에서 동일한 데이터를 읽는다.
  useStatusPoller();

  // 테마 적용 — OS 선호를 읽어 <html>에 .dark를 붙이고 이후 변경도 따라간다.
  // 가장 먼저 돌아야 첫 페인트에서 라이트로 번쩍이지 않는다.
  useEffect(() => initTheme(), []);

  // Phase 2: 보안 UI 승인 상태
  const [pendingSecurityApproval, setPendingSecurityApproval] = useState(null);
  const pollTimerRef = useRef(null);

  useEffect(() => {
    // 온보딩 미완료이거나 봇 토큰 미설정이면 건너뜀
    if (!onboardingComplete || !telegramConnected) return;
    // 이미 시도했으면 재시도하지 않음 (StrictMode 이중 실행 방지)
    if (autoStartAttempted.current) return;
    autoStartAttempted.current = true;

    const autoStartBot = async () => {
      // sidecar 준비까지 잠시 대기 (앱 시작 직후 sidecar가 아직 초기화 중일 수 있음)
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        const status = await telegramStatus();
        if (!status?.running) {
          await telegramStart();
        }
      } catch {
        // 자동 시작 실패는 무시 — 사용자가 텔레그램 메뉴에서 수동 시작 가능
      }
    };

    autoStartBot();
  }, [onboardingComplete, telegramConnected]);

  // Phase 2: 보안 승인 요청 폴링
  // 텔레그램 봇이 연결되어 있지 않을 때 앱 UI로 HITL 승인을 처리한다.
  useEffect(() => {
    if (!onboardingComplete) return;

    const pollApprovals = async () => {
      // 이미 표시 중인 승인이 있으면 폴링 건너뜀
      if (pendingSecurityApproval) return;

      try {
        const result = await securityGetPendingApprovals();
        if (result?.pending?.length > 0) {
          // 가장 오래된 (첫 번째) 요청을 표시
          setPendingSecurityApproval(result.pending[0]);
        }
      } catch {
        // 폴링 실패는 무시 — sidecar가 아직 시작 중일 수 있음
      }
    };

    pollTimerRef.current = setInterval(pollApprovals, SECURITY_POLL_INTERVAL_MS);
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [onboardingComplete, pendingSecurityApproval]);

  const handleSecurityApprove = useCallback(async () => {
    if (!pendingSecurityApproval) return;
    const { approval_id } = pendingSecurityApproval;
    setPendingSecurityApproval(null);
    try {
      await securityRespondApproval(approval_id, true);
    } catch (err) {
      // 응답 전달 실패는 조용히 처리 (sidecar 측에서 타임아웃 처리됨)
    }
  }, [pendingSecurityApproval]);

  const handleSecurityReject = useCallback(async () => {
    if (!pendingSecurityApproval) return;
    const { approval_id } = pendingSecurityApproval;
    setPendingSecurityApproval(null);
    try {
      await securityRespondApproval(approval_id, false);
    } catch {
      // 응답 전달 실패는 조용히 처리
    }
  }, [pendingSecurityApproval]);

  return (
    <>
      <Layout />
      {!onboardingComplete && <OnboardingWizard />}

      {/* Ollama 로컬 모델 자동 설정 위저드
          OnboardingWizard가 떠 있으면 내부 가드로 노출 안 함(중복 방지) */}
      <LocalAISetupWizard />

      {/* Phase 2: 보안 HITL 승인 다이얼로그 — 텔레그램 미연결 시 앱 UI 대체 수단 */}
      {pendingSecurityApproval && (
        <ApprovalDialog
          open={true}
          command={pendingSecurityApproval.command}
          reason={pendingSecurityApproval.reason}
          auditId={pendingSecurityApproval.audit_id}
          timeoutSeconds={60}
          onApprove={handleSecurityApprove}
          onReject={handleSecurityReject}
        />
      )}
    </>
  );
}
