/**
 * LocalAISetupWizard.jsx — "OpenClaw + Ollama 로컬 모델"을 한 번에 자동 설정.
 *
 * 진단 → 계획 → 자동 실행 → 검증 단일 모달.
 * 단계:
 *   0. Node.js 설치 (Windows: winget / macOS: brew) — 미설치 시 (OpenClaw 선행 조건)
 *   1. OpenClaw 설치 (`npm install -g openclaw@latest`) — 미설치 시
 *   2. OpenClaw 게이트웨이 시작 (`openclaw gateway --port 18789`) — 미실행 시
 *   3. Ollama 설치 (macOS: brew / Windows: winget) — 미설치 시
 *   4. Ollama 데몬 실행 (macOS: brew services / Windows: Ollama 앱 시작) — 미실행 시
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
import { StatusRow } from "@/components/ui/status";
import useAppStore from "@/store/appStore";
import useStatusStore from "@/store/statusStore";
import {
  STATUS_MODULES,
  refreshAllModules,
  getDerivedDiag,
} from "@/lib/statusManager";
import {
  openclawUseOllama,
  agentChat,
  installerCancel,
  saveLLMSettings,
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
import {
  QWEN3_OPENCLAW_PRESET,
  applyLocalStackPreset,
} from "@/lib/localStack";
import { toUserMessage } from "@/lib/errorMessages";

/** 진단/검증 후 잠깐 기다림 (ms) */
const DETECTION_DELAY_MS = 1000;

// ── 단계 정의 (순수 로직은 @/lib/localAISetup.js로 분리, 여기는 wizard 전용 상수만) ──

/** 프롬프트 검증 시 보낼 핑 메시지 — 응답 형식·언어는 모델마다 다르므로 *비어있지 않은 응답*만 검사 */
const PING_MESSAGE = QWEN3_OPENCLAW_PRESET.pingMessage;
const PROMPT_TEST_TIMEOUT_MS = 120_000;

function isGatewayUnavailableError(err) {
  const msg = String(err?.message ?? err ?? "");
  return (
    msg.includes("HTTP 503") ||
    msg.includes("OpenClaw 게이트웨이가 실행되지 않았습니다")
  );
}

// ── 메인 컴포넌트 ───────────────────────────────────────────────────────────

export default function LocalAISetupWizard() {
  const onboardingComplete = useAppStore((s) => s.onboardingComplete);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  // 새 중앙 상태 store에서 모듈 데이터 구독 — App 루트의 useStatusPoller가 자동 갱신.
  // 이 wizard는 더 이상 자체 fetch를 하지 않고 store의 데이터를 읽기만 한다.
  const nodeModule = useStatusStore((s) => s.modules.node);
  const ocModule = useStatusStore((s) => s.modules.openclaw);
  const ollamaModule = useStatusStore((s) => s.modules.ollama);

  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [phase, setPhase] = useState("idle"); // idle | running | done | error
  const [stepStates, setStepStates] = useState({});
  // stepStates[id] = { status: 'pending'|'running'|'done'|'skipped'|'error', logs: [{kind, text}] }
  const [activeStep, setActiveStep] = useState(null);
  const [errorMsg, setErrorMsg] = useState("");
  // 진행 중인 설치는 Rust 측 installer에서 PID로 관리하므로 frontend childRef 불필요.
  // cancelRef.current는 runAll 루프의 다음 step 진입 직전 검사용으로만 사용.
  const cancelRef = useRef(false);

  // store 모듈 상태 → buildPlan/isAllReady가 받는 diag 형태로 변환 (호환성).
  // 두 모듈 모두 unknown(=한 번도 check 안 됨)이면 diag=null로 두어 로딩 표시.
  const diag = useMemo(() => {
    if (ocModule.state === "unknown" && ollamaModule.state === "unknown") {
      return null;
    }
    return {
      node: { installed: nodeModule.installed, version: nodeModule.version },
      oc: {
        state: ocModule.running ? "running" : "stopped",
        message: ocModule.message,
        port: ocModule.port,
      },
      ocInstalled: { installed: ocModule.installed, version: ocModule.version },
      oll: {
        installed: ollamaModule.installed,
        running: ollamaModule.running,
        models: ollamaModule.models,
        version: ollamaModule.version,
      },
    };
  }, [nodeModule, ocModule, ollamaModule]);

  // 진단 트리거 — 실제 fetch는 statusManager가 담당, 결과는 store로 자동 반영.
  // 단계별 실행 후 readiness 판정에 사용하기 위해 fresh diag도 반환한다.
  const runDiagnosis = useCallback(async () => {
    await refreshAllModules();
    return getDerivedDiag();
  }, []);

  // 앱 시작 직후 1회 진단은 App.jsx의 useStatusPoller가 처리 — 여기선 별도 트리거 불필요.

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

  // 글로벌 이벤트 — Dashboard "지금 자동 설치" 등에서 다시 열기.
  // 모달을 *즉시* 열어 사용자에게 피드백을 주고, 진단은 백그라운드로 갱신한다.
  // (이전에는 진단을 await 없이 호출하면서 setOpen만 동기로 실행했는데,
  //  렌더 가드 `if (!open || !diag) return null` 때문에 첫 진단이 끝날 때까지
  //  아무 것도 보이지 않는 문제가 있었다.)
  useEffect(() => {
    const handler = () => {
      setDismissed(false);
      setOpen(true);
      // 이전 단계별 상태를 초기화 — 새로 열린 세션에는 깨끗한 화면을 보여준다
      setStepStates({});
      setPhase("idle");
      setErrorMsg("");
      runDiagnosis();
    };
    window.addEventListener("officeclaw:open-local-ai-setup", handler);
    // 기존 이벤트 호환
    window.addEventListener("officeclaw:open-openclaw-install", handler);
    return () => {
      window.removeEventListener("officeclaw:open-local-ai-setup", handler);
      window.removeEventListener("officeclaw:open-openclaw-install", handler);
    };
  }, [runDiagnosis]);

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

  // Tauri installer 명령 결과 → throw + state 첨부.
  // 실패 시 errorResult를 stepStates에 함께 저장해 PlanStepRow가 EACCES/stderr_tail/manual_command를
  // 의미 있게 렌더할 수 있도록 한다.
  const handleInstallResult = useCallback(
    (stepId, result) => {
      if (result?.ok) return;
      // 실패 — 풍부한 컨텍스트 첨부
      const err = new Error(result?.message || "설치 실패");
      err.installResult = result;
      throw err;
    },
    []
  );

  // installer:log 이벤트 → pushLog로 라우팅.
  // Rust 측 streaming이 emit하는 라인을 받아 PlanStepRow의 인라인 로그에 표시.
  useEffect(() => {
    let unlisten = null;
    let cancelled = false;
    (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const off = await listen("installer:log", (event) => {
          const payload = event?.payload || {};
          const { step, kind, text } = payload;
          if (!step || typeof text !== "string") return;
          // Rust kind ("stdout"/"stderr"/"info") → 기존 frontend kind 매핑
          const mappedKind = kind === "stderr" ? "err" : kind === "info" ? "info" : "out";
          pushLog(step, mappedKind, text);
        });
        if (cancelled) {
          off();
        } else {
          unlisten = off;
        }
      } catch {
        // Tauri 비-환경(브라우저 단독 테스트 등) — 조용히 무시
      }
    })();
    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, [pushLog]);

  // 개별 단계 실행
  const runStep = useCallback(
    async (stepId) => {
      if (cancelRef.current) return false;
      setActiveStep(stepId);
      setStepStatus(stepId, "running");
      pushLog(stepId, "info", `${STEP_LABEL[stepId]} 시작`);

      // 모든 단계는 statusManager의 액션을 호출 — 액션 내부에서:
      //   1) 설치/시작 명령 실행 (Rust $SHELL -lc 경유)
      //   2) 자동으로 check() 호출 → 설치 위치/실행 상태 검증 → store 갱신
      // 결과는 InstallResult — handleInstallResult가 실패 시 throw + 컨텍스트 첨부.
      try {
        switch (stepId) {
          case STEP.INSTALL_NODE: {
            const result = await STATUS_MODULES.node.install();
            handleInstallResult(stepId, result);
            break;
          }
          case STEP.INSTALL_OC: {
            const result = await STATUS_MODULES.openclaw.install();
            handleInstallResult(stepId, result);
            break;
          }
          case STEP.START_OC: {
            pushLog(stepId, "info", "OpenClaw를 시작하고 있어요...");
            const result = await STATUS_MODULES.openclaw.start();
            if (result?.state !== "running") {
              throw new Error(result?.message || "OpenClaw를 시작하지 못했어요");
            }
            pushLog(stepId, "info", "✓ OpenClaw가 응답하고 있어요");
            break;
          }
          case STEP.INSTALL_OLLAMA: {
            const result = await STATUS_MODULES.ollama.install();
            handleInstallResult(stepId, result);
            break;
          }
          case STEP.START_OLLAMA: {
            const result = await STATUS_MODULES.ollama.start();
            handleInstallResult(stepId, result);
            // start()는 내부에서 ready polling + check를 이미 수행. 추가 확인 불필요.
            break;
          }
          case STEP.PULL_MODEL: {
            const result = await STATUS_MODULES.ollama.pullModel(model);
            handleInstallResult(stepId, result);
            break;
          }
          case STEP.CONFIG_OC: {
            pushLog(stepId, "info", `AI 모델(${model})을 OpenClaw에 연결하고 있어요...`);
            try {
              await openclawUseOllama(model);
              pushLog(stepId, "info", "✓ 연결 완료");
            } catch (err) {
              const msg = String(err?.message ?? err);
              // Windows GUI 환경에서 openclaw CLI 경로 해석 실패(os error 3) 케이스가 있다.
              // 이때도 실제 agent 경로가 정상 동작하면 불필요하게 setup 전체를 실패 처리하지 않는다.
              const pathLikeError =
                msg.includes("os error 3") || msg.includes("지정된 경로를 찾을 수 없습니다");
              if (!pathLikeError) {
                throw err;
              }
              pushLog(
                stepId,
                "info",
                "OpenClaw CLI 경로 확인에 실패했지만, 실제 AI 대화 경로로 연결 상태를 재검증합니다..."
              );
              const probe = await Promise.race([
                agentChat(PING_MESSAGE, null),
                new Promise((_, rej) =>
                  setTimeout(
                    () => rej(new Error("OpenClaw 연결 재검증이 시간 초과되었습니다.")),
                    20_000
                  )
                ),
              ]);
              const probeText = String(probe?.response ?? "").trim();
              if (!probeText) {
                throw err;
              }
              pushLog(stepId, "info", "✓ 대화 경로가 정상이라 연결 단계를 통과 처리합니다.");
            }
            break;
          }
          case STEP.PROMPT_TEST: {
            pushLog(stepId, "info", "AI에게 간단한 인사를 보내볼게요...");
            pushLog(stepId, "info", "테스트 전에 OpenClaw 게이트웨이 상태를 다시 확인합니다...");
            const ensureGateway = await STATUS_MODULES.openclaw.start();
            if (ensureGateway?.state !== "running") {
              throw new Error(
                ensureGateway?.message || "OpenClaw 게이트웨이를 준비하지 못해 AI 대화 테스트를 진행할 수 없어요."
              );
            }

            const askAgentWithTimeout = () =>
              Promise.race([
                agentChat(PING_MESSAGE, null),
                new Promise((_, rej) =>
                  setTimeout(
                    () => rej(new Error("AI 응답이 너무 오래 걸려요. 처음 모델을 띄우면 1-2분 걸릴 수 있어요.")),
                    PROMPT_TEST_TIMEOUT_MS
                  )
                ),
              ]);

            let reply;
            try {
              reply = await askAgentWithTimeout();
            } catch (err) {
              if (!isGatewayUnavailableError(err)) {
                throw err;
              }
              pushLog(stepId, "info", "OpenClaw 게이트웨이가 꺼져 있어 자동으로 다시 시작합니다...");
              const startResult = await STATUS_MODULES.openclaw.start();
              if (startResult?.state !== "running") {
                throw new Error(startResult?.message || "OpenClaw 게이트웨이를 자동으로 다시 시작하지 못했어요.");
              }
              // 게이트웨이 재기동 직후 초기화 시간을 짧게 준다.
              await new Promise((r) => setTimeout(r, 1200));
              pushLog(stepId, "info", "게이트웨이 재시작 완료. AI 대화 테스트를 다시 시도합니다.");
              reply = await askAgentWithTimeout();
            }
            const text = String(reply?.response ?? "").trim();
            if (!text) {
              throw new Error("AI가 응답하지 않았어요");
            }
            const preview = text.length > 200 ? `${text.slice(0, 200)}…` : text;
            pushLog(stepId, "out", `AI: ${preview}`);
            pushLog(stepId, "info", "✓ AI 대화가 정상적으로 동작해요");
            break;
          }
          default:
            throw new Error("알 수 없는 단계예요");
        }
        setStepStatus(stepId, "done");
        return true;
      } catch (err) {
        const msg = String(err?.message ?? err);
        const userMsg = toUserMessage(msg);
        pushLog(stepId, "err", `✗ ${userMsg}`);
        // err.installResult가 있으면 stepStates에 함께 저장 — PlanStepRow가
        // EACCES 안내 / stderr_tail / manual_command 복사 UI를 렌더.
        const installResult = err?.installResult;
        setStepStates((prev) => ({
          ...prev,
          [stepId]: {
            ...(prev[stepId] || { logs: [] }),
            status: "error",
            installResult,
          },
        }));
        setErrorMsg(userMsg);
        return false;
      }
    },
    [model, pushLog, handleInstallResult, setStepStatus]
  );

  /** 설정 완료 시 sidecar·앱 store에 Ollama 모델 동기화 */
  const persistLlmForModel = useCallback(
    async (ollamaModel) => {
      const match = RECOMMENDED_MODELS.find((m) => m.id === ollamaModel);
      if (match?.presetId) {
        await applyLocalStackPreset(match.presetId, {
          saveLLMSettings,
          setLLMConfig,
        });
        return;
      }
      const config = { provider: "ollama", model: ollamaModel };
      await saveLLMSettings(config);
      setLLMConfig(config);
    },
    [setLLMConfig]
  );

  const finalizeSetupIfReady = useCallback(
    async (freshDiag) => {
      if (!isAllReady(freshDiag, model)) return false;
      try {
        await persistLlmForModel(model);
      } catch (err) {
        setErrorMsg(String(err?.message ?? err));
        setPhase("error");
        return false;
      }
      return true;
    },
    [model, persistLlmForModel]
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
    const fresh = await runDiagnosis();
    if (await finalizeSetupIfReady(fresh)) {
      setPhase("done");
    } else {
      setPhase("idle");
    }
  }, [plan.todo, runDiagnosis, runStep, model, finalizeSetupIfReady]);

  // 단일 단계 실행 — 사용자가 PlanList의 각 [실행] 버튼을 눌렀을 때 호출.
  //
  //   1) cancelRef/phase=running 설정으로 다른 버튼 비활성화
  //   2) 해당 stepId의 이전 로그/상태 초기화 후 runStep 호출
  //   3) 성공 시 즉시 진단 재실행 → 다음 todo 갱신
  //   4) 모든 사전조건이 충족(isAllReady)되면 phase=done, 아니면 phase=idle
  //   5) 실패 시 phase=error — PlanList의 [재시도] 버튼은 그대로 동작
  const runSingleStep = useCallback(
    async (stepId) => {
      cancelRef.current = false;
      setPhase("running");
      setErrorMsg("");
      // 이 단계의 이전 상태(특히 logs)만 비워 새로 시작
      setStepStates((prev) => {
        const next = { ...prev };
        delete next[stepId];
        return next;
      });

      const ok = await runStep(stepId);
      setActiveStep(null);

      if (!ok || cancelRef.current) {
        setPhase("error");
        return;
      }

      const fresh = await runDiagnosis();
      if (await finalizeSetupIfReady(fresh)) {
        setPhase("done");
      } else {
        setPhase("idle");
      }
    },
    [runStep, runDiagnosis, model, finalizeSetupIfReady]
  );

  // 취소 — 진행 중인 Rust 측 자식 프로세스에 SIGTERM 전송.
  // installer.rs가 저장된 PID에 kill -TERM을 보내고, child.wait이 비정상 종료로 리턴됨.
  // cancelRef.current는 runAll의 다음 step 진입 전 검사에서 중단 신호로 사용.
  const cancelAll = useCallback(async () => {
    cancelRef.current = true;
    try {
      await installerCancel();
    } catch {
      // ignore — 이미 종료됐거나 PID 없음
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

  if (!open) return null;

  // diag가 아직 없으면 wizard는 열되 "확인 중..." 표시 (이전 동작은 완전 숨김이었음)
  const diagReady = !!diag;
  const allReady = diagReady && isAllReady(diag, model);

  return (
    <div className="fixed inset-0 z-[1100] overflow-y-auto bg-black/50">
      <div
        className="flex min-h-full items-center justify-center p-4"
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
            <h2 className="text-sm font-semibold">AI 자동 설정</h2>
          </div>
          <button
            type="button"
            onClick={phase === "running" ? undefined : handleClose}
            disabled={phase === "running"}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
            aria-label="닫기"
            title={phase === "running" ? "진행 중에는 닫을 수 없어요" : "닫기"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 p-5">
          {/* 진단 미완료 상태 — 초기 로딩 표시 */}
          {!diagReady && (
            <div className="flex items-center gap-3 rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              현재 상태를 확인하고 있습니다...
            </div>
          )}

          {/* 진단 결과 — 항상 표시 (단계별 클릭 흐름에서 사용자가 현재 상태를 계속 봐야 함) */}
          {diagReady && <DiagnosisCard diag={diag} model={model} />}

          {/* 모델 선택 — idle 단계에서만 (running 중 모델 변경 방지) */}
          {diagReady && phase === "idle" && !allReady && (
            <ModelPicker model={model} onChange={setModel} />
          )}

          {/* 계획 체크리스트 — 단계별 [실행] 버튼 + 인라인 로그.
              완료된 단계는 글자로만, 미완료 단계는 클릭 가능한 버튼으로 표시. */}
          {diagReady && phase !== "done" &&
            (plan.todo.length > 0 || plan.skipped.length > 0) && (
              <PlanList
                plan={plan}
                stepStates={stepStates}
                activeStep={activeStep}
                phase={phase}
                onRunStep={runSingleStep}
                onRunAll={runAll}
                onCancel={cancelAll}
              />
            )}

          {/* 에러 메시지 — 추가 안내 (Ollama 자동 설치 미지원 등). 자세한 사유는 각 단계의 [기술 정보 보기]에서 확인. */}
          {phase === "error" && errorMsg && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
              <p className="font-semibold">작업 중 문제가 발생했어요.</p>
              <p className="mt-1">{errorMsg}</p>
              {(stepStates[STEP.INSTALL_OLLAMA]?.status === "error" ||
                stepStates[STEP.START_OLLAMA]?.status === "error") && (
                <p className="mt-2">
                  자동 설치/실행이 실패하면 Ollama를 직접 설치/실행한 뒤 [재진단]을 눌러주세요.{" "}
                  <a
                    href="https://ollama.com/download"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 underline"
                  >
                    Ollama 다운로드
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </p>
              )}
            </div>
          )}

          {/* 완료 */}
          {phase === "done" && allReady && (
            <div className="rounded-md border border-green-300 bg-green-50/60 p-3 text-sm text-green-800 dark:border-green-900/40 dark:bg-green-950/30 dark:text-green-300">
              <div className="flex items-center gap-2 font-semibold">
                <Check className="h-4 w-4" />
                AI 사용 준비가 끝났어요!
              </div>
              <p className="mt-1 text-xs">
                {model} 모델로 AI 대화를 시작할 수 있어요.
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
              {phase === "idle" && diagReady && (
                <>
                  {allReady ? (
                    <>
                      <Button variant="outline" size="sm" onClick={handleClose}>
                        닫기
                      </Button>
                      <Button size="sm" onClick={runAll}>
                        <Zap className="mr-1.5 h-3.5 w-3.5" />
                        AI 대화 테스트
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button variant="outline" size="sm" onClick={handleLater}>
                        나중에
                      </Button>
                    </>
                  )}
                </>
              )}
              {phase === "running" && (
                <Button variant="outline" size="sm" onClick={cancelAll}>
                  전체 중단
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
                  {/* 단계별 [재시도] 버튼이 PlanList에 있으므로 하단 일괄 재시도는 제거.
                      특정 단계만 다시 실행하려면 해당 행의 [재시도] 클릭. */}
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
    </div>
  );
}

// ── 하위 컴포넌트 ──────────────────────────────────────────────────────────

// 진단 카드 — 사용자에게 *세부* 항목별 상태를 보여주는 유일한 화면.
// 다른 위치(StatusBar/Dashboard)는 "OpenClaw 준비됨/문제 있음" 단일 표시지만,
// 여기서는 진단 목적이므로 설치/실행/모델을 모두 분리해서 보여준다.
// 통일된 톤 시스템(STATUS_TONE) 사용 — "준비됨"(ok) / "문제 있음"(warning).
function DiagnosisCard({ diag, model }) {
  const nodeInst = !!diag.node?.installed;
  const ocInst = !!diag.ocInstalled?.installed;
  const ocRun = diag.oc?.state === "running";
  const ollInst = !!diag.oll?.installed;
  const ollRun = !!diag.oll?.running;
  const modelInst = hasModelInstalled(diag.oll?.models, model);

  // 사용자 친화적 표현 — 포트/데몬/게이트웨이 같은 용어는 표시하지 않음.
  // 버전은 hint(우측 메타)로만 작게 표시.
  const items = [
    { label: "Node.js 설치", ok: nodeInst, hint: diag.node?.version },
    { label: "OpenClaw 설치", ok: ocInst, hint: diag.ocInstalled?.version },
    { label: "OpenClaw 실행", ok: ocRun },
    { label: "Ollama 설치", ok: ollInst, hint: diag.oll?.version },
    { label: "Ollama 실행", ok: ollRun },
    { label: `AI 모델 (${model})`, ok: modelInst },
  ];

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="mb-2 text-xs font-semibold text-muted-foreground">현재 상태</p>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <StatusRow
            key={i}
            tone={it.ok ? "ok" : "warning"}
            title={it.label}
            right={
              it.hint ? (
                <code className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                  {it.hint}
                </code>
              ) : null
            }
          />
        ))}
      </ul>
    </div>
  );
}

function ModelPicker({ model, onChange }) {
  const [custom, setCustom] = useState(false);
  return (
    <div className="rounded-md border border-primary/30 bg-primary/5 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
        <Download className="h-3.5 w-3.5 text-primary" />
        AI 모델 선택
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
            placeholder="qwen3:8b"
            className="flex-1 rounded border border-input bg-background px-2 py-0.5 font-mono text-[11px] disabled:opacity-50"
          />
        </label>
      </div>
    </div>
  );
}

/**
 * 단계별 실행 계획 UI.
 *
 * 핵심 UX:
 *   - 완료(skipped)된 단계는 글자만 표시 (다시 수행하지 않음)
 *   - 미완료(todo)된 단계는 [실행] / [재시도] 버튼으로 클릭 가능
 *   - 실행 중 단계는 인라인 로그가 펼쳐짐 + [중단] 버튼
 *   - 실패한 단계는 인라인 로그 + [재시도] 버튼
 *   - 다른 단계가 실행 중이면 모든 버튼 비활성화 (동시 실행 방지)
 */
function PlanList({ plan, stepStates, activeStep, phase, onRunStep, onRunAll, onCancel }) {
  const isAnyRunning = phase === "running";

  return (
    <div className="rounded-md border border-border bg-background p-3">
      {plan.skipped.length > 0 && (
        <>
          <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
            이미 완료된 항목 ({plan.skipped.length}) — 다시 실행하지 않아요
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
                  완료
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
        해야 할 작업 ({plan.todo.length}) — 단계별로 [실행]을 눌러주세요
      </p>
      <ul className="space-y-1.5">
        {plan.todo.map((id, idx) => {
          const prevIds = plan.todo.slice(0, idx);
          const prevDone = prevIds.every((pid) => stepStates[pid]?.status === "done");
          return (
            <PlanStepRow
              key={id}
              stepId={id}
              state={stepStates[id]}
              isActive={activeStep === id}
              isAnyRunning={isAnyRunning}
              canRun={prevDone}
              onRun={() => onRunStep(id)}
              onCancel={onCancel}
            />
          );
        })}
      </ul>
      {plan.todo.length > 0 && (
        <div className="mt-2 flex justify-end">
          <Button size="sm" onClick={onRunAll} disabled={isAnyRunning}>
            <Zap className="mr-1 h-3 w-3" />
            전체 실행 테스트
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * 단일 단계 행 — 라벨 + 상태 + [실행/재시도/중단] 버튼 + 인라인 로그.
 *
 * 로그/세부 정보는 기본적으로 숨김(토글). 일반 사용자가 압도되지 않도록.
 */
function PlanStepRow({ stepId, state, isActive, isAnyRunning, canRun, onRun, onCancel }) {
  const status = state?.status ?? "pending";
  const logs = state?.logs ?? [];
  const logBoxRef = useRef(null);
  const [showLogs, setShowLogs] = useState(false);
  const isRunning = status === "running" && isActive;
  const isDone = status === "done";
  const isError = status === "error";

  // 활성 단계 로그 자동 스크롤
  useEffect(() => {
    if (logBoxRef.current && isRunning && showLogs) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [logs, isRunning, showLogs]);

  // 우측 버튼 결정
  let actionButton = null;
  if (isRunning) {
    actionButton = (
      <Button size="sm" variant="outline" onClick={onCancel}>
        중단
      </Button>
    );
  } else if (isDone) {
    // 완료된 단계는 버튼 없음 — 사용자 요청대로 글자만 표시
    actionButton = null;
  } else {
    actionButton = (
      <Button
        size="sm"
        variant={isError ? "outline" : "default"}
        onClick={onRun}
        disabled={isAnyRunning || !canRun}
        title={!canRun ? "이전 단계를 먼저 완료해 주세요." : undefined}
      >
        <Zap className="mr-1 h-3 w-3" />
        {isError ? "재시도" : "실행"}
      </Button>
    );
  }

  // 상태 라벨 (글자)
  let statusLabel = null;
  if (isDone) {
    statusLabel = (
      <span className="text-[10px] uppercase tracking-wide text-green-700/80 dark:text-green-400/80">
        완료
      </span>
    );
  } else if (isError) {
    statusLabel = (
      <span className="text-[10px] uppercase tracking-wide text-destructive">실패</span>
    );
  } else if (isRunning) {
    statusLabel = (
      <span className="text-[10px] uppercase tracking-wide text-primary">실행 중</span>
    );
  }

  return (
    <li className="rounded-md border border-border/60 bg-muted/20 p-2">
      <div className="flex items-center gap-2 text-xs">
        <StepIcon status={status} active={isActive} />
        <span
          className={
            isDone
              ? "text-foreground"
              : isError
              ? "text-destructive"
              : isRunning
              ? "text-primary font-medium"
              : "text-foreground/80"
          }
        >
          {STEP_LABEL[stepId]}
        </span>
        {statusLabel}
        <span className="ml-auto">{actionButton}</span>
      </div>

      {/* 진행 중 안내 메시지 — 일반 사용자가 보기엔 raw 로그가 부담스러우므로
          기본적으로 친화적 안내만 보여주고, raw 로그는 "상세 보기"로 숨김. */}
      {isRunning && (
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted-foreground">
            진행 중이에요. 보통 1-2분 정도 걸려요.
          </span>
          {logs.length > 0 && (
            <button
              type="button"
              onClick={() => setShowLogs((v) => !v)}
              className="text-[11px] text-primary underline-offset-2 hover:underline"
            >
              {showLogs ? "상세 숨기기" : "상세 보기"}
            </button>
          )}
        </div>
      )}

      {/* 실패 시 toggle 영역 */}
      {isError && logs.length > 0 && (
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            onClick={() => setShowLogs((v) => !v)}
            className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {showLogs ? "기술 정보 숨기기" : "기술 정보 보기"}
          </button>
        </div>
      )}

      {/* raw 로그 박스 — 토글 켜진 경우에만 노출. 개발자 도움이 필요한 사용자를 위한 도구. */}
      {(isRunning || isError) && showLogs && logs.length > 0 && (
        <div
          ref={logBoxRef}
          className="mt-2 max-h-[200px] overflow-y-auto rounded-md border border-border bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed text-zinc-100"
        >
          {logs.map((l, i) => (
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
          ))}
        </div>
      )}

      {/* 실패 시 풍부한 컨텍스트: EACCES 안내 / stderr 꼬리 / 수동 명령 복사.
          Rust installer가 채워준 state.installResult를 사용 — 단순 "종료 코드 N"보다
          훨씬 의미 있는 정보를 제공한다. */}
      {isError && state?.installResult && (
        <FailureContext result={state.installResult} stepId={stepId} />
      )}
    </li>
  );
}

/**
 * 실패 컨텍스트 패널 — Rust installer가 넘긴 InstallResult를 표시.
 *
 *   - EACCES → 빨간 경고 + "관리자 권한 필요" + nvm 대안 안내
 *   - stderr_tail → 마지막 N줄 (npm ERR! / brew error: 같은 핵심 정보)
 *   - manual_command → 한 줄 복사 버튼
 */
function FailureContext({ result, stepId }) {
  const [copied, setCopied] = useState(false);
  const [showTech, setShowTech] = useState(false);

  const handleCopy = async () => {
    const cmd = result?.eacces && stepId === "install-oc"
      ? `sudo ${result.manual_command.replace(/^sudo\s+/, "")}`
      : result.manual_command;
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore
    }
  };

  // 사용자가 한 번에 보면 좋을 핵심 정보만 노출.
  // exit code / stderr 같은 기술 정보는 "기술 정보 보기" 토글로 숨김.
  const hasTech =
    result.code != null ||
    (Array.isArray(result.stderr_tail) && result.stderr_tail.length > 0);

  return (
    <div className="mt-2 space-y-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-[11px]">
      {result.eacces ? (
        <div className="text-destructive">
          <p className="font-semibold">관리자 권한이 필요해요.</p>
          <p className="mt-1 text-destructive/80">
            앱에서는 관리자 비밀번호를 입력할 수 없어요. 아래 명령을 터미널에서 직접 실행해주세요.
          </p>
        </div>
      ) : (
        <div className="text-destructive">
          <p className="font-semibold">설치에 실패했어요.</p>
          <p className="mt-1 text-destructive/80">
            아래 명령을 터미널에서 직접 실행해보거나, 잠시 후 다시 시도해주세요.
          </p>
        </div>
      )}

      {result.manual_command && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            터미널에서 직접 실행
          </p>
          <div className="flex items-center gap-2 rounded border border-border bg-background px-2 py-1 font-mono text-[11px]">
            <code className="flex-1 select-all break-all">
              {result.eacces && stepId === "install-oc"
                ? `sudo ${result.manual_command.replace(/^sudo\s+/, "")}`
                : result.manual_command}
            </code>
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex shrink-0 items-center gap-1 rounded border border-border bg-muted px-2 py-0.5 text-[10px] hover:bg-muted/80"
            >
              {copied ? "복사됨" : "복사"}
            </button>
          </div>
          {result.eacces && stepId === "install-oc" && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              또는 nvm을 사용하면 관리자 권한 없이도 설치할 수 있어요:{" "}
              <a
                href="https://github.com/nvm-sh/nvm#installing-and-updating"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                nvm 설치 안내
              </a>
            </p>
          )}
        </div>
      )}

      {/* 기술 정보 토글 — 개발자나 문제 해결이 필요한 사용자가 펼쳐서 볼 수 있게. */}
      {hasTech && (
        <div className="border-t border-destructive/20 pt-1.5">
          <button
            type="button"
            onClick={() => setShowTech((v) => !v)}
            className="text-[10px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {showTech ? "기술 정보 숨기기" : "기술 정보 보기"}
          </button>
          {showTech && (
            <div className="mt-1.5 space-y-1">
              {result.code != null && (
                <p className="text-[10px] text-muted-foreground">
                  종료 코드: {result.code}
                </p>
              )}
              {Array.isArray(result.stderr_tail) && result.stderr_tail.length > 0 && (
                <pre className="whitespace-pre-wrap break-all rounded bg-zinc-950 p-1.5 font-mono text-[10px] text-amber-300">
                  {result.stderr_tail.slice(-8).join("\n")}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
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
