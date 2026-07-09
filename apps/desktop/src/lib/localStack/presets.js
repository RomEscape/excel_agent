/**
 * 로컬 AI 스택 프리셋 — Ollama 단독 (OpenAI 호환 tool-calling).
 */

import { QWEN3_LOCAL_PRESET } from "./qwen3Local.js";

/** @typedef {typeof QWEN3_LOCAL_PRESET} LocalStackPreset */

export const LOCAL_STACK_PRESETS = Object.freeze({
  [QWEN3_LOCAL_PRESET.id]: QWEN3_LOCAL_PRESET,
});

/** 앱 기본 로컬 스택 */
export const DEFAULT_PRESET_ID = QWEN3_LOCAL_PRESET.id;

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
    id: QWEN3_LOCAL_PRESET.ollamaModel,
    label: "qwen3:4b (권장, ~3~4GB)",
    note: "Qwen 3 최신 계열 · 한국어 자연어 명령 안정성 우선",
    presetId: QWEN3_LOCAL_PRESET.id,
  },
  {
    id: "qwen3:8b",
    label: "qwen3:8b (~5~6GB)",
    note: "정확도 향상(중간 사양 권장)",
  },
]);

export const DEFAULT_OLLAMA_MODEL = QWEN3_LOCAL_PRESET.ollamaModel;
