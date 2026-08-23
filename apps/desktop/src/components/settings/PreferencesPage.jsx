/**
 * PreferencesPage — 환경 설정 (개선안 와이어프레임 `243:1140`).
 *
 * 탭 허브(`SettingsHub`)가 아니라 **섹션을 세로로 쌓은 단일 페이지**다.
 * 사이드바 푸터의 `환경 설정`이 여기로 온다(페이지 키 `preferences`).
 *
 * SettingsHub는 지우지 않았다 — 메신저·자격증명·보안·허용 범위·실행 기록은
 * 와이어프레임에 없지만 실제 기능이 붙어 있고, `Cmd/Ctrl+K` 명령 팔레트가
 * 그 탭 키(`messenger_settings` 등)로 직접 진입시킨다. 여기서 SettingsHub까지
 * 없애면 그 기능들이 코드에만 남고 갈 길이 사라진다.
 *
 * 와이어프레임 6섹션 + 협의로 추가한 2섹션:
 *   1) 내 요금제        — 플레이스홀더 (결제·플랜 백엔드 없음)
 *   2) 디바이스 추가
 *   3) 연결된 디바이스
 *   4) 폰트 크기
 *   5) 시스템 모드
 *   6) 사용중인 AI 모델
 *   7) 버전 및 업데이트  — 추가
 *   8) 회원 정보        — 추가, 플레이스홀더 (계정 시스템 없음)
 */
import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Monitor,
  Moon,
  Plus,
  RefreshCw,
  Smartphone,
  Sun,
  Type,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CURRENT_VERSION, checkForUpdate } from "@/lib/appUpdate";
import { FONT_SCALES, FONT_SCALE_LABELS } from "@/lib/fontScale";
import { setFontScale } from "@/lib/fontScaleManager";
import { THEME_LABELS, THEME_PREFERENCES } from "@/lib/theme";
import { setThemePreference } from "@/lib/themeManager";
import { disconnect as relayDisconnect } from "@/lib/relayManager";
import AlertDialog from "@/components/ui/dialog";
import Modal from "@/components/ui/modal";
import useAppStore from "@/store/appStore";
import useFontScaleStore from "@/store/fontScaleStore";
import useRelayStore from "@/store/relayStore";
import useThemeStore from "@/store/themeStore";

const THEME_ICONS = { system: Monitor, light: Sun, dark: Moon };

/**
 * QR 페어링 본문은 `RelayPairing`을 그대로 재사용한다 — QR 생성, TTL 카운트다운,
 * 재발급, 스토어 배지가 전부 거기 있고 `lib/pairingCountdown.js`와 물려 있다.
 * 여기서 다시 그리면 두 벌이 되고 프로토콜이 바뀔 때 한쪽만 고쳐진다.
 */
const RelayPairing = lazy(() => import("@/components/relay/RelayPairing"));

/** 섹션 카드 한 장 — 와이어프레임 1146 폭, 흰 카드 위 라벨 + 본문. */
function Section({ title, description, children, action }) {
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          {/* 프레임: 섹션 라벨 14px w400 #3D443C · 설명 14px w400 #B2B9B0.
              (`폰트 크기`만 18px w500인데 프레임 안에서도 이 한 장만 달라
               작업 흔적으로 보고 나머지에 맞췄다.) */}
          <h2 className="text-sm font-normal text-ink-body">{title}</h2>
          {description && <p className="mt-1 text-sm text-ink-faint">{description}</p>}
        </div>
        {action}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </section>
  );
}

/**
 * 알약 선택 버튼 — 폰트 크기·시스템 모드가 같은 모양을 쓴다.
 * 선택된 것에 체크 표시가 붙는다(와이어프레임 `tabler:check`).
 */
function PillOption({ icon: Icon, label, active, onClick }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={cn(
        // 프레임: 16px w500. 선택 #249000(≈--primary) on 연초록, 미선택 #B2B9B0.
        "flex items-center gap-2 rounded-lg border px-3.5 py-2 text-base font-medium transition-colors",
        active
          ? "border-primary/40 bg-accent text-primary"
          : "border-border text-ink-faint hover:bg-accent/50 hover:text-foreground"
      )}
    >
      {Icon && <Icon className="h-4 w-4 shrink-0" />}
      {label}
      {active && <Check className="h-4 w-4 shrink-0 text-primary" />}
    </button>
  );
}

/** 아직 붙일 백엔드가 없는 자리 — 왜 못 쓰는지 밝히고 비활성으로 둔다. */
function PlaceholderNote({ children }) {
  return (
    <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export default function PreferencesPage() {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const llmConfig = useAppStore((s) => s.llmConfig);

  const themePreference = useThemeStore((s) => s.preference);
  const themeResolved = useThemeStore((s) => s.resolved);
  const fontScale = useFontScaleStore((s) => s.scale);

  const relayConnected = useRelayStore((s) => s.connected);
  const relayUrl = useRelayStore((s) => s.relayUrl);

  const [update, setUpdate] = useState(null); // null | {status, version?, message?}
  const [checking, setChecking] = useState(false);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairedOpen, setPairedOpen] = useState(false);

  // 페어링 창을 띄워둔 동안 연결이 성사되면 QR을 닫고 성공 모달로 넘긴다
  // (와이어프레임 244:1599 → 250:6498). 이미 연결된 상태로 창을 열었을 때
  // 곧바로 성공 모달이 뜨지 않도록, 값이 실제로 뒤집힌 순간만 잡는다.
  const wasConnected = useRef(relayConnected);
  useEffect(() => {
    if (relayConnected && !wasConnected.current && pairOpen) {
      setPairOpen(false);
      setPairedOpen(true);
    }
    wasConnected.current = relayConnected;
  }, [relayConnected, pairOpen]);

  const handleCheckUpdate = async () => {
    setChecking(true);
    setUpdate(await checkForUpdate());
    setChecking(false);
  };

  return (
    <div className="mx-auto max-w-[1146px] space-y-6">
      {/* 프레임: 타이틀 24px w600 #0C1909 */}
      <h1 className="text-2xl font-semibold">환경 설정</h1>

      <div className="space-y-4">
        {/* 1. 내 요금제 ------------------------------------------------------ */}
        <Section
          title="내 요금제"
          action={
            <div className="flex shrink-0 items-center gap-3">
              {/* 프레임: 22px w600 */}
              <span className="text-[1.375rem] font-semibold leading-tight">Free</span>
              <Button size="sm" disabled title="요금제 기능은 준비 중입니다">
                업그레이드
              </Button>
            </div>
          }
        >
          <PlaceholderNote>
            결제·플랜 연동이 아직 없어 표시만 되는 자리입니다. 업그레이드 동선이
            정해지면 버튼을 연결합니다.
          </PlaceholderNote>
        </Section>

        {/* 2. 디바이스 추가 -------------------------------------------------- */}
        <Section
          title="디바이스 추가"
          description="디바이스를 추가해 다양한 장소에서 김대리를 사용해 보세요."
          action={
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => setPairOpen(true)}
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              추가
            </Button>
          }
        />

        {/* 3. 연결된 디바이스 ------------------------------------------------ */}
        <Section title="연결된 디바이스">
          {relayConnected ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
                  <Smartphone className="h-5 w-5 text-muted-foreground" />
                </div>
                <div className="min-w-0">
                  {/* 프레임: 기기명 16px w500 #0C1909 · 부제 12px w400 #B2B9B0 */}
                  <p className="truncate text-base font-medium text-foreground">모바일 기기</p>
                  {/* 기기 이름·접속 위치는 릴레이가 주지 않는다. 지어내지 않고
                      실제로 아는 값(중계 주소)만 보여준다. */}
                  <p className="truncate text-xs text-ink-faint">{relayUrl || "연결됨"}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setConfirmUnpair(true)}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"
                aria-label="이 기기 연결 해제"
                title="연결 해제"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center">
              <p className="text-sm text-foreground">연결된 디바이스가 없습니다.</p>
              <p className="mt-1 text-xs text-muted-foreground">
                디바이스를 추가해 다양한 장소에서 김대리를 사용해 보세요.
              </p>
            </div>
          )}
        </Section>

        {/* 4. 폰트 크기 ------------------------------------------------------ */}
        <Section title="폰트 크기">
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="폰트 크기">
            {FONT_SCALES.map((scale) => (
              <PillOption
                key={scale}
                icon={Type}
                label={FONT_SCALE_LABELS[scale]}
                active={fontScale === scale}
                onClick={() => setFontScale(scale)}
              />
            ))}
          </div>
        </Section>

        {/* 5. 시스템 모드 ---------------------------------------------------- */}
        <Section title="시스템 모드">
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="화면 테마">
            {THEME_PREFERENCES.map((pref) => (
              <PillOption
                key={pref}
                icon={THEME_ICONS[pref]}
                label={THEME_LABELS[pref]}
                active={themePreference === pref}
                onClick={() => setThemePreference(pref)}
              />
            ))}
          </div>
          {themePreference === "system" && (
            <p className="mt-2 text-xs text-muted-foreground">
              운영체제 설정에 따라 <b>{themeResolved === "dark" ? "다크" : "라이트"}</b>로
              보이고 있어요.
            </p>
          )}
        </Section>

        {/* 6. 사용중인 AI 모델 ------------------------------------------------ */}
        <Section title="사용중인 AI 모델">
          <button
            type="button"
            onClick={() => setCurrentPage("guide")}
            className="flex w-full items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-left transition-colors hover:bg-accent"
          >
            <span className="truncate text-sm font-medium">
              {llmConfig?.model || "선택된 모델 없음"}
            </span>
            <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
              모델 변경
              <ChevronRight className="h-4 w-4" />
            </span>
          </button>
          <PlaceholderNote>
            실제로 설치된 모델만 고를 수 있습니다. 목록에 없다면 로컬 AI 화면에서
            먼저 내려받아 주세요.
          </PlaceholderNote>
        </Section>

        {/* 7. 버전 및 업데이트 (추가) ----------------------------------------- */}
        <Section
          title="버전 및 업데이트"
          description={`현재 버전 v${CURRENT_VERSION}`}
          action={
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={handleCheckUpdate}
              disabled={checking}
            >
              {checking ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              )}
              업데이트 확인
            </Button>
          }
        >
          {update && (
            <p className="text-xs text-muted-foreground">
              {update.status === "latest" && "최신 버전을 사용 중입니다."}
              {update.status === "available" && (
                <span className="font-semibold text-primary">
                  새 버전 v{update.version} 이(가) 있습니다. 우측 상단 알림에서
                  설치할 수 있어요.
                </span>
              )}
              {update.status === "unsupported" &&
                "이 환경에서는 자동 업데이트를 확인할 수 없습니다 (설치본에서만 동작)."}
              {update.status === "error" && `확인에 실패했습니다 — ${update.message}`}
            </p>
          )}
        </Section>

        {/* 8. 회원 정보 (추가) ------------------------------------------------ */}
        <Section title="회원 정보">
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled title="계정 기능은 준비 중입니다">
              정보 변경
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled
              title="계정 기능은 준비 중입니다"
              className="text-destructive"
            >
              회원 탈퇴
            </Button>
          </div>
          <PlaceholderNote>
            김대리는 현재 로그인 없이 이 컴퓨터에서만 동작합니다. 계정 시스템이
            생기면 여기에서 정보 변경과 탈퇴를 할 수 있습니다.
          </PlaceholderNote>
        </Section>
      </div>

      {/* 디바이스 추가 — QR 페어링 (와이어프레임 244:1599) */}
      <Modal
        open={pairOpen}
        onClose={() => setPairOpen(false)}
        title="디바이스 추가"
        description="폰에서 김대리 앱을 열고 QR을 스캔하세요."
        size="md"
        icon={Smartphone}
      >
        <Suspense
          fallback={
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          }
        >
          <RelayPairing />
        </Suspense>
      </Modal>

      {/* 연결 성공 (와이어프레임 250:6498) */}
      <Modal
        open={pairedOpen}
        onClose={() => setPairedOpen(false)}
        title="기기가 연결됐습니다"
        size="sm"
        icon={CheckCircle2}
        footer={
          <>
            <span className="text-xs text-ink-subtle">환경 설정에서 언제든 해제할 수 있어요.</span>
            <Button size="sm" onClick={() => setPairedOpen(false)}>
              확인
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-ink-body">
          이제 밖에서도 폰으로 작업 진행 상황을 보고 승인 요청에 답할 수 있습니다.
        </p>
      </Modal>

      <AlertDialog
        open={confirmUnpair}
        title="연결 해제"
        description="이 기기의 연결을 해제하시겠습니까? 다시 사용하려면 QR로 새로 페어링해야 합니다."
        confirmLabel="해제"
        confirmVariant="destructive"
        onConfirm={() => {
          setConfirmUnpair(false);
          relayDisconnect();
        }}
        onCancel={() => setConfirmUnpair(false)}
      />
    </div>
  );
}
