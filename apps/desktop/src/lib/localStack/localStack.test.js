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

  it("Ollama 기본 모델 태그는 에이닷 라이트다", () => {
    assert.equal(DEFAULT_OLLAMA_MODEL, "skt/A.X-4.0-Light:latest");
    assert.equal(QWEN3_OPENCLAW_PRESET.ollamaModel, "skt/A.X-4.0-Light:latest");
    assert.equal(QWEN3_OPENCLAW_PRESET.hfModel, "skt/A.X-4.0-Light");
  });

  it("LLM 설정은 ollama + 에이닷 라이트다", () => {
    const cfg = getPresetLlmConfig();
    assert.deepEqual(cfg, { provider: "ollama", model: "skt/A.X-4.0-Light:latest" });
    assert.deepEqual(getLocalStackPreset().llm, cfg);
  });

  it("Ollama 목록에서 에이닷 라이트 태그를 인식한다", () => {
    assert.equal(
      hasModelInstalled([{ name: "skt/A.X-4.0-Light:latest" }], "skt/A.X-4.0-Light:latest"),
      true
    );
    assert.equal(
      hasModelInstalled([{ name: "skt/A.X-4.0-Light:latest" }], "skt/A.X-4.0-Light"),
      true
    );
  });
});
