/**
 * 로컬 스택 프리셋 적용 — UI/store/sidecar 설정 동기화.
 */

import { getLocalStackPreset, DEFAULT_PRESET_ID } from "./presets.js";

/**
 * 프리셋의 LLM 설정 객체.
 * @param {string} [presetId]
 */
export function getPresetLlmConfig(presetId = DEFAULT_PRESET_ID) {
  const preset = getLocalStackPreset(presetId);
  return { provider: preset.llm.provider, model: preset.llm.model };
}

/**
 * sidecar + zustand에 LLM 설정을 저장한다.
 *
 * @param {string} presetId
 * @param {{ saveLLMSettings: (cfg: object) => Promise<unknown>, setLLMConfig: (cfg: object) => void }} deps
 */
export async function applyLocalStackPreset(presetId, { saveLLMSettings, setLLMConfig }) {
  const config = getPresetLlmConfig(presetId);
  await saveLLMSettings(config);
  setLLMConfig(config);
  return config;
}

/**
 * Ollama pull에 쓸 모델 태그.
 * @param {string} [presetId]
 */
export function getPresetOllamaModel(presetId = DEFAULT_PRESET_ID) {
  return getLocalStackPreset(presetId).ollamaModel;
}
