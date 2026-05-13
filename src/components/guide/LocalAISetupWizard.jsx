/**
 * LocalAISetupWizard.jsx — "OpenClaw + Ollama 로컬 모델"을 한 번에 자동 설정.
 *
 * 진단 → 계획 → 자동 실행 → 검증 단일 모달.
 * 단계:
 *   1. OpenClaw 설치 (`npm install -g openclaw@latest`) — 미설치 시
 *   2. OpenClaw 게이트웨이 시작 (`openclaw gateway --port 18789`) — 미실행 시
 *   3. Ollama 설치 (`brew install ollama` macOS / 외부 다운로드 링크) — 미설치 시
 *   4. Ollama 데몬 실행 (`brew services start ollama` 또는 안내) — 미실행 시
 *   5. Ollama 모델 다운로드 (`ollama pull <model>`) — 미다운로드 시
 *   6. OpenClaw → Ollama 연결 (`openclaw config set ...` 비인터랙티브) — 항상
 *
 * 모든 단계는 멱등하게 동작 — 이미 충족되면 즉시 skip.
 */
import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import {
  Cpu,
  Zap,
  X,
  Check,
  Loader2,
  AlertCircle,
  Download,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import useAppStore from "@/store/appStore";
import {
  openclawStatus,
  openclawInstalled,
  openclawEnsureRunning,
  openclawUseOllama,
  ollamaStatus,
  agentChat,
} from "@/lib/api";
import {
  STEP,
  STEP_LABEL,
  RECOMMENDED_MODELS,
  DEFAULT_MODEL,
  buildPlan,
  isAllReady,
  hasModelInstalled,
} from "@/lib/localAISetup";

/** 진단/검증 후 잠깐 기다림 (ms) */
const DETECTION_DELAY_MS = 1000;

/** macOS 여부 — Ollama 자동 설치는 brew 기반이라 OS 분기 필요 */
function isMac() {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad/.test(navigator.platform || "") ||
    /Mac OS X/.test(navigator.userAgent || "");
}

// ── 단계 정의 (순수 로직은 @/lib/localAISetup.js로 분리, 여기는 wizard 전용 상수만) ──

/** 프롬프트 검증 시 보낼 핑 메시지 — 응답 형식·언어는 모델마다 다르므로 *비어있지 않은 응답*만 검사 */
const PING_MESSAGE = "안녕! 한 단어로 'OK'라고만 답해줘.";
const PROMPT_TEST_TIMEOUT_MS = 60_000;

// ── 메인 컴포넌트 ───────────────────────────────────────────────────────────

export default function LocalAISetupWizard() {
  const onboardingComplete = useAppStore((s) => s.onboardingComplete);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const setOpenClawStatus = useAppStore((s) => s.setOpenClawStatus);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [diag, setDiag] = useState(null); // null | { oc, ocInstalled, oll }
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [phase, setPhase] = useState("idle"); // idle | running | done | error
  const [stepStates, setStepStates] = useState({});
  // stepStates[id] = { status: 'pending'|'running'|'done'|'skipped'|'error', logs: [{kind, text}] }
  const [activeStep, setActiveStep] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  const childRef = useRef(null);
  const cancelRef = useRef(false);
  const logBoxRef = useRef(null);

  // 진단 — OpenClaw 설치/실행 + Ollama 설치/실행/모델
  const runDiagnosis = useCallback(async () => {
    try {
      const [oc, ocInst, oll] = await Promise.all([
        openclawStatus().catch(() => ({ state: "stopped", message: "" })),
        openclawInstalled().catch(() => ({ installed: false })),
        ollamaStatus().catch(() => ({ installed: false, running: false, models: [] })),
      ]);
      setDiag({ oc, ocInstalled: ocInst, oll });
      // store 동기화
      setOpenClawStatus({
        state: oc?.state ?? "stopped",
        message: oc?.message ?? "",
        port: oc?.port,
      });
    } catch {
      setDiag({
        oc: { state: "error" },
        ocInstalled: { installed: false },
        oll: { installed: false, running: false, models: [] },
      });
    }
  }, [setOpenClawStatus]);

  // 앱 시작 직후 1회 진단 → 부족하면 모달 자동 노출
  useEffect(() => {
    const t = setTimeout(() => {
      runDiagnosis();
    }, DETECTION_DELAY_MS);
    return () => clearTimeout(t);
  }, [runDiagnosis]);

  // 모달 자동 노출 결정 — provider=ollama인 사용자에게만 자동 노출
  // (Claude API 사용자는 글로벌 이벤트로 수동 트리거 가능)
  useEffect(() => {
    if (!diag) return;
    if (!onboardingComplete) return;
    if (dismissed) return;
    if (llmConfig?.provider !== "ollama") return;
    const allReady = isAllReady(diag, model);
    if (!allReady && !open) {
      setOpen(true);
    }
  }, [diag, onboardingComplete, dismissed, model, open, llmConfig?.provider]);

  // llmConfig의 model이 있으면 초기값으로 사용
  useEffect(() => {
    if (llmConfig?.provider === "ollama" && llmConfig?.model) {
      setModel(llmConfig.model);
    }
  }, [llmConfig?.provider, llmConfig?.model]);

  // 글로벌 이벤트 — Dashboard 등에서 다시 열기
  useEffect(() => {
    const handler = () => {
      setDismissed(false);
      runDiagnosis();
      setOpen(true);
    };
    window.addEventListener("private-claw:open-local-ai-setup", handler);
    // 기존 이벤트 호환
    window.addEventListener("private-claw:open-openclaw-install", handler);
    return () => {
      window.removeEventListener("private-claw:open-local-ai-setup", handler);
      window.removeEventListener("private-claw:open-openclaw-install", handler);
    };
  }, [runDiagnosis]);

  // 자동 스크롤 — 활성 단계 로그
  useEffect(() => {
    if (logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [stepStates, activeStep]);

  // 필요한 단계 / 이미 완료된 단계 분리
  const plan = useMemo(() => {
    if (!diag) return { todo: [], skipped: [] };
    return buildPlan(diag, model);
  }, [diag, model]);

  // 단계별 로그 push
  const pushLog = useCallback((stepId, kind, text) => {
    setStepStates((prev) => ({
      ...prev,
      [stepId]: {
        ...(prev[stepId] || { status: "running", logs: [] }),
        logs: [...(prev[stepId]?.logs ?? []), { kind, text }],
      },
    }));
  }, []);

  const setStepStatus = useCallback((stepId, status) => {
    setStepStates((prev) => ({
      ...prev,
      [stepId]: {
        ...(prev[stepId] || { logs: [] }),
        status,
      },
    }));
  }, []);

  // 셸 명령 실행 — stdout/stderr 라이브 스트리밍
  const runShell = useCallback(
    async (stepId, name, args) => {
      const { Command } = await import("@tauri-apps/plugin-shell");
      return new Promise((resolve, reject) => {
        try {
          const cmd = Command.create(name, args);
          cmd.stdout.on("data", (line) => pushLog(stepId, "out", String(line).trimEnd()));
          cmd.stderr.on("data", (line) => pushLog(stepId, "err", String(line).trimEnd()));
          cmd.on("close", (data) => {
            childRef.current = null;
            const code = data?.code ?? 0;
            if (code === 0) resolve();
            else reject(new Error(`종료 코드 ${code}`));
          });
          cmd.on("error", (err) => {
            childRef.current = null;
            reject(new Error(String(err)));
          });
          cmd.spawn().then((child) => {
            childRef.current = child;
          }).catch(reject);
        } catch (e) {
          reject(e);
        }
      });
    },
    [pushLog]
  );

  // 개별 단계 실행
  const runStep = useCallback(
    async (stepId) => {
      if (cancelRef.current) return false;
      setActiveStep(stepId);
      setStepStatus(stepId, "running");
      pushLog(stepId, "info", `▶ ${STEP_LABEL[stepId]}`);

      try {
        switch (stepId) {
          case STEP.INSTALL_OC: {
            pushLog(stepId, "info", "$ npm install -g openclaw@latest");
            await runShell(stepId, "npm", ["install", "-g", "openclaw@latest"]);
            break;
          }
          case STEP.START_OC: {
            pushLog(stepId, "info", "openclaw gateway --port 18789 (자식 프로세스로 spawn)");
            const result = await openclawEnsureRunning();
            setOpenClawStatus({
              state: result?.state ?? "stopped",
              message: result?.message ?? "",
              port: result?.port,
            });
            if (result?.state !== "running") {
              throw new Error(result?.message || "게이트웨이 ready 실패");
            }
            pushLog(stepId, "info", `✓ port ${result?.port ?? 18789} 응답`);
            break;
          }
          case STEP.INSTALL_OLLAMA: {
            if (isMac()) {
              pushLog(stepId, "info", "$ brew install ollama");
              await runShell(stepId, "brew-install-ollama", ["install", "ollama"]);
            } else {
              pushLog(stepId, "err", "현재 자동 설치는 macOS(brew)만 지원합니다.");
              pushLog(
                stepId,
                "info",
                "https://ollama.com/download 에서 설치 후 [재진단]을 눌러 주세요."
              );
              throw new Error("macOS 외 OS는 자동 설치 미지원 — 수동 설치 필요");
            }
            break;
          }
          case STEP.START_OLLAMA: {
            if (isMac()) {
              pushLog(stepId, "info", "$ brew services start ollama");
              try {
                await runShell(stepId, "brew-services-start-ollama", [
                  "services",
                  "start",
                  "ollama",
                ]);
              } catch (e) {
                pushLog(stepId, "err", `brew services 실패: ${e?.message ?? e}`);
                pushLog(stepId, "info", "Ollama.app을 직접 실행한 뒤 [재진단]을 눌러 주세요.");
                throw e;
              }
            } else {
              pushLog(stepId, "info", "Ollama 앱을 실행한 뒤 [재진단]을 눌러 주세요.");
              throw new Error("자동 시작 미지원 — Ollama 앱 직접 실행 필요");
            }
            // 잠깐 기다린 뒤 ready 확인
            for (let i = 0; i < 30; i++) {
              await new Promise((r) => setTimeout(r, 500));
              const s = await ollamaStatus().catch(() => ({ running: false }));
              if (s?.running) {
                pushLog(stepId, "info", "✓ Ollama 11434 응답");
                return true;
              }
            }
            throw new Error("Ollama 데몬이 15초 내에 응답하지 않습니다");
          }
          case STEP.PULL_MODEL: {
            pushLog(stepId, "info", `$ ollama pull ${model}`);
            await runShell(stepId, "ollama-pull", ["pull", model]);
            break;
          }
          case STEP.CONFIG_OC: {
            pushLog(stepId, "info", `$ openclaw config set ... (model = ollama/${model})`);
            const r = await openclawUseOllama(model);
            (r?.applied || []).forEach((a) => {
              pushLog(stepId, "out", `  ${a.path} ← ${a.value}`);
            });
            pushLog(stepId, "info", "✓ OpenClaw 기본 모델이 Ollama로 설정됨");
            break;
          }
          case STEP.PROMPT_TEST: {
            pushLog(stepId, "info", `> ${PING_MESSAGE}`);
            const reply = await Promise.race([
              agentChat(PING_MESSAGE, null),
              new Promise((_, rej) =>
                setTimeout(
                  () => rej(new Error("응답 대기 60초 초과 — 모델 첫 로드가 오래 걸렸을 수 있습니다")),
                  PROMPT_TEST_TIMEOUT_MS
                )
              ),
            ]);
            const text = String(reply?.response ?? "").trim();
            if (!text) {
              throw new Error("게이트웨이가 빈 응답을 반환했습니다");
            }
            const preview = text.length > 200 ? `${text.slice(0, 200)}…` : text;
            pushLog(stepId, "out", `< ${preview}`);
            pushLog(stepId, "info", "✓ 로컬 모델 ↔ OpenClaw ↔ sidecar 경로 검증 완료");
            break;
          }
          default:
            throw new Error(`알 수 없는 단계: ${stepId}`);
        }
        setStepStatus(stepId, "done");
        return true;
      } catch (err) {
        const msg = String(err?.message ?? err);
        pushLog(stepId, "err", `✗ ${msg}`);
        setStepStatus(stepId, "error");
        setErrorMsg(msg);
        return false;
      }
    },
    [model, pushLog, runShell, setOpenClawStatus, setStepStatus]
  );

  // 전체 자동 실행 — todo 단계만 순차 실행. skipped는 UI에서 "이미 완료"로만 표시.
  const runAll = useCallback(async () => {
    cancelRef.current = false;
    setPhase("running");
    setErrorMsg("");
    setStepStates({});
    for (const stepId of plan.todo) {
      const ok = await runStep(stepId);
      if (!ok) {
        setPhase("error");
        setActiveStep(null);
        return;
      }
      if (cancelRef.current) {
        setPhase("error");
        setActiveStep(null);
        return;
      }
    }
    setActiveStep(null);
    // 마지막 검증 진단
    await runDiagnosis();
    setPhase("done");
  }, [plan.todo, runDiagnosis, runStep]);

  // 취소
  const cancelAll = useCallback(async () => {
    cancelRef.current = true;
    if (childRef.current) {
      try {
        await childRef.current.kill();
      } catch {
        // ignore
      }
      childRef.current = null;
    }
  }, []);

  const handleClose = () => {
    if (phase === "running") return; // 안전 — 실행 중에는 닫지 않음
    setOpen(false);
    setDismissed(true);
  };

  const handleLater = () => {
    setOpen(false);
    setDismissed(true);
  };

  const handleOpenGuide = () => {
    setOpen(false);
    setDismissed(true);
    setCurrentPage("guide");
  };

  const handleRediagnose = async () => {
    setPhase("idle");
    setStepStates({});
    setErrorMsg("");
    await runDiagnosis();
  };

  if (!open || !diag) return null;

  const allReady = isAllReady(diag, model);

  return (
    <div
      className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/50 px-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && phase !== "running") handleClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="로컬 AI 자동 설정"
        className="w-full max-w-2xl overflow-hidden rounded-lg border border-border bg-popover shadow-2xl"
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">로컬 AI 자동 설정 (OpenClaw + Ollama)</h2>
          </div>
          <button
            type="button"
            onClick={phase === "running" ? undefined : handleClose}
            disabled={phase === "running"}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
            aria-label="닫기"
            title={phase === "running" ? "실행 중에는 닫을 수 없습니다" : "닫기"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-5">
          {/* 진단 결과 + 모델 선택 — phase=idle 또는 done */}
          {(phase === "idle" || phase === "done") && (
            <DiagnosisCard diag={diag} model={model} />
          )}

          {/* 모델 선택 — idle 단계에서만 */}
          {phase === "idle" && !allReady && (
            <ModelPicker model={model} onChange={setModel} />
          )}

          {/* 계획 체크리스트 — idle / running / error */}
          {phase !== "done" && (plan.todo.length > 0 || plan.skipped.length > 0) && (
            <PlanList plan={plan} stepStates={stepStates} activeStep={activeStep} />
          )}

          {/* 활성 단계 로그 — running */}
          {phase === "running" && activeStep && (
            <LogBox
              title={STEP_LABEL[activeStep]}
              logs={stepStates[activeStep]?.logs ?? []}
              ref={logBoxRef}
            />
          )}

          {/* 에러 메시지 */}
          {phase === "error" && errorMsg && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
              <p className="font-semibold">자동 설정 중 문제가 발생했습니다.</p>
              <p className="mt-1 break-all">{errorMsg}</p>
              {(stepStates[STEP.INSTALL_OLLAMA]?.status === "error" ||
                stepStates[STEP.START_OLLAMA]?.status === "error") && (
                <p className="mt-2">
                  Ollama를 직접 설치/실행한 뒤 [재진단]을 눌러 주세요.{" "}
                  <a
                    href="https://ollama.com/download"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 underline"
                  >
                    ollama.com/download
                    <ExternalLink className="h-3 w-3" />
                  </a>
                  {isMac() && (
                    <span className="block mt-1 opacity-80">
                      Mac 자동 설치는 Homebrew(<code className="font-mono">brew</code>)가 필요합니다.
                    </span>
                  )}
                </p>
              )}
            </div>
          )}

          {/* 완료 */}
          {phase === "done" && allReady && (
            <div className="rounded-md border border-green-300 bg-green-50/60 p-3 text-sm text-green-800 dark:border-green-900/40 dark:bg-green-950/30 dark:text-green-300">
              <div className="flex items-center gap-2 font-semibold">
                <Check className="h-4 w-4" />
                로컬 AI가 준비됐습니다.
              </div>
              <p className="mt-1 text-xs">
                OpenClaw 게이트웨이가 18789에서 응답하고, Ollama({model})이 OpenClaw의 기본 모델로 등록됐습니다.
              </p>
            </div>
          )}

          {/* 액션 버튼 */}
          <div className="flex items-center justify-between gap-2 pt-1">
            <button
              type="button"
              onClick={handleOpenGuide}
              className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              수동 설치 안내 보기
            </button>
            <div className="flex gap-2">
              {phase === "idle" && (
                <>
                  {allReady ? (
                    <>
                      <Button variant="outline" size="sm" onClick={handleClose}>
                        닫기
                      </Button>
                      <Button size="sm" onClick={runAll}>
                        <Zap className="mr-1.5 h-3.5 w-3.5" />
                        프롬프트 대화 검증
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button variant="outline" size="sm" onClick={handleLater}>
                        나중에
                      </Button>
                      <Button size="sm" onClick={runAll}>
                        <Zap className="mr-1.5 h-3.5 w-3.5" />
                        모두 자동 설정
                      </Button>
                    </>
                  )}
                </>
              )}
              {phase === "running" && (
                <Button variant="outline" size="sm" onClick={cancelAll}>
                  중단
                </Button>
              )}
              {phase === "error" && (
                <>
                  <Button variant="outline" size="sm" onClick={handleClose}>
                    닫기
                  </Button>
                  <Button size="sm" onClick={handleRediagnose}>
                    재진단
                  </Button>
                  <Button size="sm" onClick={runAll}>
                    재시도
                  </Button>
                </>
              )}
              {phase === "done" && (
                <Button size="sm" onClick={handleClose}>
                  완료
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 하위 컴포넌트 ──────────────────────────────────────────────────────────

function DiagnosisCard({ diag, model }) {
  const ocInst = !!diag.ocInstalled?.installed;
  const ocRun = diag.oc?.state === "running";
  const ollInst = !!diag.oll?.installed;
  const ollRun = !!diag.oll?.running;
  const modelInst = hasModelInstalled(diag.oll?.models, model);

  const items = [
    { label: "OpenClaw 설치", ok: ocInst, hint: diag.ocInstalled?.version },
    { label: "OpenClaw 게이트웨이 (18789)", ok: ocRun },
    { label: "Ollama 설치", ok: ollInst, hint: diag.oll?.version },
    { label: "Ollama 데몬 (11434)", ok: ollRun },
    { label: `Ollama 모델 ${model}`, ok: modelInst },
  ];

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="mb-2 text-xs font-semibold text-muted-foreground">현재 상태</p>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-2 text-xs">
            <StatusDot ok={it.ok} />
            <span className={it.ok ? "text-foreground" : "text-muted-foreground"}>
              {it.label}
            </span>
            {it.hint && (
              <code className="ml-1 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                {it.hint}
              </code>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusDot({ ok }) {
  return (
    <span
      className={`inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full ${
        ok ? "bg-green-500/20 text-green-600" : "bg-amber-500/20 text-amber-600"
      }`}
    >
      {ok ? <Check className="h-2.5 w-2.5" /> : <span className="text-[8px]">○</span>}
    </span>
  );
}

function ModelPicker({ model, onChange }) {
  const [custom, setCustom] = useState(false);
  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
        <Download className="h-3.5 w-3.5 text-primary" />
        Ollama 모델 선택
      </p>
      <div className="space-y-1.5">
        {RECOMMENDED_MODELS.map((m) => (
          <label
            key={m.id}
            className={`flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
              model === m.id && !custom
                ? "border-primary bg-primary/5"
                : "border-border hover:bg-muted/50"
            }`}
          >
            <input
              type="radio"
              name="ollama-model"
              checked={model === m.id && !custom}
              onChange={() => {
                onChange(m.id);
                setCustom(false);
              }}
              className="mt-0.5"
            />
            <div className="flex-1">
              <div className="font-medium">{m.label}</div>
              <div className="text-muted-foreground">{m.note}</div>
            </div>
          </label>
        ))}
        <label
          className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
            custom ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50"
          }`}
        >
          <input
            type="radio"
            name="ollama-model"
            checked={custom}
            onChange={() => setCustom(true)}
          />
          <span className="font-medium">직접 입력:</span>
          <input
            type="text"
            disabled={!custom}
            value={custom ? model : ""}
            onChange={(e) => onChange(e.target.value)}
            placeholder="qwen2.5:14b"
            className="flex-1 rounded border border-input bg-background px-2 py-0.5 font-mono text-[11px] disabled:opacity-50"
          />
        </label>
      </div>
    </div>
  );
}

function PlanList({ plan, stepStates, activeStep }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      {plan.skipped.length > 0 && (
        <>
          <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
            이미 완료된 항목 ({plan.skipped.length}) — 다시 수행하지 않습니다
          </p>
          <ul className="mb-3 space-y-1">
            {plan.skipped.map((id) => (
              <li
                key={id}
                className="flex items-center gap-2 text-xs text-muted-foreground"
              >
                <Check className="h-3.5 w-3.5 shrink-0 text-green-600/70" />
                <span>{STEP_LABEL[id]}</span>
                <span className="ml-auto text-[10px] uppercase tracking-wide text-green-700/70 dark:text-green-400/70">
                  설치됨
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
        실행 계획 ({plan.todo.length})
      </p>
      <ul className="space-y-1">
        {plan.todo.map((id) => {
          const s = stepStates[id];
          const status = s?.status ?? "pending";
          return (
            <li key={id} className="flex items-center gap-2 text-xs">
              <StepIcon status={status} active={activeStep === id} />
              <span
                className={
                  status === "done"
                    ? "text-foreground"
                    : status === "error"
                    ? "text-destructive"
                    : status === "running"
                    ? "text-primary"
                    : "text-muted-foreground"
                }
              >
                {STEP_LABEL[id]}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function StepIcon({ status, active }) {
  if (status === "done") return <Check className="h-3.5 w-3.5 shrink-0 text-green-600" />;
  if (status === "error") return <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />;
  if (status === "running" || active)
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />;
  return <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />;
}

const LogBox = React.forwardRef(function LogBox({ title, logs }, ref) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium">{title}</p>
      <div
        ref={ref}
        className="max-h-[200px] overflow-y-auto rounded-md border border-border bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-100"
      >
        {logs.length === 0 ? (
          <span className="text-zinc-500">출력 대기 중...</span>
        ) : (
          logs.map((l, i) => (
            <div
              key={i}
              className={
                l.kind === "err"
                  ? "text-amber-300"
                  : l.kind === "info"
                  ? "text-zinc-400"
                  : "text-zinc-100"
              }
            >
              {l.text}
            </div>
          ))
        )}
      </div>
    </div>
  );
});
