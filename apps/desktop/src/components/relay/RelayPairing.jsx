/**
 * 모바일 연결(QR 페어링) 화면.
 *
 * 상태는 `store/relayStore`, 액션은 `lib/relayManager`가 소유한다 — 이 컴포넌트는 조합만.
 * QR 페이로드는 relayManager.buildQrPayload가 모바일 파서와 동일 형태로 만든다.
 */
import { useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Loader2, Smartphone, Unplug } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  buildQrPayload,
  disconnect,
  pollUntilConnected,
  refreshStatus,
  startPairing,
} from "@/lib/relayManager";
import useRelayStore from "@/store/relayStore";

export default function RelayPairing() {
  const phase = useRelayStore((s) => s.phase);
  const pairing = useRelayStore((s) => s.pairing);
  const connected = useRelayStore((s) => s.connected);
  const lastError = useRelayStore((s) => s.lastError);
  const [busy, setBusy] = useState(false);
  const stopPoll = useRef(null);

  // 진입 시 현재 연동 상태 1회 조회, 이탈 시 폴링 정리
  useEffect(() => {
    refreshStatus();
    return () => stopPoll.current?.();
  }, []);

  const onPair = async () => {
    setBusy(true);
    try {
      await startPairing();
      stopPoll.current?.();
      stopPoll.current = pollUntilConnected();
    } catch {
      // 실패는 store.lastError로 표시된다
    } finally {
      setBusy(false);
    }
  };

  const onDisconnect = async () => {
    setBusy(true);
    stopPoll.current?.();
    await disconnect();
    setBusy(false);
  };

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
          <div className="flex flex-col items-center gap-3">
            {/* QR은 스캔 대비 항상 흰 배경 위에 렌더 (다크모드 대응) */}
            <div className="rounded-lg bg-white p-4">
              <QRCodeSVG value={buildQrPayload(pairing)} size={200} />
            </div>
            <p className="text-sm text-muted-foreground">
              폰 앱에서 <b>QR로 데스크톱 연결</b>을 눌러 이 코드를 스캔하세요.
            </p>
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              스캔 대기 중… (코드 {pairing.code})
            </p>
          </div>
        ) : (
          <Button onClick={onPair} disabled={busy || phase === "pairing"}>
            {busy || phase === "pairing" ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Smartphone className="mr-2 h-4 w-4" />
            )}
            모바일 연결 시작
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
