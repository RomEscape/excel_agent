/**
 * SettingsHub.jsx — 모든 설정/관리 메뉴를 흡수한 통합 허브.
 *
 * 좌측 sub-nav로 카테고리를 선택하고 우측에 해당 콘텐츠가 렌더링된다.
 * 핵심 가치(비개발자가 로컬 AI를 쉽게 설치 + 보안성 제고)에 맞춰
 * "로컬 AI" 탭을 가장 먼저 강조 노출한다.
 *
 * settings/credentials/audit/security/permissions/guide/mobile_relay 어떤 키로
 * 진입해도 Layout이 모두 SettingsHub로 라우팅하므로,
 * 최초 마운트 시 useAppStore.currentPage 값으로 활성 탭을 결정한다.
 */
import React, { lazy, Suspense, useEffect, useState } from "react";
import {
  Cpu,
  KeyRound,
  ClipboardList,
  ShieldCheck,
  ShieldAlert,
  Smartphone,
  Bot,
  SlidersHorizontal,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import useAppStore from "@/store/appStore";

const GeneralSettings = lazy(() => import("./Settings"));
const CredentialsManager = lazy(() => import("@/components/credentials/CredentialsManager"));
const AuditLog = lazy(() => import("@/components/audit/AuditLog"));
const SecurityDashboard = lazy(() => import("@/components/security/SecurityDashboard"));
const PermissionManager = lazy(() => import("@/components/permissions/PermissionManager"));
const SetupGuide = lazy(() => import("@/components/guide/SetupGuide"));
const RelayPairing = lazy(() => import("@/components/relay/RelayPairing"));

const TABS = [
  {
    id: "guide",
    label: "로컬 AI",
    icon: Bot,
    description: "Ollama 설치·실행 단계별 가이드",
    component: SetupGuide,
    highlight: true,
  },
  { id: "settings", label: "일반", icon: SlidersHorizontal, component: GeneralSettings },
  { id: "mobile_relay", label: "모바일 연결", icon: Smartphone, component: RelayPairing },
  { id: "credentials", label: "자격증명", icon: KeyRound, component: CredentialsManager },
  { id: "security", label: "보안", icon: ShieldCheck, component: SecurityDashboard },
  { id: "permissions", label: "에이전트 허용 범위", icon: ShieldAlert, component: PermissionManager },
  { id: "audit", label: "실행 기록", icon: ClipboardList, component: AuditLog },
];

function PaneLoader() {
  return (
    <div className="flex h-64 items-center justify-center">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function SettingsHub() {
  const currentPage = useAppStore((s) => s.currentPage);

  // 외부에서 settings/credentials/audit 등으로 진입하면 해당 탭 활성화.
  // 단순 "settings"는 첫 탭(가이드)으로 시작.
  const initialTab = TABS.find((t) => t.id === currentPage)?.id ?? "guide";
  const [activeTab, setActiveTab] = useState(initialTab);

  // 사이드바에서 다른 설정 키로 다시 클릭한 경우 동기화
  useEffect(() => {
    const matched = TABS.find((t) => t.id === currentPage);
    if (matched) setActiveTab(matched.id);
  }, [currentPage]);

  const ActivePane = TABS.find((t) => t.id === activeTab)?.component ?? GeneralSettings;

  return (
    <div className="mx-auto flex h-full max-w-[1280px] gap-6">
      {/* Sub-navigation */}
      <nav
        className="w-52 shrink-0 space-y-0.5 border-r border-border pr-3"
        aria-label="설정 메뉴"
      >
        <h2 className="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          설정
        </h2>
        {TABS.map(({ id, label, icon: Icon, description, highlight }) => {
          const active = activeTab === id;
          return (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-foreground/70 hover:bg-muted hover:text-foreground",
                highlight && !active && "ring-1 ring-primary/20"
              )}
              aria-current={active ? "page" : undefined}
            >
              <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", highlight && "text-primary")} />
              <span className="flex flex-col">
                <span className={cn("font-medium", highlight && "text-primary")}>{label}</span>
                {description && (
                  <span className="text-[11px] font-normal text-muted-foreground">
                    {description}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </nav>

      {/* Active pane */}
      <div className="flex-1 min-w-0 overflow-y-auto pr-1">
        <Suspense fallback={<PaneLoader />}>
          <ActivePane />
        </Suspense>
      </div>
    </div>
  );
}
