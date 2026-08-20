/**
 * 모바일 연결(QR 페어링) 화면 — 최종 와이어프레임 B-4 (Frame 167 / 다크 153).
 *
 * 구버전 대비 추가된 것:
 *   - 페어링 코드 복사 필드 (334×44)
 *   - `입력 가능 시간 3:29` 카운트다운 + 재발급 버튼
 *   - Google Play / App Store 배지
 *
 * 카운트다운이 필요한 이유: relay의 페어링 code는 TTL(기본 120초)로 만료되는데,
 * 표시가 없으면 사용자는 이유 없는 페어링 실패만 보게 된다. 남은 시간은
 * `/relay/pair`가 준 `expires_in`에서 온다 — 앱이 TTL을 추측하지 않는다.
 *
 * 상태는 `store/relayStore`, 액션은 `lib/relayManager`, 시간 계산은
 * `lib/pairingCountdown`이 소유한다 — 이 컴포넌트는 조합만.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Check, Copy, Loader2, RotateCw, Smartphone, Unplug } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { formatCountdown, isExpired, remainingSeconds } from "@/lib/pairingCountdown";
import {
  buildQrPayload,
  disconnect,
  pollUntilConnected,
  refreshStatus,
  startPairing,
} from "@/lib/relayManager";
import useRelayStore from "@/store/relayStore";

/** 스토어 배지 — 와이어프레임 Frame 156 (Google Play 153×48 · App Store 139×48). */
const STORE_LINKS = [
  { id: "play", label: "Google Play", sub: "GET IT ON", url: "https://play.google.com/store" },
  { id: "appstore", label: "App Store", sub: "Download on the", url: "https://apps.apple.com" },
];

function StoreBadge({ label, sub, url }) {
  const open = async () => {
    try {
      const { open: shellOpen } = await import("@tauri-apps/plugin-shell");
      await shellOpen(url);
    } catch {
      window.open(url, "_blank");
    }
  };
  return (
    <button
      type="button"
      onClick={open}
      className="flex h-12 items-center gap-2.5 rounded-lg border border-border bg-card px-4 transition-colors hover:border-primary/50"
    >
      <Smartphone className="h-5 w-5 shrink-0 text-foreground" />
      <span className="flex flex-col items-start leading-tight">
        <span className="text-[9px] uppercase tracking-wide text-muted-foreground">{sub}</span>
        <span className="text-xs font-semibold text-foreground">{label}</span>
      </span>
    </button>
  );
}

export default function RelayPairing() {
  const phase = useRelayStore((s) => s.phase);
  const pairing = useRelayStore((s) => s.pairing);
  const pairingExpiresAt = useRelayStore((s) => s.pairingExpiresAt);
  const connected = useRelayStore((s) => s.connected);
  const relayUrl = useRelayStore((s) => s.relayUrl);
  const lastError = useRelayStore((s) => s.lastError);

  const [busy, setBusy] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [copied, setCopied] = useState(false);
  // 1초마다 흐르는 현재 시각 — 카운트다운은 이 값에서 파생된다.
  const [now, setNow] = useState(() => Date.now());
  const stopPoll = useRef(null);

  // 진입 시 현재 연동 상태 1회 조회, 이탈 시 폴링 정리
  useEffect(() => {
    refreshStatus();
    return () => stopPoll.current?.();
  }, []);

  // 저장된 relay 주소를 입력칸에 채운다 (사용자가 편집 중이면 덮지 않음)
  useEffect(() => {
    if (relayUrl && !urlInput) setUrlInput(relayUrl);
  }, [relayUrl, urlInput]);

  // 카운트다운 틱 — QR이 떠 있고 만료 시각이 있을 때만 돈다.
  // 항상 돌리면 이 화면을 열어둔 내내 1초마다 리렌더가 난다.
  useEffect(() => {
    if (phase !== "waiting" || !pairingExpiresAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [phase, pairingExpiresAt]);

  const onPair = useCallback(async () => {
    setBusy(true);
    try {
      await startPairing(urlInput.trim() || undefined);
      stopPoll.current?.();
      stopPoll.current = pollUntilConnected();
    } catch {
      // 실패는 store.lastError로 표시된다
    } finally {
      setBusy(false);
    }
  }, [urlInput]);

  const onDisconnect = async () => {
    setBusy(true);
    stopPoll.current?.();
    await disconnect();
    setBusy(false);
  };

  const onCopyCode = () => {
    if (!pairing?.code) return;
    navigator.clipboard?.writeText(pairing.code).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => {}
    );
  };

  const left = remainingSeconds(pairingExpiresAt, now);
  const expired = isExpired(pairingExpiresAt, now);
  // 마지막 30초는 붉게 — 남은 시간이 얼마 없다는 걸 숫자만으로는 잘 못 읽는다.
  const urgent = left !== null && left <= 30 && !expired;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Smartphone className="h-4 w-4" />
              모바일 연결
            </CardTitle>
            <CardDescription>
              폰으로 이 데스크톱의 에이전트를 원격 조종합니다. 대화·파일은 이 PC를 떠나지
              않고, 중계 서버는 암호화된 제어 신호만 전달합니다.
            </CardDescription>
          </div>
          {connected ? <Badge>연결됨</Badge> : <Badge variant="secondary">미연결</Badge>}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {lastError && <p className="text-sm text-destructive">{lastError}</p>}

        {connected ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-muted-foreground">
              모바일이 연결돼 있습니다. 폰 채팅으로 작업을 시킬 수 있어요.
            </p>
            <Button variant="outline" onClick={onDisconnect} disabled={busy}>
              <Unplug className="mr-2 h-4 w-4" />
              연결 해제
            </Button>
          </div>
        ) : phase === "waiting" && pairing ? (
          <div className="flex flex-col items-center gap-4">
            {/* QR 160×160 — 스캔 대비 항상 흰 배경 위에 렌더 (다크모드 대응).
                만료되면 흐리게 눕혀서 "이건 이제 안 된다"를 먼저 보이게 한다. */}
            <div className={cn("rounded-lg bg-white p-4", expired && "opacity-30")}>
              <QRCodeSVG value={buildQrPayload(pairing)} size={180} />
            </div>

            {/* 페어링 코드 복사 필드 — 와이어프레임 Frame 70 (334×44) */}
            <div className="flex w-full max-w-[21rem] items-center gap-2 rounded-lg border border-border bg-card px-3 py-2.5">
              <code className="min-w-0 flex-1 truncate text-xs text-foreground" title={pairing.code}>
                {pairing.code}
              </code>
              <button
                type="button"
                onClick={onCopyCode}
                className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
                aria-label="페어링 코드 복사"
                title="페어링 코드 복사"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-primary" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>

            {/* TTL 카운트다운 + 재발급 — 와이어프레임 Frame 145 */}
            {left !== null && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-muted-foreground">입력 가능 시간</span>
                <span
                  className={cn(
                    "font-semibold tabular-nums",
                    expired
                      ? "text-destructive"
                      : urgent
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-foreground"
                  )}
                >
                  {expired ? "만료됨" : formatCountdown(left)}
                </span>
                <button
                  type="button"
                  onClick={onPair}
                  disabled={busy}
                  className="rounded p-1 text-primary transition-colors hover:bg-accent disabled:opacity-50"
                  aria-label="페어링 코드 재발급"
                  title="페어링 코드 재발급"
                >
                  <RotateCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
                </button>
              </div>
            )}

            {expired ? (
              <p className="text-xs text-destructive">
                코드가 만료됐습니다. 위 재발급 버튼을 눌러 새 QR을 받으세요.
              </p>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  폰 앱에서 <b>QR로 데스크톱 연결</b>을 눌러 이 코드를 스캔하세요.
                </p>
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  스캔 대기 중…
                </p>
              </>
            )}

            {/* 앱 설치 안내 + 스토어 배지 — 와이어프레임 Frame 157 */}
            <div className="flex flex-col items-center gap-3 border-t border-border pt-4">
              <p className="text-center text-xs text-muted-foreground">
                아직 김대리 앱이 없으신가요?
                <br />
                김대리를 다운받고 더 많은 기능을 사용해보세요.
              </p>
              <div className="flex gap-2">
                {STORE_LINKS.map((s) => (
                  <StoreBadge key={s.id} {...s} />
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="relay-url">중계 서버 주소</Label>
              <Input
                id="relay-url"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder="http://127.0.0.1:8787"
              />
              <p className="text-xs text-muted-foreground">
                실제 폰으로 테스트하려면 이 데스크톱의 <b>LAN IP</b>를 넣으세요 (예:{" "}
                <code>http://192.168.0.12:8787</code>). QR에 이 주소가 그대로 실리는데{" "}
                <code>127.0.0.1</code>은 폰에겐 자기 자신이라 연결되지 않습니다.
              </p>
            </div>
            <Button onClick={onPair} disabled={busy || phase === "pairing"}>
              {busy || phase === "pairing" ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Smartphone className="mr-2 h-4 w-4" />
              )}
              모바일 연결 시작
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
