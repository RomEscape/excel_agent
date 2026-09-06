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
]);
// 2026-09-06: `qwen3:8b` 선택지를 뺐다. 앱은 대화 A.X-4.0-Light + 계획 ax7bplanner-v3 둘로만 검증됐고,
// 사용자가 다른 모델을 고르면 플래너와 어긋난 채 "조용히 나빠지는" 상태가 된다.

export const DEFAULT_OLLAMA_MODEL = QWEN3_OPENCLAW_PRESET.ollamaModel;

/**
 * Excel 계획 수립 전용 플래너(사이드카 local_stack/presets.py 의 planner_model 과 같은 이름).
 * 마법사가 범용 모델만 받고 이 모델을 안 받아 새 PC 에서 "플래너가 조용히 죽는" 상태로
 * 시작했다(2026-09-06 실측). 범용 모델과 함께 받는다.
 */
export const DEFAULT_PLANNER_MODEL = "ax7bplanner-v3:latest";
