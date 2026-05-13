import React, { useState, useEffect, useCallback } from "react";
import { MessageCircle, Play, Square, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { telegramStatus, telegramStart, telegramStop } from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

const COMMANDS = [
  { cmd: "/emails", desc: "최근 이메일 요약 (중요도 포함)" },
  { cmd: "/status", desc: "시스템 상태 확인" },
  { cmd: "/help", desc: "도움말" },
  { cmd: "텍스트 메시지", desc: "AI 채팅 (Ollama / Claude)" },
  { cmd: "파일 전송", desc: "파일 내용 AI 요약 (최대 1MB)" },
];

export default function TelegramModule() {
  const [running, setRunning] = useState(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [msgError, setMsgError] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const s = await telegramStatus();
      setRunning(s?.running ?? false);
    } catch {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const handleStart = async () => {
    setLoading(true);
    setMsgError(false);
    try {
      await telegramStart();
      setRunning(true);
      setMsg("봇이 시작되었습니다.");
    } catch (e) {
      setMsg(toUserMessage(e, "텔레그램 봇 시작에 실패했습니다. 자격증명을 확인해 주세요."));
      setMsgError(true);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    setMsgError(false);
    try {
      await telegramStop();
      setRunning(false);
      setMsg("봇이 중지되었습니다.");
    } catch (e) {
      setMsg(toUserMessage(e, "봇 중지에 실패했습니다."));
      setMsgError(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">텔레그램 봇</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          텔레그램으로 어디서든 메일 확인 및 AI 채팅
        </p>
      </div>

      {/* Bot status + controls */}
      <Card>
        <CardContent className="flex items-center justify-between py-4">
          <div className="flex items-center gap-3">
            <MessageCircle className="h-5 w-5 text-primary" />
            <div>
              <p className="text-sm font-medium">봇 상태</p>
              <p className="text-xs text-muted-foreground">
                {running === null ? "확인 중..." : running ? "실행 중" : "중지됨"}
              </p>
            </div>
            {running !== null && (
              <Badge variant={running ? "success" : "secondary"}>
                {running ? "실행 중" : "중지됨"}
              </Badge>
            )}
          </div>
          <div className="flex gap-2">
            {!running && (
              <Button size="sm" onClick={handleStart} disabled={loading}>
                {loading ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <Play className="mr-1 h-3 w-3" />
                )}
                봇 시작
              </Button>
            )}
            {running && (
              <Button
                size="sm"
                variant="destructive"
                onClick={handleStop}
                disabled={loading}
              >
                {loading ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <Square className="mr-1 h-3 w-3" />
                )}
                봇 중지
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {msg && (
        <p className={`text-xs ${msgError ? "text-destructive" : "text-muted-foreground"}`}>
          {msg}
        </p>
      )}

      {/* Setup guide */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">설정 방법</CardTitle>
          <CardDescription>
            처음 사용하는 경우 아래 순서대로 설정하세요.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-3">
          <div className="space-y-1">
            <p>
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary mr-2">1</span>
              텔레그램에서{" "}
              <strong className="text-foreground">@BotFather</strong>에게{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">/newbot</code>
              을 보내 봇을 생성하세요.
            </p>
          </div>

          <div className="space-y-1">
            <p>
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary mr-2">2</span>
              받은 봇 토큰을{" "}
              <strong className="text-foreground">자격증명 관리</strong>에서{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">telegram_bot_token</code>
              으로 저장하세요.
            </p>
          </div>

          <div className="space-y-1">
            <p>
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary mr-2">3</span>
              본인의 텔레그램 Chat ID를{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">telegram_chat_id</code>
              로 저장하세요.
            </p>
            {/* Concrete Chat ID guidance for non-developers */}
            <Card className="ml-7 border-muted bg-muted/30">
              <CardContent className="py-3 text-xs space-y-1.5">
                <p className="font-medium text-foreground">Chat ID 확인 방법</p>
                <p>
                  방법 1 (가장 쉬움): 텔레그램에서{" "}
                  <strong className="text-foreground">@userinfobot</strong>에게 아무 메시지나 보내면
                  본인의 Chat ID를 즉시 알려줍니다.
                </p>
                <p>
                  방법 2: 생성한 봇에게 아무 메시지를 보낸 뒤 브라우저에서{" "}
                  <code className="rounded bg-muted px-1">
                    https://api.telegram.org/bot<span className="text-primary">[토큰]</span>/getUpdates
                  </code>
                  을 열면 <code className="rounded bg-muted px-1">"id"</code> 값이 Chat ID입니다.
                </p>
              </CardContent>
            </Card>
          </div>
        </CardContent>
      </Card>

      {/* Available commands */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">사용 가능한 명령어</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <tbody className="divide-y">
              {COMMANDS.map(({ cmd, desc }) => (
                <tr key={cmd}>
                  <td className="py-2 pr-4 font-mono text-xs text-primary">
                    {cmd}
                  </td>
                  <td className="py-2 text-muted-foreground">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
