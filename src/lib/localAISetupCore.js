/**
 * LocalAISetupWizard 단계·계획 로직 (프리셋과 분리).
 */

import {
  DEFAULT_OLLAMA_MODEL,
  LOCAL_STACK_MODEL_OPTIONS,
} from "./localStack/index.js";

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

/** @deprecated 이름 호환 — LOCAL_STACK_MODEL_OPTIONS와 동일 */
export const RECOMMENDED_MODELS = LOCAL_STACK_MODEL_OPTIONS;

export const DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL;

/**
 * Ollama 모델 목록에 해당 모델이 이미 받아져 있는지 확인.
 * Ollama는 `qwen3:4b` 또는 `qwen3:4b:latest` 형태로 표시될 수 있음.
 */
export function hasModelInstalled(models, model) {
  const target = String(model || "").trim();
  const hasTag = target.includes(":");
  const base = target.split(":")[0];
  return (models || []).some((m) => {
    const name = String(m?.name || "");
    if (hasTag) {
      // 태그를 지정한 경우(예: qwen3:8b)는 다른 태그(qwen3:4b)를 매치하면 안 된다.
      return name === target || name.startsWith(`${target}:`);
    }
    // 태그를 생략한 경우(예: qwen3)만 base prefix 매칭 허용.
    return name === target || name.startsWith(`${target}:`) || name.startsWith(`${base}:`);
  });
}

export function isAllReady(diag, model) {
  if (!diag) return false;
  const ocOK = diag.oc?.state === "running" && diag.ocInstalled?.installed;
  const ollOK = diag.oll?.installed && diag.oll?.running;
  const modelOK = hasModelInstalled(diag.oll?.models, model);
  return Boolean(ocOK && ollOK && modelOK);
}

export function buildPlan(diag, model) {
  const todo = [];
  const skipped = [];
  const route = (done, id) => (done ? skipped.push(id) : todo.push(id));

  route(Boolean(diag?.ocInstalled?.installed), STEP.INSTALL_OC);
  route(diag?.oc?.state === "running", STEP.START_OC);
  route(Boolean(diag?.oll?.installed), STEP.INSTALL_OLLAMA);
  route(Boolean(diag?.oll?.running), STEP.START_OLLAMA);
  route(hasModelInstalled(diag?.oll?.models, model), STEP.PULL_MODEL);

  todo.push(STEP.CONFIG_OC);
  todo.push(STEP.PROMPT_TEST);

  return { todo, skipped };
}
