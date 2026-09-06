/**
 * modelCatalog 계약 테스트.
 *
 * 고정하는 것:
 *   1) 두 갈래 원본(객체 배열 / 문자열 배열)을 모두 받는다 — 한쪽만 받으면
 *      다른 화면에서 예외 없이 빈 목록이 된다.
 *   2) 미설치 항목이 `installed: false`로 구분된다 — 안 그러면 안 받은 모델이
 *      이미 받은 것처럼 보인다.
 *   3) 저장된 선택이 목록에 있으면 그것이 유지된다 — 화면 이동 때 모델이
 *      슬그머니 바뀌던 문제.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  RECOMMENDED_MODEL,
  buildModelChoices,
  isChatModelChoice,
  buildModelOptions,
  describeModel,
  pickDefaultModel,
  toModelId,
} from "./modelCatalog.js";

test("toModelId — `/api/tags` 객체와 사이드카 문자열을 모두 받는다", () => {
  assert.equal(toModelId({ name: "qwen3:4b", size: 123 }), "qwen3:4b");
  assert.equal(toModelId({ model: "gemma2:2b" }), "gemma2:2b");
  assert.equal(toModelId("  llama3.2:1b  "), "llama3.2:1b");
  assert.equal(toModelId(null), "");
  assert.equal(toModelId({ size: 1 }), "");
});

test("buildModelOptions — 객체 배열(Rust ollama_status 경로)", () => {
  const opts = buildModelOptions([
    { name: "qwen2.5-coder:14b" },
    { name: "qwen3:4b" },
    { name: "qwen2.5:7b" },
  ]);
  assert.deepEqual(
    opts.map((o) => o.id),
    ["qwen3:4b", "qwen2.5-coder:14b", "qwen2.5:7b"],
    "추천이 맨 위, 나머지는 이름순"
  );
  assert.ok(opts.every((o) => o.installed === true));
});

test("buildModelOptions — 문자열 배열(사이드카 health 경로)도 같은 결과", () => {
  const fromObjects = buildModelOptions([{ name: "qwen3:4b" }, { name: "gemma2:2b" }]);
  const fromStrings = buildModelOptions(["qwen3:4b", "gemma2:2b"]);
  assert.deepEqual(fromStrings, fromObjects);
});

test("buildModelOptions — 중복 제거·빈 입력 방어", () => {
  assert.deepEqual(buildModelOptions(null), []);
  assert.deepEqual(buildModelOptions(undefined), []);
  const dup = buildModelOptions(["qwen3:4b", { name: "qwen3:4b" }, "  qwen3:4b  "]);
  assert.equal(dup.length, 1);
});

test("buildModelChoices — 미설치 후보가 목록에 끼되 installed:false로 구분된다", () => {
  const opts = buildModelChoices([{ name: "qwen2.5:7b" }], ["qwen3:4b", "qwen3:8b"]);
  const byId = Object.fromEntries(opts.map((o) => [o.id, o]));

  assert.equal(byId["qwen2.5:7b"].installed, true);
  assert.equal(byId["qwen3:4b"].installed, false, "안 받은 모델을 받은 것처럼 표시하면 안 된다");
  assert.equal(byId["qwen3:8b"].installed, false);
  assert.equal(opts[0].id, RECOMMENDED_MODEL, "추천은 미설치여도 맨 위");
});

test("buildModelChoices — 이미 설치된 모델은 후보로 중복되지 않는다", () => {
  const opts = buildModelChoices(["qwen3:4b"], ["qwen3:4b"]);
  assert.equal(opts.length, 1);
  assert.equal(opts[0].installed, true, "설치됨이 이긴다");
});

test("pickDefaultModel — 저장된 선택이 목록에 있으면 유지된다", () => {
  const opts = buildModelChoices(["qwen2.5:7b"], ["qwen3:4b"]);
  assert.equal(pickDefaultModel(opts, "qwen2.5:7b"), "qwen2.5:7b");
});

test("pickDefaultModel — 저장된 선택이 없으면 추천 → 첫 항목 순", () => {
  const withRec = buildModelChoices(["qwen2.5:7b"], ["qwen3:4b"]);
  assert.equal(pickDefaultModel(withRec, "지워진모델:1b"), RECOMMENDED_MODEL);

  const noRec = buildModelOptions(["gemma2:2b", "llama3.2:1b"]);
  assert.equal(pickDefaultModel(noRec, ""), "gemma2:2b");
  assert.equal(pickDefaultModel([], "아무거나"), "");
});

test("describeModel — 제조사·태그 분해는 기존 계약 유지", () => {
  const m = describeModel("qwen3:4b");
  assert.equal(m.name, "qwen3");
  assert.equal(m.tag, "4b");
  assert.equal(m.brand, "Qwen");
  assert.equal(m.recommended, true);
  assert.equal(describeModel("듣도보도못한모델").brand, "로컬 모델");
});

test("buildModelChoices — hf.co 출처 태그와 플래너는 대화 모델 목록에서 숨긴다 (2026-09-06)", () => {
  const opts = buildModelChoices(
    [
      { name: "skt/A.X-4.0-Light:latest" },
      { name: "hf.co/jayusop/A.X-4.0-Light-Q4_K_M-GGUF:latest" },
      { name: "ax7bplanner-v3:latest" },
      { name: "hf.co/PJiNH/ax7bplanner-v3-GGUF:latest" },
      { name: "ax4-light:latest" },
    ],
    [],
  );
  assert.deepEqual(
    opts.map((o) => o.id).sort(),
    ["ax4-light:latest", "skt/A.X-4.0-Light:latest"],
  );
  assert.equal(isChatModelChoice("ax7bplanner-v3"), false);
  assert.equal(isChatModelChoice("qwen3:8b"), true);
});
