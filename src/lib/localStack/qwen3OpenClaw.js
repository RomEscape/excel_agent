/**
 * Qwen 3 + OpenClaw 로컬 스택 프리셋.
 * Ollama 실행 태그: qwen3:4b (경량 기본값)
 */

export const QWEN3_OPENCLAW_PRESET = Object.freeze({
  id: "qwen3-openclaw",
  label: "Qwen 3 + OpenClaw (로컬)",
  description:
    "Qwen 3 모델을 Ollama로 실행하고, OpenClaw 게이트웨이로 대화·스킬을 사용합니다. 한국어 자연어 명령 안정성을 우선합니다.",
  hfModel: "Qwen/Qwen3-4B-Instruct",
  ollamaModel: "qwen3:4b",
  multimodal: false,
  vramNote: "약 3~4GB (경량 기본값)",
  llm: Object.freeze({
    provider: "ollama",
    model: "qwen3:4b",
  }),
  /** LocalAISetupWizard PROMPT_TEST용 — 응답만 비어 있지 않으면 통과 */
  pingMessage: "안녕! 한 단어로 'OK'라고만 답해줘.",
});
