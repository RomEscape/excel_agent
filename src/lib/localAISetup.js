/**
 * LocalAISetupWizard의 *순수 로직* — UI 없음. 테스트 가능하도록 분리.
 *
 * - STEP/STEP_LABEL: 단계 식별자와 표시 라벨
 * - buildPlan: 진단 결과를 todo/skipped로 분할 (idempotent 핵심)
 * - isAllReady: 자동 노출 여부 결정용
 *
 * 진단 데이터 형태 (`diag`):
 *   {
 *     oc:           { state: 'running'|'stopped'|'error', port?, message? },
 *     ocInstalled:  { installed: boolean, version?: string|null },
 *     oll:          { installed: boolean, running: boolean, models: [{ name }] },
 *   }
 */

export const STEP = Object.freeze({
  INSTALL_OC: "install-oc",
  START_OC: "start-oc",
  INSTALL_OLLAMA: "install-ollama",
  START_OLLAMA: "start-ollama",
  PULL_MODEL: "pull-model",
  CONFIG_OC: "config-oc",
  PROMPT_TEST: "prompt-test",
});

export const STEP_LABEL = Object.freeze({
  [STEP.INSTALL_OC]: "OpenClaw 설치",
  [STEP.START_OC]: "OpenClaw 실행",
  [STEP.INSTALL_OLLAMA]: "Ollama 설치",
  [STEP.START_OLLAMA]: "Ollama 실행",
  [STEP.PULL_MODEL]: "AI 모델 다운로드",
  [STEP.CONFIG_OC]: "OpenClaw에 AI 모델 연결",
  [STEP.PROMPT_TEST]: "AI 대화 테스트",
});

/**
 * Office 문서(Excel/Docs/PowerPoint) 작업에 강점이 있는 가벼운 로컬 LLM 후보.
 * 첫 항목이 권장값.
 */
export const RECOMMENDED_MODELS = Object.freeze([
  {
    id: "phi3.5",
    label: "phi3.5 (권장, ~2.3GB)",
    note: "Microsoft 제작 · Excel/표/문서/PowerPoint 이해 학습 포함",
  },
  { id: "llama3.2", label: "llama3.2 (~2GB)", note: "가벼운 일반 용도" },
  { id: "qwen2.5:7b", label: "qwen2.5:7b (~4.5GB)", note: "한국어/추론 강함" },
  { id: "gemma2:2b", label: "gemma2:2b (~1.5GB)", note: "초경량" },
]);

export const DEFAULT_MODEL = "phi3.5";

/**
 * Ollama 모델 목록에 해당 모델이 이미 받아져 있는지 확인.
 * Ollama는 `phi3.5:latest` 같은 태그 접미사가 붙으므로 startsWith로 매칭.
 */
export function hasModelInstalled(models, model) {
  return (models || []).some((m) => String(m?.name || "").startsWith(model));
}

/**
 * 모든 사전 조건(설치/실행/모델)이 충족됐는지 — 자동 노출 차단용.
 * 프롬프트 검증(PROMPT_TEST)은 별개 — 매 세션 검증 가치가 있어 항상 plan에 포함.
 */
export function isAllReady(diag, model) {
  if (!diag) return false;
  const ocOK = diag.oc?.state === "running" && diag.ocInstalled?.installed;
  const ollOK = diag.oll?.installed && diag.oll?.running;
  const modelOK = hasModelInstalled(diag.oll?.models, model);
  return Boolean(ocOK && ollOK && modelOK);
}

/**
 * 진단 결과 → 실행해야 할 단계(todo) / 이미 완료된 단계(skipped) 분리.
 *
 * 핵심 보증:
 *  - 이미 설치/실행 중이면 절대 다시 설치/실행 단계가 todo에 들어가지 않는다 (idempotency)
 *  - CONFIG_OC는 매번 todo (`openclaw config set`은 멱등하므로 안전)
 *  - PROMPT_TEST는 매번 todo (검증은 매 실행마다 가치)
 *
 * @param {{ oc: any, ocInstalled: any, oll: any }} diag
 * @param {string} model
 * @returns {{ todo: string[], skipped: string[] }}
 */
export function buildPlan(diag, model) {
  const todo = [];
  const skipped = [];
  const route = (done, id) => (done ? skipped.push(id) : todo.push(id));

  route(Boolean(diag?.ocInstalled?.installed), STEP.INSTALL_OC);
  route(diag?.oc?.state === "running", STEP.START_OC);
  route(Boolean(diag?.oll?.installed), STEP.INSTALL_OLLAMA);
  route(Boolean(diag?.oll?.running), STEP.START_OLLAMA);
  route(hasModelInstalled(diag?.oll?.models, model), STEP.PULL_MODEL);

  // 설정 적용 + 프롬프트 검증은 *항상* 수행 — 멱등 + 매번 가치
  todo.push(STEP.CONFIG_OC);
  todo.push(STEP.PROMPT_TEST);

  return { todo, skipped };
}
