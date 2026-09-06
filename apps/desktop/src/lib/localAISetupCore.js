/**
 * LocalAISetupWizard 단계·계획 로직 (프리셋과 분리).
 */

import {
  DEFAULT_OLLAMA_MODEL,
  DEFAULT_PLANNER_MODEL,
  LOCAL_STACK_MODEL_OPTIONS,
} from "./localStack/index.js";

export const STEP = Object.freeze({
  INSTALL_OLLAMA: "install-ollama",
  START_OLLAMA: "start-ollama",
  PULL_MODEL: "pull-model",
  PROMPT_TEST: "prompt-test",
});

export const STEP_LABEL = Object.freeze({
  [STEP.INSTALL_OLLAMA]: "로컬 AI 엔진 설치",
  [STEP.START_OLLAMA]: "로컬 AI 엔진 실행",
  [STEP.PULL_MODEL]: "AI 모델 다운로드",
  [STEP.PROMPT_TEST]: "AI 대화 테스트",
});

/** @deprecated 이름 호환 — LOCAL_STACK_MODEL_OPTIONS와 동일 */
export const RECOMMENDED_MODELS = LOCAL_STACK_MODEL_OPTIONS;

export const DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL;

/** Excel 계획 플래너 — 범용 모델과 별개로 반드시 있어야 한다. */
export const PLANNER_MODEL = DEFAULT_PLANNER_MODEL;

/**
 * Ollama 모델 목록에 해당 모델이 이미 받아져 있는지 확인.
 * Ollama는 `skt/A.X-4.0-Light:latest`처럼 태그가 붙은 문자열을 사용한다.
 */
export function hasModelInstalled(models, model) {
  const target = String(model || "").trim();
  const hasTag = target.includes(":");
  const base = target.split(":")[0];
  return (models || []).some((m) => {
    const name = String(m?.name || "");
    if (hasTag) {
      // 태그를 지정한 경우(예: foo:8b)는 다른 태그(foo:4b)를 매치하면 안 된다.
      return name === target || name.startsWith(`${target}:`);
    }
    // 태그를 생략한 경우(예: qwen3)만 base prefix 매칭 허용.
    return name === target || name.startsWith(`${target}:`) || name.startsWith(`${base}:`);
  });
}

export function isAllReady(diag, model) {
  if (!diag) return false;
  const ollOK = diag.oll?.installed && diag.oll?.running;
  const modelOK = hasModelInstalled(diag.oll?.models, model);
  // 플래너가 없으면 채팅은 되는데 Excel 계획이 조용히 실패한다 — 준비됨으로 치지 않는다.
  const plannerOK = hasModelInstalled(diag.oll?.models, PLANNER_MODEL);
  return Boolean(ollOK && modelOK && plannerOK);
}

export function buildPlan(diag, model) {
  const todo = [];
  const skipped = [];
  const route = (done, id) => (done ? skipped.push(id) : todo.push(id));

  route(Boolean(diag?.oll?.installed), STEP.INSTALL_OLLAMA);
  route(Boolean(diag?.oll?.running), STEP.START_OLLAMA);
  route(
    hasModelInstalled(diag?.oll?.models, model) &&
      hasModelInstalled(diag?.oll?.models, PLANNER_MODEL),
    STEP.PULL_MODEL,
  );

  // 대화 테스트는 항상 마지막에 수행해 Ollama 응답 경로를 검증한다.
  todo.push(STEP.PROMPT_TEST);

  return { todo, skipped };
}
