/**
 * OnboardingWizard — 첫 실행 시 표시되는 온보딩 마법사.
 *
 * 최종 와이어프레임 A군(Frame 159~165)은 3단계다:
 *   파일 설치 → 모델 설치 → 워크스페이스 지정
 *
 * 메신저 봇 연결 단계(선택 + 설정)가 3단계 뒤에 붙어 있었지만, 봇 기능이
 * 제거되면서 함께 사라졌다. 이제 남은 건 와이어프레임 A군 3단계와 완료 화면뿐이다.
 *
 * `파일 설치`와 `모델 설치`는 별도 화면이 아니라 같은 화면의 두 상태다:
 * Ollama가 아직 없으면 파일 설치(1단계), 깔려 있으면 모델 선택(2단계).
 * 실제로 사용자가 하는 일이 그 순서대로이고, 화면을 쪼개면 Ollama가 이미
 * 설치된 사람에게 빈 1단계가 한 번 스쳐 지나간다.
 *
 * Step 0: AI 엔진 — Ollama 설치 + 모델 선택 (와이어프레임 1·2단계)
 * Step 1: 워크스페이스 지정            (와이어프레임 3단계)
 * Step 4: 완료 안내
 *
 * appStore의 `onboardingComplete`가 false일 때만 표시된다.
 */
import React, { useState, useEffect } from "react";
import {
  Cpu,
  CheckCircle2,
  ChevronRight,
  ChevronLeft,
  Sparkles,
  AlertTriangle,
  Bot,
  MessageCircle,
  RefreshCw,
  Copy,
  ExternalLink,
  Hash,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/ui/logo";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  FileChecklist,
  FolderField,
  InstallProgress,
  ModelSelectField,
  WizardSteps,
} from "@/components/ui/wizard";
import { buildModelOptions, RECOMMENDED_MODEL } from "@/lib/modelCatalog";
import useAppStore from "@/store/appStore";
import {
  saveLLMSettings,
  healthCheck,
  openWorkspaceFolder,
} from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

const TOTAL_STEPS = 3;

const STEP_LABELS = [
  "AI 엔진",
  "워크스페이스",
  "완료",
];

/**
 * 와이어프레임 밖 화면(완료)의 진행 표시.
 *
 * 3단계 인디케이터(WizardSteps)는 와이어프레임 A군 화면에만 붙는다. 그 뒤
 * 화면까지 3단계를 그리면 "워크스페이스 지정"이 활성인 채로 완료 화면이 떠서
 * 어느 단계인지 거짓말을 하게 된다.
 */
function StepDots({ current }) {
  return (
    <div className="mb-6 space-y-2">
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

// ── Step 0: LLM 선택 (Ollama 설치 가이드 강화) ────────────────────────────────

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

  // 설치된 모델 → 셀렉트 옵션 (추천 모델이 맨 위로 올라온다).
  const modelOptions = React.useMemo(() => buildModelOptions(ollamaModels), [ollamaModels]);

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
    setModel(val === "claude" ? "claude-sonnet-4-20250514" : "qwen3:4b");
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
          김대리에서 사용할 AI 언어 모델을 선택하세요.
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

      {/* Ollama 미설치 — 와이어프레임 A-1의 `파일 설치` 상태 */}
      {provider === "ollama" && ollamaStatus === "not_installed" && (
        <Card className="border-amber-300 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20">
          <CardContent className="space-y-3 py-4">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
                Ollama가 설치되어 있지 않아요.
              </p>
            </div>

            {/*
              와이어프레임 A-1의 진행 바 + 파일 체크리스트.
              Ollama 설치는 우리가 아니라 사용자가 외부 설치 프로그램으로 하므로
              바이트 단위 진행률을 알 수 없다. 그래서 "무엇이 남았는지"를
              체크리스트로 보여주고, 진행 바는 그 단계 수로 채운다 —
              가짜 퍼센트를 흘리는 것보다 정확하다.
            */}
            <InstallProgress
              value={0}
              label="Ollama 설치 대기 중"
              detail="0/2 단계"
            />
            <FileChecklist
              items={[
                { name: "Ollama 런타임", state: "active" },
                { name: `AI 모델 (${RECOMMENDED_MODEL})`, state: "pending" },
              ]}
            />

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

      {/* Ollama 설치됨, 모델 없음 — 와이어프레임 A-2(설치 완료) → A-3(모델 설치) 사이 */}
      {provider === "ollama" && ollamaStatus === "ok" && ollamaModels.length === 0 && (
        <Card className="border-blue-300 bg-blue-50/50 dark:border-blue-800 dark:bg-blue-950/20">
          <CardContent className="space-y-2 py-3">
            <InstallProgress value={50} label="Ollama 설치 완료" detail="1/2 단계" />
            <FileChecklist
              items={[
                { name: "Ollama 런타임", state: "done" },
                { name: `AI 모델 (${RECOMMENDED_MODEL})`, state: "active" },
              ]}
            />
            <p className="text-xs font-medium text-blue-700 dark:text-blue-400">
              Ollama가 설치되었어요. 이제 AI 모델을 받아야 해요.
            </p>
            <p className="text-xs text-blue-600 dark:text-blue-500">
              터미널에서 추천 모델을 받아주세요:
            </p>
            <div className="flex items-center gap-2 rounded bg-blue-100 dark:bg-blue-900/40 px-3 py-2">
              <code className="flex-1 text-xs font-mono">ollama pull qwen3:4b</code>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-xs"
                onClick={() => navigator.clipboard.writeText("ollama pull qwen3:4b")}
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

      {/* Ollama 모델 선택 — 와이어프레임 A-3/A-4 (제조사 아이콘 + `추천` 배지) */}
      {provider === "ollama" && ollamaStatus === "ok" && ollamaModels.length > 0 && (
        <div className="space-y-2">
          <Label>설치할 AI 모델을 선택해주세요.</Label>
          <ModelSelectField
            options={modelOptions}
            value={model}
            onChange={setModel}
            placeholder="모델을 선택해주세요."
          />
          <p className="text-xs text-muted-foreground">
            AI 모델은 추후에 언제든 변경이 가능합니다.
          </p>
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

      {/*
        와이어프레임 3단계 인디케이터.
        Ollama가 아직 없으면 `파일 설치`(0), 깔려 있으면 `모델 설치`(1)가 활성이다.
        Claude API를 고른 경우엔 받을 파일이 없으므로 곧바로 모델 단계로 본다.
      */}
      <WizardSteps
        current={provider === "ollama" && ollamaStatus !== "ok" ? 0 : 1}
      />
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
        <h2 className="text-xl font-bold">워크스페이스 위치를 지정해주세요.</h2>
      </div>

      {/* 와이어프레임 A-6/A-7의 폴더 선택 필드 441×40.
          경로는 앱이 정하고 사용자는 확인만 하므로 항상 값이 채워진 A-7 상태다.
          누르면 Finder/탐색기로 그 폴더를 연다. */}
      <div className="flex flex-col items-center gap-3">
        <FolderField
          value={workspacePath}
          onClick={handleOpenFolder}
          disabled={opening}
        />
        <p className="text-center text-xs text-muted-foreground">
          워크스페이스 위치는 추후에 언제든 변경이 가능합니다. 모든 파일 접근은 이
          폴더 안으로 제한됩니다.
        </p>
      </div>

      {openError && <p className="text-xs text-destructive">{openError}</p>}

      <div className="flex gap-2">
        <Button variant="ghost" className="flex-none" onClick={onPrev}>
          <ChevronLeft className="mr-1 h-4 w-4" />
          이전
        </Button>
        <Button className="flex-1" onClick={onNext} disabled={!workspacePath}>
          확인
          <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
      </div>

      <WizardSteps current={2} />
    </div>
  );
}

// ── Step 3: 완료 ──────────────────────────────────────────────────────────────

function StepComplete({ onFinish, onPrev }) {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const handleFinish = (navigateTo) => {
    onFinish();
    if (navigateTo) setCurrentPage(navigateTo);
  };

  return (
    <div className="space-y-6 text-center">
      <div>
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
          <Sparkles className="h-7 w-7 text-green-600 dark:text-green-400" />
        </div>
        <h2 className="text-xl font-bold">준비 완료!</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          김대리를 사용할 준비가 되었습니다.
        </p>
      </div>

      <div className="rounded-lg border bg-muted/30 p-4 text-left space-y-2">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          다음 단계
        </p>
        <ul className="space-y-2 text-sm">
          <li className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong>워크스페이스</strong> 폴더에 파일을 넣어 보세요.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              채팅 패널에서 <strong>"파일 목록 보여줘"</strong>라고 입력해 확인하세요.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              밖에서도 쓰려면 <strong>환경 설정 → 디바이스 추가</strong>에서 폰을 연결하세요.
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
 * 3단계 흐름
 *   0: LLM 선택 (Ollama 설치 가이드 강화)
 *   1: 워크스페이스 폴더 확인
 *   2: 완료
 */
export default function OnboardingWizard() {
  const completeOnboarding = useAppStore((s) => s.completeOnboarding);
  const [step, setStep] = useState(0);

  const goNext = () => setStep((s) => s + 1);
  const goPrev = () => setStep((s) => Math.max(0, s - 1));


  // 순서는 와이어프레임 A군을 따른다: AI 엔진(파일 설치 → 모델 설치) →
  // 워크스페이스 지정 → 완료.
  const steps = [
    <StepLLM key="llm" onNext={goNext} onPrev={goPrev} />,
    <StepWorkspace key="workspace" onNext={goNext} onPrev={goPrev} />,
    <StepComplete key="complete" onFinish={completeOnboarding} onPrev={goPrev} />,
  ];

  // 와이어프레임 3단계 화면(0·1)은 자기 하단에 WizardSteps를 직접 그린다.
  // 그 밖의 화면만 여기서 점 인디케이터를 얹는다.
  const showDots = step >= 2;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-background/95 backdrop-blur-sm">
      <div className="flex min-h-full items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="mb-4 flex flex-col items-center gap-1">
            {/* 브랜드 마크 — 이전에는 OpenClaw 시절 잔재인 🦞 이모지였다. */}
            <BrandMark className="h-9 w-9 rounded-lg" />
            <p className="text-xs uppercase tracking-widest text-muted-foreground">
              김대리 시작하기
            </p>
          </div>

          {showDots && <StepDots current={step} />}

          <Card>
            <CardContent className="pb-6 pt-6">{steps[step]}</CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
