/**
 * Qwen 3 로컬 스택 프리셋 (Ollama 단독).
 * Ollama 실행 태그: qwen3:4b (경량 기본값)
 */

export const QWEN3_LOCAL_PRESET = Object.freeze({
  id: "qwen3-local",
  label: "Qwen 3 (로컬 Ollama)",
  description:
    "Qwen 3 모델을 Ollama로 실행하고, OpenAI 호환 tool-calling으로 엑셀 작업과 대화를 처리합니다. 한국어 자연어 명령 안정성을 우선합니다.",
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
