/**
 * OnboardingWizard — 첫 실행 시 표시되는 온보딩 마법사 (Phase 3 Private-Claw).
 *
 * Step 0: OpenClaw 설치 확인
 * Step 1: LLM 엔진 선택 (Ollama 설치 가이드 강화)
 * Step 2: 메신저 선택 (Telegram / Slack / Discord)
 * Step 3: 선택된 메신저 설정
 * Step 4: 워크스페이스 폴더 확인
 * Step 5: 완료 안내
 *
 * appStore의 `onboardingComplete`가 false일 때만 표시된다.
 */
import React, { useState, useEffect, useCallback } from "react";
import {
  Cpu,
  CheckCircle2,
  ChevronRight,
  ChevronLeft,
  Sparkles,
  AlertTriangle,
  Bot,
  MessageCircle,
  FolderOpen,
  RefreshCw,
  Copy,
  ExternalLink,
  Hash,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBanner } from "@/components/ui/status";
import useAppStore from "@/store/appStore";
import {
  saveLLMSettings,
  healthCheck,
  openclawStatus,
  openclawInstalled,
  telegramSetup,
  telegramStatus,
  telegramStart,
  slackSetup,
  slackStart,
  discordSetup,
  discordStart,
  openWorkspaceFolder,
} from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

const TOTAL_STEPS = 6;

const STEP_LABELS = [
  "OpenClaw 확인",
  "LLM 선택",
  "메신저 선택",
  "메신저 설정",
  "워크스페이스",
  "완료",
];

/** Step progress indicator — dots + text label */
function StepDots({ current }) {
  return (
    <div className="mb-8 space-y-2">
      <div className="flex items-center justify-center gap-2">
        {Array.from({ length: TOTAL_STEPS }, (_, i) => (
          <span
            key={i}
            className={`block h-2 rounded-full transition-all ${
              i < current
                ? "w-4 bg-primary"
                : i === current
                ? "w-6 bg-primary"
                : "w-2 bg-muted-foreground/30"
            }`}
          />
        ))}
      </div>
      <p className="text-center text-xs text-muted-foreground">
        {current + 1} / {TOTAL_STEPS}단계 — {STEP_LABELS[current]}
      </p>
    </div>
  );
}

// ── Step 0: OpenClaw 설치 확인 ────────────────────────────────────────────────

function StepOpenClaw({ onNext, onPrev, stepIndex }) {
  const setOpenClawStatus = useAppStore((s) => s.setOpenClawStatus);
  const [status, setStatus] = useState("checking");

  // openclaw 바이너리 설치 여부를 우선 검사한다 — 게이트웨이가 일시적으로 stopped여도
  // 바이너리가 있으면 "ok"로 간주해 다음 단계로 진행 가능. 게이트웨이 상태는 부가 정보로만 갱신.
  const checkOpenClaw = useCallback(async () => {
    setStatus("checking");
    try {
      const inst = await openclawInstalled();
      if (inst?.installed) {
        setStatus("ok");
        // 게이트웨이 실행 상태는 별도로 한 번 더 조회해 store 갱신 (실패해도 무시)
        try {
          const result = await openclawStatus();
          setOpenClawStatus({
            state: result?.state ?? "stopped",
            message: result?.message ?? "",
            port: result?.port,
          });
        } catch {
          setOpenClawStatus({ state: "stopped", message: "" });
        }
      } else {
        setStatus("missing");
        setOpenClawStatus({ state: "stopped", message: "openclaw 바이너리 미설치" });
      }
    } catch {
      setStatus("missing");
      setOpenClawStatus({ state: "error", message: "OpenClaw 설치 확인 실패" });
    }
  }, [setOpenClawStatus]);

  useEffect(() => {
    checkOpenClaw();
  }, [checkOpenClaw]);

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Bot className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">OpenClaw 설치 확인</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          ajou-ai는 OpenClaw를 통해 AI 기능을 실행해요.
        </p>
      </div>

      {status === "checking" && (
        <StatusBanner
          tone="pending"
          icon={Bot}
          title="OpenClaw 확인 중"
          description="설치와 실행 상태를 확인하고 있어요..."
        />
      )}

      {status === "ok" && (
        <StatusBanner
          tone="ok"
          icon={Bot}
          title="OpenClaw 준비됨"
          description="OpenClaw가 정상적으로 실행되고 있어요."
        />
      )}

      {status === "missing" && (
        <StatusBanner
          tone="warning"
          icon={Bot}
          title="OpenClaw 문제 있음"
          description={
            <ol className="ml-4 space-y-2 list-decimal">
              <li>
                Node.js 22+ 가 설치되어 있는지 확인하세요.{" "}
                <a href="https://nodejs.org" target="_blank" rel="noreferrer" className="underline underline-offset-2">
                  nodejs.org
                </a>
              </li>
              <li>
                터미널에서 아래 명령어를 실행하세요:
                <code className="ml-2 mt-1 block rounded bg-amber-100 px-2 py-1 font-mono dark:bg-amber-900/40">
                  npm install -g openclaw@latest
                </code>
              </li>
              <li>앱을 재시작하거나 아래 "재확인" 버튼을 누르세요.</li>
            </ol>
          }
        />
      )}

      <div className="flex gap-2">
        {status === "missing" && (
          <Button variant="outline" className="flex-1" onClick={checkOpenClaw}>
            <RefreshCw className="mr-1.5 h-4 w-4" />
            재확인
          </Button>
        )}
        <Button
          className="flex-1"
          onClick={() => onNext(status === "missing")}
          disabled={status === "checking"}
          variant={status === "missing" ? "outline" : "default"}
        >
          {status === "missing" ? "건너뛰기 (나중에 설치)" : "다음"}
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 1: LLM 선택 (Ollama 설치 가이드 강화) ────────────────────────────────

function StepLLM({ onNext, onPrev }) {
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);
  const llmConfig = useAppStore((s) => s.llmConfig);

  const [provider, setProvider] = useState(llmConfig.provider);
  const [model, setModel] = useState(llmConfig.model);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [ollamaStatus, setOllamaStatus] = useState("unknown");
  const [ollamaModels, setOllamaModels] = useState([]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (provider !== "ollama") {
      setOllamaStatus("unknown");
      setOllamaModels([]);
      return;
    }
    let cancelled = false;
    setOllamaStatus("unknown");
    healthCheck()
      .then((result) => {
        if (cancelled) return;
        const isConnected = result?.ollama_status === "connected";
        setOllamaStatus(isConnected ? "ok" : "not_installed");
        if (isConnected && Array.isArray(result?.ollama_models)) {
          setOllamaModels(result.ollama_models);
          if (result.ollama_models.length > 0) {
            setModel(result.ollama_models[0]);
          }
        }
      })
      .catch(() => {
        if (!cancelled) setOllamaStatus("not_installed");
      });
    return () => { cancelled = true; };
  }, [provider]);

  const handleProviderChange = (val) => {
    setProvider(val);
    setModel(val === "claude" ? "claude-sonnet-4-20250514" : "llama3.2");
    setOllamaModels([]);
    setOllamaStatus("unknown");
  };

  const handleNext = async () => {
    setSaving(true);
    setError("");
    try {
      const config = { provider, model };
      await saveLLMSettings(config);
      setLLMConfig(config);
      onNext();
    } catch (err) {
      setError(toUserMessage(err));
      setLLMConfig({ provider, model });
      onNext();
    } finally {
      setSaving(false);
    }
  };

  const handleCopyBrew = () => {
    navigator.clipboard.writeText("brew install ollama").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleOpenOllama = async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open("https://ollama.com/download");
    } catch {
      window.open("https://ollama.com/download", "_blank");
    }
  };

  const handleRecheck = () => {
    setOllamaStatus("unknown");
    healthCheck()
      .then((result) => {
        const isConnected = result?.ollama_status === "connected";
        setOllamaStatus(isConnected ? "ok" : "not_installed");
        if (isConnected && Array.isArray(result?.ollama_models)) {
          setOllamaModels(result.ollama_models);
          if (result.ollama_models.length > 0) setModel(result.ollama_models[0]);
        }
      })
      .catch(() => setOllamaStatus("not_installed"));
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Cpu className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">AI 엔진 선택</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          ajou-ai에서 사용할 AI 언어 모델을 선택하세요.
        </p>
      </div>

      <div className="grid gap-3">
        <Card
          className={`cursor-pointer transition-all ${provider === "ollama" ? "border-primary ring-1 ring-primary" : ""}`}
          onClick={() => handleProviderChange("ollama")}
        >
          <CardContent className="flex items-start gap-3 pt-4 pb-4">
            <div className="mt-0.5 h-4 w-4 rounded-full border-2 border-primary flex items-center justify-center">
              {provider === "ollama" && <span className="block h-2 w-2 rounded-full bg-primary" />}
            </div>
            <div>
              <p className="text-sm font-semibold">Ollama (로컬 — 완전 오프라인)</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                모든 데이터가 내 컴퓨터에서만 처리됩니다. 인터넷 불필요.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card
          className={`cursor-pointer transition-all ${provider === "claude" ? "border-primary ring-1 ring-primary" : ""}`}
          onClick={() => handleProviderChange("claude")}
        >
          <CardContent className="flex items-start gap-3 pt-4 pb-4">
            <div className="mt-0.5 h-4 w-4 rounded-full border-2 border-primary flex items-center justify-center">
              {provider === "claude" && <span className="block h-2 w-2 rounded-full bg-primary" />}
            </div>
            <div>
              <p className="text-sm font-semibold">Claude API (클라우드)</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Anthropic의 Claude 모델. API 키가 필요합니다.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Ollama 미설치 — 설치 가이드 강화 */}
      {provider === "ollama" && ollamaStatus === "not_installed" && (
        <Card className="border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20">
          <CardContent className="space-y-3 py-4">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
                Ollama가 설치되어 있지 않아요.
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                방법 1: Homebrew (macOS 권장)
              </p>
              <div className="flex items-center gap-2 rounded bg-amber-100 dark:bg-amber-900/40 px-3 py-2">
                <code className="flex-1 text-xs font-mono">brew install ollama</code>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 px-2 text-xs"
                  onClick={handleCopyBrew}
                >
                  <Copy className="h-3 w-3 mr-1" />
                  {copied ? "복사됨!" : "복사"}
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                방법 2: 공식 사이트에서 다운로드
              </p>
              <Button
                size="sm"
                variant="outline"
                className="w-full text-xs border-amber-400 text-amber-700 hover:bg-amber-100"
                onClick={handleOpenOllama}
              >
                <ExternalLink className="h-3 w-3 mr-1" />
                ollama.com/download 열기
              </Button>
            </div>

            <Button
              size="sm"
              variant="outline"
              className="w-full text-xs"
              onClick={handleRecheck}
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              설치 후 재확인
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Ollama 설치됨, 모델 없음 */}
      {provider === "ollama" && ollamaStatus === "ok" && ollamaModels.length === 0 && (
        <Card className="border-blue-300 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-950/20">
          <CardContent className="space-y-2 py-3">
            <p className="text-xs font-medium text-blue-700 dark:text-blue-400">
              Ollama가 설치되었어요. 이제 AI 모델을 받아야 해요.
            </p>
            <p className="text-xs text-blue-600 dark:text-blue-500">
              터미널에서 추천 모델을 받아주세요:
            </p>
            <div className="flex items-center gap-2 rounded bg-blue-100 dark:bg-blue-900/40 px-3 py-2">
              <code className="flex-1 text-xs font-mono">ollama pull llama3.2</code>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-xs"
                onClick={() => navigator.clipboard.writeText("ollama pull llama3.2")}
              >
                <Copy className="h-3 w-3" />
              </Button>
            </div>
            <Button size="sm" variant="outline" className="w-full text-xs" onClick={handleRecheck}>
              <RefreshCw className="h-3 w-3 mr-1" />
              모델 확인
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Ollama 모델 선택 */}
      {provider === "ollama" && ollamaStatus === "ok" && ollamaModels.length > 0 && (
        <div className="space-y-1.5">
          <Label htmlFor="ob-ollama-model">사용할 모델</Label>
          <Select value={model} onValueChange={setModel}>
            <SelectTrigger id="ob-ollama-model">
              <SelectValue placeholder="모델 선택" />
            </SelectTrigger>
            <SelectContent>
              {ollamaModels.map((m) => (
                <SelectItem key={m} value={m}>{m}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Claude API 키 안내 */}
      {provider === "claude" && (
        <div className="space-y-1.5">
          <Label htmlFor="ob-model">모델</Label>
          <Input
            id="ob-model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="claude-sonnet-4-20250514"
          />
          <p className="text-xs text-muted-foreground">
            API 키는 완료 후 자격증명 관리에서{" "}
            <code className="rounded bg-muted px-1">claude_api_key</code>로 저장하세요.
          </p>
        </div>
      )}

      {error && (
        <p className="text-xs text-muted-foreground">
          설정 저장 실패 (나중에 설정 페이지에서 다시 저장하세요): {error}
        </p>
      )}

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button className="flex-1" onClick={handleNext} disabled={saving}>
          {saving ? "저장 중..." : "다음"}
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 2: 메신저 선택 ────────────────────────────────────────────────────────

function StepMessengerChoice({ onNext, onPrev }) {
  const selectedMessenger = useAppStore((s) => s.selectedMessenger);
  const setSelectedMessenger = useAppStore((s) => s.setSelectedMessenger);
  const [choice, setChoice] = useState(selectedMessenger);

  const MESSENGERS = [
    {
      id: "telegram",
      name: "텔레그램 (Telegram)",
      desc: "스마트폰에서 가장 쉽게 사용 가능. @BotFather로 봇 토큰 발급 후 즉시 연결.",
      icon: MessageCircle,
      color: "text-blue-500",
    },
    {
      id: "slack",
      name: "슬랙 (Slack)",
      desc: "팀 채널에서 업무 자동화. Slack App과 소켓 모드 토큰이 필요합니다.",
      icon: Hash,
      color: "text-purple-500",
    },
    {
      id: "discord",
      name: "디스코드 (Discord)",
      desc: "Discord 서버에서 봇으로 사용. Bot Token 발급 후 서버에 초대하세요.",
      icon: Bot,
      color: "text-indigo-500",
    },
  ];

  const handleNext = () => {
    setSelectedMessenger(choice);
    onNext();
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <MessageCircle className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">메신저 선택</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          어떤 메신저로 ajou-ai를 제어하시겠어요?
        </p>
      </div>

      <div className="grid gap-3">
        {MESSENGERS.map(({ id, name, desc, icon: Icon, color }) => (
          <Card
            key={id}
            className={`cursor-pointer transition-all ${choice === id ? "border-primary ring-1 ring-primary" : ""}`}
            onClick={() => setChoice(id)}
          >
            <CardContent className="flex items-start gap-3 pt-4 pb-4">
              <div className="mt-0.5 h-4 w-4 rounded-full border-2 border-primary flex items-center justify-center shrink-0">
                {choice === id && <span className="block h-2 w-2 rounded-full bg-primary" />}
              </div>
              <Icon className={`h-5 w-5 shrink-0 mt-0.5 ${color}`} />
              <div>
                <p className="text-sm font-semibold">{name}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{desc}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button className="flex-1" onClick={handleNext}>
          다음
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 3a: 텔레그램 봇 설정 ────────────────────────────────────────────────────

function StepTelegram({ onNext, onPrev }) {
  const setTelegramConnected = useAppStore((s) => s.setTelegramConnected);

  const [token, setToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const handleTest = async () => {
    if (!token.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await telegramSetup(token.trim(), chatId.trim() || undefined);
      setTestResult(result);
      if (result.ok) setTelegramConnected(true);
    } catch (err) {
      setTestResult({ ok: false, error: toUserMessage(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <MessageCircle className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">텔레그램 봇 설정</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          텔레그램으로 PC 파일에 원격 접근할 수 있습니다.
        </p>
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="ob-tg-token">봇 토큰</Label>
          <Input
            id="ob-tg-token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="123456789:ABC-DEF..."
            type="password"
            autoComplete="off"
          />
          <p className="text-xs text-muted-foreground">
            텔레그램에서 <strong>@BotFather</strong>에게 메시지를 보내{" "}
            <code className="rounded bg-muted px-1">/newbot</code> 명령으로 봇을 만들고 토큰을 발급받으세요.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-tg-chatid">내 Chat ID (권장)</Label>
          <Input
            id="ob-tg-chatid"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            placeholder="숫자로 이루어진 내 Chat ID"
            className={!chatId.trim() ? "border-orange-400 focus-visible:ring-orange-400" : ""}
          />
          {!chatId.trim() ? (
            <div className="flex items-start gap-1.5 rounded-md bg-orange-50 dark:bg-orange-950/20 border border-orange-300 dark:border-orange-800 px-3 py-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-orange-600" />
              <p className="text-xs text-orange-700 dark:text-orange-400">
                <strong>보안 권장:</strong> Chat ID를 설정하면 나만 봇에 접근할 수 있습니다.
                텔레그램에서{" "}
                <code className="rounded bg-orange-100 dark:bg-orange-900/40 px-1">@userinfobot</code>에
                메시지를 보내면 내 Chat ID를 확인할 수 있습니다.
              </p>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              이 Chat ID만 봇에 접근할 수 있습니다. 보안 강화 설정입니다.
            </p>
          )}
        </div>

        <Button
          variant="outline"
          className="w-full"
          onClick={handleTest}
          disabled={testing || !token.trim()}
        >
          {testing ? (
            <>
              <RefreshCw className="mr-1.5 h-4 w-4 animate-spin" />
              연결 테스트 중...
            </>
          ) : (
            "연결 테스트"
          )}
        </Button>

        {testResult?.ok && (
          <div className="flex items-center gap-2 rounded-md bg-green-50 dark:bg-green-950/20 px-3 py-2">
            <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            <p className="text-sm text-green-700 dark:text-green-400">
              연결 성공! 봇: @{testResult.bot_username} ({testResult.bot_name})
            </p>
          </div>
        )}

        {testResult && !testResult.ok && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive shrink-0" />
            <p className="text-sm text-destructive">{testResult.error}</p>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button variant="ghost" className="flex-1" onClick={onNext}>
          나중에 설정
        </Button>
        <Button className="flex-1" onClick={onNext} disabled={testing}>
          {testResult?.ok ? "완료" : "다음"}
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 3b: 슬랙 봇 설정 ────────────────────────────────────────────────────────

function StepSlack({ onNext, onPrev }) {
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const handleTest = async () => {
    if (!botToken.trim() || !appToken.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await slackSetup(botToken.trim(), appToken.trim());
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, error: toUserMessage(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Hash className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">슬랙 봇 설정</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          슬랙 채널에서 ajou-ai를 사용합니다.
        </p>
      </div>

      <Card className="border-blue-200 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-950/20">
        <CardContent className="py-3 space-y-1.5">
          <p className="text-xs font-medium text-blue-700 dark:text-blue-400">준비 사항</p>
          <ol className="ml-4 space-y-1 text-xs text-blue-600 dark:text-blue-500 list-decimal">
            <li><a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="underline">api.slack.com/apps</a>에서 새 Slack App을 생성하세요.</li>
            <li>OAuth &amp; Permissions에서 Bot Token Scopes 추가: <code className="rounded bg-blue-100 px-1">chat:write, app_mentions:read, im:read, im:write</code></li>
            <li>Socket Mode를 활성화하고 App Token을 발급받으세요.</li>
            <li>앱을 워크스페이스에 설치한 뒤 아래 토큰을 입력하세요.</li>
          </ol>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="ob-slack-bot">Bot Token (xoxb-...)</Label>
          <Input
            id="ob-slack-bot"
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
            placeholder="xoxb-..."
            type="password"
            autoComplete="off"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-slack-app">App Token (xapp-...)</Label>
          <Input
            id="ob-slack-app"
            value={appToken}
            onChange={(e) => setAppToken(e.target.value)}
            placeholder="xapp-..."
            type="password"
            autoComplete="off"
          />
        </div>

        <Button
          variant="outline"
          className="w-full"
          onClick={handleTest}
          disabled={testing || !botToken.trim() || !appToken.trim()}
        >
          {testing ? (
            <><RefreshCw className="mr-1.5 h-4 w-4 animate-spin" />연결 테스트 중...</>
          ) : "연결 테스트"}
        </Button>

        {testResult?.ok && (
          <div className="flex items-center gap-2 rounded-md bg-green-50 dark:bg-green-950/20 px-3 py-2">
            <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            <p className="text-sm text-green-700 dark:text-green-400">
              슬랙 연결 성공! 팀: {testResult.team}
            </p>
          </div>
        )}

        {testResult && !testResult.ok && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive shrink-0" />
            <p className="text-sm text-destructive">{testResult.error}</p>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button variant="ghost" className="flex-1" onClick={onNext}>
          나중에 설정
        </Button>
        <Button className="flex-1" onClick={onNext} disabled={testing}>
          다음
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 3c: 디스코드 봇 설정 ──────────────────────────────────────────────────

function StepDiscord({ onNext, onPrev }) {
  const [token, setToken] = useState("");
  const [guildId, setGuildId] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const handleTest = async () => {
    if (!token.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await discordSetup(token.trim(), guildId.trim() || undefined);
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, error: toUserMessage(err) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Bot className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">디스코드 봇 설정</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Discord 서버에서 ajou-ai를 사용합니다.
        </p>
      </div>

      <Card className="border-indigo-200 bg-indigo-50/50 dark:border-indigo-800 dark:bg-indigo-950/20">
        <CardContent className="py-3 space-y-1.5">
          <p className="text-xs font-medium text-indigo-700 dark:text-indigo-400">준비 사항</p>
          <ol className="ml-4 space-y-1 text-xs text-indigo-600 dark:text-indigo-500 list-decimal">
            <li><a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer" className="underline">Discord 개발자 포털</a>에서 새 Application을 만드세요.</li>
            <li>Bot 탭에서 봇을 생성하고 Token을 복사하세요.</li>
            <li>Message Content Intent, Server Members Intent를 활성화하세요.</li>
            <li>OAuth2에서 봇을 서버에 초대한 뒤 아래 토큰을 입력하세요.</li>
          </ol>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="ob-discord-token">Bot Token</Label>
          <Input
            id="ob-discord-token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Discord Bot Token"
            type="password"
            autoComplete="off"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-discord-guild">서버 ID (선택 — 보안 강화)</Label>
          <Input
            id="ob-discord-guild"
            value={guildId}
            onChange={(e) => setGuildId(e.target.value)}
            placeholder="서버 우클릭 → 서버 ID 복사"
          />
          <p className="text-xs text-muted-foreground">
            입력하면 이 서버의 메시지만 처리합니다. 빈칸이면 모든 서버에서 동작합니다.
          </p>
        </div>

        <Button
          variant="outline"
          className="w-full"
          onClick={handleTest}
          disabled={testing || !token.trim()}
        >
          {testing ? (
            <><RefreshCw className="mr-1.5 h-4 w-4 animate-spin" />연결 테스트 중...</>
          ) : "연결 테스트"}
        </Button>

        {testResult?.ok && (
          <div className="flex items-center gap-2 rounded-md bg-green-50 dark:bg-green-950/20 px-3 py-2">
            <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            <p className="text-sm text-green-700 dark:text-green-400">
              디스코드 연결 성공! 봇: @{testResult.bot_username}
            </p>
          </div>
        )}

        {testResult && !testResult.ok && (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive shrink-0" />
            <p className="text-sm text-destructive">{testResult.error}</p>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button variant="ghost" className="flex-1" onClick={onNext}>
          나중에 설정
        </Button>
        <Button className="flex-1" onClick={onNext} disabled={testing}>
          다음
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 4: 워크스페이스 폴더 확인 ────────────────────────────────────────────────

function StepWorkspace({ onNext, onPrev }) {
  const workspacePath = useAppStore((s) => s.workspacePath);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState("");

  const handleOpenFolder = async () => {
    setOpening(true);
    setOpenError("");
    try {
      await openWorkspaceFolder();
    } catch (err) {
      setOpenError(toUserMessage(err));
    } finally {
      setOpening(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <FolderOpen className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-bold">워크스페이스 폴더</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          모든 파일 접근은 아래 폴더 안으로 안전하게 제한됩니다.
        </p>
      </div>

      <Card className="border-primary/30 bg-primary/5">
        <CardContent className="py-4">
          <p className="text-xs text-muted-foreground mb-1 font-medium uppercase tracking-wide">
            워크스페이스 경로
          </p>
          <code className="text-sm font-mono break-all">{workspacePath}</code>
          <p className="mt-3 text-xs text-muted-foreground">
            앱을 처음 실행할 때 자동으로 생성됩니다.
            메신저에서 "파일 목록 보여줘" 같은 명령으로 이 폴더의 파일에 접근할 수 있습니다.
          </p>
        </CardContent>
      </Card>

      {openError && <p className="text-xs text-destructive">{openError}</p>}

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button variant="outline" className="flex-1" onClick={handleOpenFolder} disabled={opening}>
          <FolderOpen className="mr-1.5 h-4 w-4" />
          {opening ? "열기 중..." : "폴더 열기"}
        </Button>
        <Button className="flex-1" onClick={onNext}>
          다음
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 5: 완료 ──────────────────────────────────────────────────────────────

function StepComplete({ onFinish, onPrev }) {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const telegramConnected = useAppStore((s) => s.telegramConnected);
  const selectedMessenger = useAppStore((s) => s.selectedMessenger);
  const [botStarting, setBotStarting] = useState(false);
  const [botStarted, setBotStarted] = useState(false);
  const [botError, setBotError] = useState("");

  const MESSENGER_LABELS = {
    telegram: "텔레그램",
    slack: "슬랙",
    discord: "디스코드",
  };

  useEffect(() => {
    let cancelled = false;
    const autoStart = async () => {
      setBotStarting(true);
      setBotError("");
      try {
        if (selectedMessenger === "telegram" && telegramConnected) {
          const status = await telegramStatus();
          if (!status?.running) await telegramStart();
          if (!cancelled) setBotStarted(true);
        } else if (selectedMessenger === "slack") {
          await slackStart();
          if (!cancelled) setBotStarted(true);
        } else if (selectedMessenger === "discord") {
          await discordStart();
          if (!cancelled) setBotStarted(true);
        }
      } catch (err) {
        if (!cancelled) setBotError(toUserMessage(err));
      } finally {
        if (!cancelled) setBotStarting(false);
      }
    };
    autoStart();
    return () => { cancelled = true; };
  }, [selectedMessenger, telegramConnected]);

  const handleFinish = (navigateTo) => {
    onFinish();
    if (navigateTo) setCurrentPage(navigateTo);
  };

  const messengerLabel = MESSENGER_LABELS[selectedMessenger] ?? selectedMessenger;

  return (
    <div className="space-y-6 text-center">
      <div>
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
          <Sparkles className="h-7 w-7 text-green-600 dark:text-green-400" />
        </div>
        <h2 className="text-xl font-bold">준비 완료!</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          ajou-ai를 사용할 준비가 되었습니다.
        </p>
      </div>

      <div className="rounded-lg border bg-muted/30 p-4 text-left space-y-2">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          다음 단계
        </p>
        <ul className="space-y-2 text-sm">
          <li className="flex items-start gap-2">
            {botStarting ? (
              <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
            ) : botStarted ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-500" />
            ) : (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            )}
            <span>
              {botStarting
                ? `${messengerLabel} 봇 자동 시작 중...`
                : botStarted
                ? `${messengerLabel} 봇이 자동으로 시작되었습니다.`
                : botError
                ? `${messengerLabel} 봇 시작 실패 — 메신저 설정에서 직접 시작하세요.`
                : `${messengerLabel} 봇 시작 대기 중...`}
            </span>
          </li>
          <li className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong>워크스페이스</strong> 폴더에 파일을 넣어 보세요.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              {messengerLabel}에서 <strong>"파일 목록 보여줘"</strong>라고 입력해 확인하세요.
            </span>
          </li>
        </ul>
      </div>

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <div className="flex flex-1 flex-col gap-2">
          <Button variant="outline" onClick={() => handleFinish("workspace")}>
            워크스페이스 바로 가기
          </Button>
          <Button onClick={() => handleFinish(null)}>시작하기</Button>
        </div>
      </div>
    </div>
  );
}

// ── 메인 마법사 ───────────────────────────────────────────────────────────────

/**
 * Main onboarding wizard — shown only when onboardingComplete is false.
 * Phase 3 (Private-Claw): 6단계 흐름
 *   0: OpenClaw 확인
 *   1: LLM 선택 (Ollama 설치 가이드 강화)
 *   2: 메신저 선택 (Telegram / Slack / Discord)
 *   3: 메신저별 설정 (분기)
 *   4: 워크스페이스 폴더 확인
 *   5: 완료
 */
export default function OnboardingWizard() {
  const completeOnboarding = useAppStore((s) => s.completeOnboarding);
  const selectedMessenger = useAppStore((s) => s.selectedMessenger);
  const [step, setStep] = useState(0);

  const goNext = () => setStep((s) => s + 1);
  const goPrev = () => setStep((s) => Math.max(0, s - 1));

  // Step 3: 선택된 메신저에 따라 분기
  const MessengerStep = {
    telegram: StepTelegram,
    slack: StepSlack,
    discord: StepDiscord,
  }[selectedMessenger] ?? StepTelegram;

  const steps = [
    <StepOpenClaw key="openclaw" onNext={goNext} onPrev={goPrev} stepIndex={0} />,
    <StepLLM key="llm" onNext={goNext} onPrev={goPrev} />,
    <StepMessengerChoice key="messenger-choice" onNext={goNext} onPrev={goPrev} />,
    <MessengerStep key={`messenger-${selectedMessenger}`} onNext={goNext} onPrev={goPrev} />,
    <StepWorkspace key="workspace" onNext={goNext} onPrev={goPrev} />,
    <StepComplete key="complete" onFinish={completeOnboarding} onPrev={goPrev} />,
  ];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-background/95 backdrop-blur-sm">
      <div className="flex min-h-full items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="mb-2 text-center">
            <span className="text-3xl select-none">🦞</span>
            <p className="mt-1 text-xs text-muted-foreground uppercase tracking-widest">
              ajou-ai 시작하기
            </p>
          </div>

          <StepDots current={step} />

          <Card>
            <CardContent className="pt-6 pb-6">{steps[step]}</CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
