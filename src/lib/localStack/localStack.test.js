/**
 * 로컬 스택 프리셋 단위 테스트.
 * 실행: node --test src/lib/localStack/localStack.test.js
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  QWEN3_OPENCLAW_PRESET,
  DEFAULT_PRESET_ID,
  DEFAULT_OLLAMA_MODEL,
  getLocalStackPreset,
  getPresetLlmConfig,
} from "./index.js";
import { hasModelInstalled } from "../localAISetupCore.js";

describe("qwen3-openclaw preset", () => {
  it("기본 프리셋 ID가 qwen3-openclaw이다", () => {
    assert.equal(DEFAULT_PRESET_ID, "qwen3-openclaw");
  });

  it("Ollama 모델 태그는 qwen3:4b이다", () => {
    assert.equal(DEFAULT_OLLAMA_MODEL, "qwen3:4b");
    assert.equal(QWEN3_OPENCLAW_PRESET.ollamaModel, "qwen3:4b");
    assert.equal(QWEN3_OPENCLAW_PRESET.hfModel, "Qwen/Qwen3-4B-Instruct");
  });

  it("LLM 설정은 ollama + qwen3:4b이다", () => {
    const cfg = getPresetLlmConfig();
    assert.deepEqual(cfg, { provider: "ollama", model: "qwen3:4b" });
    assert.deepEqual(getLocalStackPreset().llm, cfg);
  });

  it("Ollama 목록에서 qwen3:4b 태그를 인식한다", () => {
    assert.equal(hasModelInstalled([{ name: "qwen3:4b" }], "qwen3:4b"), true);
    assert.equal(hasModelInstalled([{ name: "qwen3:4b:latest" }], "qwen3:4b"), true);
  });
});
