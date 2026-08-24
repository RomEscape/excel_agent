/**
 * PreferencesPage — 환경 설정 (와이어프레임 `243:1140`).
 *
 * 탭 허브(`SettingsHub`)가 아니라 **섹션 카드를 세로로 쌓은 단일 페이지**다.
 * 사이드바 푸터의 `환경 설정`이 여기로 온다(페이지 키 `preferences`).
 *
 * SettingsHub는 지우지 않았다 — 자격증명·보안·허용 범위·실행 기록·로컬 AI는
 * 와이어프레임에 없지만 실제 기능이 붙어 있고, `Cmd/Ctrl+K` 명령 팔레트가
 * 그 탭 키(`credentials` 등)로 직접 진입시킨다.
 *
 * 치수는 Figma export에서 그대로 옮겼다. 틀리기 쉬운 지점:
 *   - **카드 안은 세로 쌓기다** — 라벨이 위, 내용이 아래(gap 12). 라벨 왼쪽 /
 *     값 오른쪽으로 배치하면 프레임과 전혀 다른 화면이 된다.
 *   - **시스템 모드 3개는 세로 목록**이지 가로 알약이 아니다.
 *   - **폰트 크기만 세그먼트 컨트롤**(바깥 radius 28 테두리 안에 칩 두 개).
 *   - 섹션 라벨은 14px Regular인데 `폰트 크기` 한 장만 18px Medium이다(프레임 그대로).
 */
import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  Loader2,
  Plus,
  RefreshCw,
  Smartphone,
  Type,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { CURRENT_VERSION, checkForUpdate } from "@/lib/appUpdate";
import { FONT_SCALES, FONT_SCALE_LABELS } from "@/lib/fontScale";
import { setFontScale } from "@/lib/fontScaleManager";
import { describeModel } from "@/lib/modelCatalog";
import { THEME_LABELS, THEME_PREFERENCES } from "@/lib/theme";
import { setThemePreference } from "@/lib/themeManager";
import { disconnect as relayDisconnect } from "@/lib/relayManager";
import AlertDialog from "@/components/ui/dialog";
import Modal from "@/components/ui/modal";
import useAppStore from "@/store/appStore";
import useFontScaleStore from "@/store/fontScaleStore";
import useRelayStore from "@/store/relayStore";
import useThemeStore from "@/store/themeStore";

/**
 * QR 페어링 본문은 `RelayPairing`을 그대로 재사용한다 — QR 생성, TTL 카운트다운,
 * 재발급, 스토어 배지가 전부 거기 있고 `lib/pairingCountdown.js`와 물려 있다.
 */
const RelayPairing = lazy(() => import("@/components/relay/RelayPairing"));

/**
 * 섹션 카드 — Figma: padding 20/16, radius 12, 1px #E1E6DF, 내부 gap 12.
 * `big`은 `폰트 크기` 한 장만 쓰는 18px Medium 라벨(프레임의 예외).
 */
function Section({ title, big = false, children }) {
  return (
    <section className="flex w-full flex-col gap-3 rounded-xl border border-border bg-card px-5 py-4">
      <h2
        className={cn(
          "text-ink-body",
          big ? "text-lg font-medium leading-6" : "text-sm font-normal leading-5"
        )}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

/** 채운 알약 버튼 — Figma: minWidth 80, px20 py8, radius 21, 12px 글자. */
function PillButton({ children, onClick, disabled, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="inline-flex min-w-[80px] items-center justify-center rounded-[21px] bg-brand px-5 py-2 text-xs leading-4 text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

/** 테두리 알약 버튼 — Figma: bg #F9FDF7, 0.5px #2DB400, radius 16, 12px 글자. */
function OutlinePill({ icon: Icon, children, onClick, disabled, title, tone = "brand" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "inline-flex min-w-[80px] items-center justify-center gap-1 rounded-2xl border-[0.5px] py-2 pl-4 pr-5 text-xs leading-4 transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        tone === "brand"
          ? "border-brand bg-secondary text-brand hover:bg-accent"
          : "border-border bg-card text-ink-subtle hover:bg-accent"
      )}
    >
      {Icon && <Icon className="h-3 w-3 shrink-0" />}
      {children}
    </button>
  );
}

/** 아직 붙일 백엔드가 없는 자리 — 왜 못 쓰는지 밝히고 비활성으로 둔다. */
function PlaceholderNote({ children }) {
  return (
    <p className="flex items-start gap-1.5 text-xs leading-4 text-ink-faint">
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

  const [update, setUpdate] = useState(null);
  const [checking, setChecking] = useState(false);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairedOpen, setPairedOpen] = useState(false);

  // 페어링 창을 띄워둔 동안 연결이 성사되면 QR을 닫고 성공 모달로 넘긴다
  // (244:1599 → 250:6498). 이미 연결된 상태로 창을 열었을 때 성공 모달이
  // 곧바로 뜨지 않도록, 값이 실제로 뒤집힌 순간만 잡는다.
  const wasConnected = useRef(relayConnected);
  useEffect(() => {
    if (relayConnected && !wasConnected.current && pairOpen) {
      setPairOpen(false);
      setPairedOpen(true);
    }
    wasConnected.current = relayConnected;
  }, [relayConnected, pairOpen]);

  // 지금 고른 모델이 추천 모델인지 — 배지 표시 판정용.
  const model = describeModel(llmConfig?.model);

  const handleCheckUpdate = async () => {
    setChecking(true);
    setUpdate(await checkForUpdate());
    setChecking(false);
  };

  return (
    <div className="mx-auto flex max-w-[1146px] flex-col gap-3 pb-6 pt-3">
      {/* Figma: 24px SemiBold #0C1909, leading 32 */}
      <h1 className="text-2xl font-semibold leading-8 text-foreground">환경 설정</h1>

      {/* 1. 내 요금제 ------------------------------------------------------ */}
      <Section title="내 요금제">
        <div className="flex items-center gap-3">
          {/* 22px SemiBold */}
          <span className="text-[22px] font-semibold leading-[30px] text-foreground">Free</span>
          <PillButton disabled title="요금제 기능은 준비 중입니다">
            업그레이드
          </PillButton>
        </div>
        <PlaceholderNote>
          결제·플랜 연동이 아직 없어 표시만 되는 자리입니다.
        </PlaceholderNote>
      </Section>

      {/* 2. 디바이스 추가 -------------------------------------------------- */}
      <Section title="디바이스 추가">
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm leading-5 text-ink-faint">
            디바이스를 추가해 다양한 장소에서 김대리를 사용해 보세요.
          </p>
          <OutlinePill icon={Plus} onClick={() => setPairOpen(true)}>
            추가
          </OutlinePill>
        </div>
      </Section>

      {/* 3. 연결된 디바이스 ------------------------------------------------ */}
      <Section title="연결된 디바이스">
        {relayConnected ? (
          <div className="flex w-[372px] max-w-full items-start justify-between">
            <div className="flex items-center gap-2">
              {/* Figma: #F6F7F5 지면, radius 7, padding 7, 아이콘 28 */}
              <span className="flex shrink-0 items-center rounded-[7px] bg-muted p-[7px]">
                <Smartphone className="h-7 w-7 text-ink-subtle" />
              </span>
              <div className="flex min-w-0 flex-col justify-center gap-1">
                <p className="truncate text-base font-medium leading-[22px] tracking-[-0.64px] text-foreground">
                  모바일 기기
                </p>
                {/* 기기 이름·접속 위치는 릴레이가 주지 않는다. 지어내지 않고
                    실제로 아는 값(중계 주소)만 보여준다. */}
                <p className="truncate text-xs leading-4 text-ink-faint">
                  {relayUrl || "연결됨"}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setConfirmUnpair(true)}
              className="flex h-8 w-8 shrink-0 items-center justify-center text-ink-subtle transition-colors hover:text-destructive"
              aria-label="이 기기 연결 해제"
              title="연결 해제"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        ) : (
          <p className="text-sm leading-5 text-ink-faint">연결된 디바이스가 없습니다.</p>
        )}
      </Section>

      {/* 4. 폰트 크기 — 프레임에서 유일하게 라벨이 18px Medium인 카드 -------- */}
      <Section title="폰트 크기" big>
        {/* Figma: 바깥 radius 28, 1px #CACFC7, padding 4 / 안쪽 칩 radius 29 */}
        <div
          className="inline-flex w-fit items-start rounded-[28px] border border-ink-disabled bg-card p-1"
          role="radiogroup"
          aria-label="폰트 크기"
        >
          {FONT_SCALES.map((scale) => {
            const active = fontScale === scale;
            return (
              <button
                key={scale}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setFontScale(scale)}
                className={cn(
                  "flex items-center gap-1 rounded-[29px] py-2 pl-3 pr-4 text-base font-medium leading-[22px] tracking-[-0.64px] transition-colors",
                  active ? "bg-brand-soft text-primary" : "text-ink-faint hover:text-foreground"
                )}
              >
                <Type className={cn("h-5 w-5 shrink-0", scale === "large" && "h-6 w-6")} />
                {FONT_SCALE_LABELS[scale]}
              </button>
            );
          })}
        </div>
      </Section>

      {/* 5. 시스템 모드 — 세로 목록 ----------------------------------------- */}
      <Section title="시스템 모드">
        <div className="flex flex-col items-start" role="radiogroup" aria-label="화면 테마">
          {THEME_PREFERENCES.map((pref) => {
            const active = themePreference === pref;
            return (
              <button
                key={pref}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setThemePreference(pref)}
                className={cn(
                  "flex items-center gap-9 rounded-[29px] px-3 py-2 text-base font-medium leading-[22px] tracking-[-0.64px] transition-colors",
                  active ? "text-primary" : "text-ink-faint hover:text-foreground"
                )}
              >
                {THEME_LABELS[pref]}
                {active && <Check className="h-5 w-5 shrink-0 text-primary" />}
              </button>
            );
          })}
        </div>
        {themePreference === "system" && (
          <p className="text-xs leading-4 text-ink-faint">
            운영체제 설정에 따라 <b>{themeResolved === "dark" ? "다크" : "라이트"}</b>로
            보이고 있어요.
          </p>
        )}
      </Section>

      {/* 6. 사용중인 AI 모델 ------------------------------------------------ */}
      <Section title="사용중인 AI 모델">
        {/* Figma: bg #F6F7F5, radius 4, px12 py8, width 441 */}
        <button
          type="button"
          onClick={() => setCurrentPage("guide")}
          className="flex w-[441px] max-w-full items-center justify-between rounded bg-muted px-3 py-2 text-left transition-colors hover:bg-accent"
        >
          <span className="truncate text-sm leading-5 text-foreground">
            {llmConfig?.model || "선택된 모델 없음"}
          </span>
          <span className="flex shrink-0 items-center gap-2">
            {/* 배지는 실제로 추천 모델일 때만 붙인다 — 어떤 모델을 골라도 `추천`이
                떠 있으면 배지가 아무 말도 하지 않는 셈이다. 판정은
                `lib/modelCatalog.js`가 소유한다(온보딩 셀렉트와 같은 규칙). */}
            {model.recommended && (
              <span className="text-xs leading-4 text-brand-step">추천</span>
            )}
            <ChevronDown className="h-6 w-6 text-ink-subtle" />
          </span>
        </button>
      </Section>

      {/* 7. 버전 및 업데이트 (협의로 추가) ----------------------------------- */}
      <Section title="버전 및 업데이트">
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm leading-5 text-ink-faint">현재 버전 v{CURRENT_VERSION}</p>
          <OutlinePill
            icon={checking ? Loader2 : RefreshCw}
            onClick={handleCheckUpdate}
            disabled={checking}
          >
            업데이트 확인
          </OutlinePill>
          {update && (
            <p className="text-xs leading-4 text-ink-faint">
              {update.status === "latest" && "최신 버전을 사용 중입니다."}
              {update.status === "available" && (
                <span className="font-semibold text-primary">
                  새 버전 v{update.version} 이(가) 있습니다. 우측 상단 알림에서 설치할 수 있어요.
                </span>
              )}
              {update.status === "unsupported" &&
                "이 환경에서는 자동 업데이트를 확인할 수 없습니다 (설치본에서만 동작)."}
              {update.status === "error" && `확인에 실패했습니다 — ${update.message}`}
            </p>
          )}
        </div>
      </Section>

      {/* 8. 회원 정보 (협의로 추가) ------------------------------------------ */}
      <Section title="회원 정보">
        <div className="flex flex-wrap items-start gap-2">
          <OutlinePill tone="neutral" disabled title="계정 기능은 준비 중입니다">
            정보 변경
          </OutlinePill>
          <OutlinePill tone="neutral" disabled title="계정 기능은 준비 중입니다">
            회원 탈퇴
          </OutlinePill>
        </div>
        <PlaceholderNote>
          김대리는 현재 로그인 없이 이 컴퓨터에서만 동작합니다.
        </PlaceholderNote>
      </Section>

      {/* 디바이스 추가 — QR 페어링 (244:1599) */}
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

      {/* 연결 성공 (250:6498) */}
      <Modal
        open={pairedOpen}
        onClose={() => setPairedOpen(false)}
        title="기기가 연결됐습니다"
        size="sm"
        icon={CheckCircle2}
        footer={
          <>
            <span className="text-xs text-ink-subtle">환경 설정에서 언제든 해제할 수 있어요.</span>
            <PillButton onClick={() => setPairedOpen(false)}>확인</PillButton>
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
