/**
 * MessengerSettings.jsx — Phase 3 멀티 메신저 설정 UI.
 *
 * 텔레그램 / 슬랙 / 디스코드 각각 설정 카드.
 * - 연결 상태 표시 (녹색/빨간 dot)
 * - 각 메신저별 토큰/ID 입력 + 저장 버튼
 * - 시작/중지 버튼
 */

import React, { useState, useEffect, useCallback } from "react";
import {
  MessageCircle,
  RefreshCw,
  Check,
  X,
  Eye,
  EyeOff,
  Play,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { invoke } from "@tauri-apps/api/core";
import { parseResponse } from "@/lib/api";

// ── API helpers ───────────────────────────────────────────────────────────────

async function call(cmd, args = {}) {
  const raw = await invoke(cmd, args);
  return parseResponse(raw);
}

// ── 연결 상태 dot ─────────────────────────────────────────────────────────────

function StatusDot({ running }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${
        running ? "bg-green-500" : "bg-red-400"
      }`}
      title={running ? "연결됨" : "미연결"}
    />
  );
}

// ── 비밀번호 입력 토글 ────────────────────────────────────────────────────────

function TokenInput({ value, onChange, placeholder, id }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="pr-9 font-mono text-sm"
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        tabIndex={-1}
      >
        {visible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

// ── 텔레그램 카드 ─────────────────────────────────────────────────────────────

function TelegramCard() {
  const [status, setStatus] = useState({ running: false });
  const [token, setToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    try {
      const s = await call("telegram_status");
      setStatus(s);
    } catch {}
  }, []);

  useEffect(() => {
    loadStatus();
    const timer = setInterval(loadStatus, 10000);
    return () => clearInterval(timer);
  }, [loadStatus]);

  const handleSetup = async () => {
    if (!token.trim()) {
      setMsg({ type: "error", text: "봇 토큰을 입력하세요." });
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      const result = await call("telegram_setup", {
        token: token.trim(),
        chat_id: chatId.trim() || null,
      });
      if (result.ok) {
        setMsg({ type: "success", text: `연결 성공: ${result.bot_username}` });
        await loadStatus();
      } else {
        setMsg({ type: "error", text: result.error || "연결 실패" });
      }
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async () => {
    setLoading(true);
    try {
      if (status.running) {
        await call("telegram_stop");
      } else {
        await call("telegram_start");
      }
      await loadStatus();
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageCircle className="h-4 w-4 text-blue-500" />
          텔레그램
          <StatusDot running={status.running} />
          <span className="ml-auto text-xs text-muted-foreground font-normal">
            {status.running ? "실행 중" : "중지됨"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="tg-token" className="text-xs">Bot Token</Label>
          <TokenInput
            id="tg-token"
            value={token}
            onChange={setToken}
            placeholder="1234567890:ABCDEFghijklmn..."
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="tg-chatid" className="text-xs">Chat ID (숫자)</Label>
          <Input
            id="tg-chatid"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            placeholder="123456789"
            className="font-mono text-sm"
          />
        </div>
        {msg && (
          <p className={`text-xs ${msg.type === "error" ? "text-destructive" : "text-green-600"}`}>
            {msg.text}
          </p>
        )}
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={handleSetup} disabled={loading} className="flex-1">
            저장 및 테스트
          </Button>
          <Button
            size="sm"
            onClick={handleToggle}
            disabled={loading}
            variant={status.running ? "destructive" : "default"}
          >
            {status.running ? (
              <><Square className="h-3 w-3 mr-1" />중지</>
            ) : (
              <><Play className="h-3 w-3 mr-1" />시작</>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 슬랙 카드 ─────────────────────────────────────────────────────────────────

function SlackCard() {
  const [status, setStatus] = useState({ running: false, configured: false });
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    try {
      const s = await call("slack_status");
      setStatus(s);
    } catch {}
  }, []);

  useEffect(() => {
    loadStatus();
    const timer = setInterval(loadStatus, 10000);
    return () => clearInterval(timer);
  }, [loadStatus]);

  const handleSetup = async () => {
    if (!botToken.trim() || !appToken.trim()) {
      setMsg({ type: "error", text: "Bot Token과 App Token을 모두 입력하세요." });
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      const result = await call("slack_setup", {
        bot_token: botToken.trim(),
        app_token: appToken.trim(),
      });
      if (result.ok) {
        setMsg({ type: "success", text: `연결 성공: ${result.team}` });
        await loadStatus();
      } else {
        setMsg({ type: "error", text: result.error || "연결 실패" });
      }
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async () => {
    setLoading(true);
    try {
      if (status.running) {
        await call("slack_stop");
      } else {
        await call("slack_start");
      }
      await loadStatus();
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <span className="text-[#4A154B] font-bold text-sm">#</span>
          슬랙 (Slack)
          <StatusDot running={status.running} />
          <span className="ml-auto text-xs text-muted-foreground font-normal">
            {status.running ? "실행 중" : status.configured ? "설정됨" : "미설정"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="sl-bot" className="text-xs">Bot Token (xoxb-...)</Label>
          <TokenInput
            id="sl-bot"
            value={botToken}
            onChange={setBotToken}
            placeholder="xoxb-xxxxxxxxxxxx-xxxx"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sl-app" className="text-xs">App Token (xapp-...)</Label>
          <TokenInput
            id="sl-app"
            value={appToken}
            onChange={setAppToken}
            placeholder="xapp-1-xxxxxxxxxx-xxxx"
          />
        </div>
        {msg && (
          <p className={`text-xs ${msg.type === "error" ? "text-destructive" : "text-green-600"}`}>
            {msg.text}
          </p>
        )}
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={handleSetup} disabled={loading} className="flex-1">
            저장 및 테스트
          </Button>
          <Button
            size="sm"
            onClick={handleToggle}
            disabled={loading || !status.configured}
            variant={status.running ? "destructive" : "default"}
          >
            {status.running ? (
              <><Square className="h-3 w-3 mr-1" />중지</>
            ) : (
              <><Play className="h-3 w-3 mr-1" />시작</>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 디스코드 카드 ─────────────────────────────────────────────────────────────

function DiscordCard() {
  const [status, setStatus] = useState({ running: false, configured: false });
  const [token, setToken] = useState("");
  const [guildId, setGuildId] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null);

  const loadStatus = useCallback(async () => {
    try {
      const s = await call("discord_status");
      setStatus(s);
    } catch {}
  }, []);

  useEffect(() => {
    loadStatus();
    const timer = setInterval(loadStatus, 10000);
    return () => clearInterval(timer);
  }, [loadStatus]);

  const handleSetup = async () => {
    if (!token.trim()) {
      setMsg({ type: "error", text: "Bot Token을 입력하세요." });
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      const result = await call("discord_setup", {
        token: token.trim(),
        allowed_guild_id: guildId.trim() || null,
      });
      if (result.ok) {
        setMsg({ type: "success", text: `연결 성공: ${result.bot_username}` });
        await loadStatus();
      } else {
        setMsg({ type: "error", text: result.error || "연결 실패" });
      }
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async () => {
    setLoading(true);
    try {
      if (status.running) {
        await call("discord_stop");
      } else {
        await call("discord_start");
      }
      await loadStatus();
    } catch (e) {
      setMsg({ type: "error", text: `오류: ${e}` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <span className="text-[#5865F2] font-bold text-sm">DC</span>
          디스코드 (Discord)
          <StatusDot running={status.running} />
          <span className="ml-auto text-xs text-muted-foreground font-normal">
            {status.running ? "실행 중" : status.configured ? "설정됨" : "미설정"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="dc-token" className="text-xs">Bot Token</Label>
          <TokenInput
            id="dc-token"
            value={token}
            onChange={setToken}
            placeholder="MTxxxxxxxxxxxxxxxxxxxxxxxx.Gxxxxx.xx"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="dc-guild" className="text-xs">
            서버 ID (Guild ID, 선택)
            <span className="text-muted-foreground ml-1">— 미입력 시 모든 서버 허용</span>
          </Label>
          <Input
            id="dc-guild"
            value={guildId}
            onChange={(e) => setGuildId(e.target.value)}
            placeholder="123456789012345678"
            className="font-mono text-sm"
          />
        </div>
        {msg && (
          <p className={`text-xs ${msg.type === "error" ? "text-destructive" : "text-green-600"}`}>
            {msg.text}
          </p>
        )}
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={handleSetup} disabled={loading} className="flex-1">
            저장 및 테스트
          </Button>
          <Button
            size="sm"
            onClick={handleToggle}
            disabled={loading || !status.configured}
            variant={status.running ? "destructive" : "default"}
          >
            {status.running ? (
              <><Square className="h-3 w-3 mr-1" />중지</>
            ) : (
              <><Play className="h-3 w-3 mr-1" />시작</>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

export default function MessengerSettings() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold">메신저 설정</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          텔레그램, 슬랙, 디스코드 봇을 설정하고 관리합니다.
          모든 메신저에서 동일한 보안 가드레일이 적용됩니다.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-1 lg:grid-cols-3">
        <TelegramCard />
        <SlackCard />
        <DiscordCard />
      </div>

      <div className="rounded-md bg-muted/50 px-4 py-3 text-xs text-muted-foreground">
        <strong>보안 안내:</strong> 모든 메신저 어댑터는 동일한 CommandAnalyzer 보안 가드레일을 적용합니다.
        DENIED 등급 명령은 즉시 차단되고, CONFIRM 등급 명령은 해당 메신저에서 승인 버튼이 전송됩니다.
      </div>
    </div>
  );
}
