import React, { useEffect } from "react";
import Layout from "@/components/layout/Layout";
import OnboardingWizard from "@/components/onboarding/OnboardingWizard";
import LocalAISetupWizard from "@/components/guide/LocalAISetupWizard";
import useAppStore from "@/store/appStore";
import { useStatusPoller } from "@/hooks/useStatusPoller";
import { initTheme } from "@/lib/themeManager";

/**
 * Root application component.
 * All routing is handled by Zustand store (currentPage), not a URL router,
 * because this is a Tauri desktop app with no URL bar.
 *
 * Shows the OnboardingWizard on first launch (onboardingComplete === false).
 *
 * 예전에는 여기서 텔레그램 봇 자동 시작과 보안 승인 큐 폴링(`ApprovalDialog`)을
 * 함께 했다. 메신저 봇 기능이 제거되면서 둘 다 사라졌다 — 승인 큐에 넣는 곳이
 * 메신저 경로뿐이었기 때문이다. 남은 승인 경로는 두 가지다:
 *   - 엑셀 CONFIRM → 채팅 패널 말풍선 인라인 버튼 (`chatManager`)
 *   - 모바일 → relay 자체 승인 프레임 (`relay_client`)
 */
export default function App() {
  const onboardingComplete = useAppStore((s) => s.onboardingComplete);

  // 시스템 상태 중앙 폴러 — ollama 모듈 상태를 30초마다 자동 갱신.
  // StatusBar/LocalAISetupWizard는 모두 statusStore에서 동일한 데이터를 읽는다.
  useStatusPoller();

  // 테마 적용 — OS 선호를 읽어 <html>에 .dark를 붙이고 이후 변경도 따라간다.
  // 가장 먼저 돌아야 첫 페인트에서 라이트로 번쩍이지 않는다.
  useEffect(() => initTheme(), []);

  return (
    <>
      <Layout />
      {!onboardingComplete && <OnboardingWizard />}

      {/* Ollama 로컬 모델 자동 설정 위저드
          OnboardingWizard가 떠 있으면 내부 가드로 노출 안 함(중복 방지) */}
      <LocalAISetupWizard />
    </>
  );
}
