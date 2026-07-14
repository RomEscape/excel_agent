/**
 * 로컬 AI 스택 프리셋 — Ollama 모델 + OpenClaw 게이트웨이 조합.
 */

import { QWEN3_OPENCLAW_PRESET } from "./qwen3OpenClaw.js";

/** @typedef {typeof QWEN3_OPENCLAW_PRESET} LocalStackPreset */

export const LOCAL_STACK_PRESETS = Object.freeze({
  [QWEN3_OPENCLAW_PRESET.id]: QWEN3_OPENCLAW_PRESET,
});

/** 앱 기본 로컬 스택 */
export const DEFAULT_PRESET_ID = QWEN3_OPENCLAW_PRESET.id;

/**
 * @param {string} [presetId]
 * @returns {LocalStackPreset}
 */
export function getLocalStackPreset(presetId = DEFAULT_PRESET_ID) {
  const preset = LOCAL_STACK_PRESETS[presetId];
  if (!preset) {
    throw new Error(`알 수 없는 로컬 스택 프리셋: ${presetId}`);
  }
  return preset;
}

/** LocalAISetupWizard 모델 선택 목록 — 첫 항목이 기본 프리셋 */
export const LOCAL_STACK_MODEL_OPTIONS = Object.freeze([
  {
    id: QWEN3_OPENCLAW_PRESET.ollamaModel,
    label: "skt/A.X-4.0-Light:latest (권장, ~4~6GB)",
    note: "에이닷 경량 계열 · 한국어 자연어 명령 안정성 우선",
    presetId: QWEN3_OPENCLAW_PRESET.id,
  },
  {
    id: "qwen3:8b",
    label: "qwen3:8b (~5~6GB)",
    note: "정확도 향상(중간 사양 권장)",
  },
]);

export const DEFAULT_OLLAMA_MODEL = QWEN3_OPENCLAW_PRESET.ollamaModel;
