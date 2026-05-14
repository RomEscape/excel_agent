/**
 * LocalAISetup 순수 로직 단위 테스트.
 *
 * 실행: `node --test src/lib/localAISetup.test.js`
 * package.json에 `npm test:unit` 스크립트로 묶여있음.
 *
 * Node 18+ 의 내장 `node:test`만 사용 — 추가 의존성 없음.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  STEP,
  DEFAULT_MODEL,
  RECOMMENDED_MODELS,
  buildPlan,
  isAllReady,
  hasModelInstalled,
} from "./localAISetup.js";

// ── 픽스처 ────────────────────────────────────────────────────────────────

const fixtures = {
  /** 아무것도 설치 안 된 새 시스템 */
  fresh: () => ({
    oc: { state: "stopped" },
    ocInstalled: { installed: false },
    oll: { installed: false, running: false, models: [] },
  }),
  /** 모든 게 준비된 시스템 (모델까지) */
  ready: (model) => ({
    oc: { state: "running", port: 18789 },
    ocInstalled: { installed: true, version: "2026.5.6" },
    oll: {
      installed: true,
      running: true,
      models: [{ name: `${model}:latest` }],
    },
  }),
  /** OpenClaw만 설치되고 게이트웨이는 꺼진 상태 */
  ocInstalledOnly: () => ({
    oc: { state: "stopped" },
    ocInstalled: { installed: true, version: "2026.5.6" },
    oll: { installed: false, running: false, models: [] },
  }),
  /** Ollama 실행 중이지만 원하는 모델이 없는 상태 */
  ollamaWrongModel: (otherModel) => ({
    oc: { state: "running" },
    ocInstalled: { installed: true },
    oll: {
      installed: true,
      running: true,
      models: [{ name: `${otherModel}:latest` }],
    },
  }),
};

// ── DEFAULT_MODEL / RECOMMENDED_MODELS ────────────────────────────────────

describe("default model", () => {
  it("Office 친화적 경량 모델인 phi3.5를 기본값으로 한다", () => {
    assert.equal(DEFAULT_MODEL, "phi3.5");
  });

  it("RECOMMENDED_MODELS 첫 항목이 DEFAULT_MODEL과 일치한다 (사용자에게 첫 노출 = 권장)", () => {
    assert.equal(RECOMMENDED_MODELS[0].id, DEFAULT_MODEL);
  });

  it("phi3.5 설명에 Office 문서 강점이 명시되어야 한다", () => {
    const phi = RECOMMENDED_MODELS.find((m) => m.id === "phi3.5");
    assert.ok(phi, "phi3.5가 추천 목록에 있어야 함");
    assert.match(phi.note, /Excel|Office|PowerPoint|문서|표/);
  });
});

// ── hasModelInstalled ─────────────────────────────────────────────────────

describe("hasModelInstalled", () => {
  it("Ollama 태그(`phi3.5:latest`)가 startsWith 매칭으로 인식된다", () => {
    assert.equal(
      hasModelInstalled([{ name: "phi3.5:latest" }], "phi3.5"),
      true
    );
  });

  it("다른 모델만 있을 때는 false", () => {
    assert.equal(
      hasModelInstalled([{ name: "llama3.2:latest" }], "phi3.5"),
      false
    );
  });

  it("빈 목록 / null / undefined를 안전하게 처리한다", () => {
    assert.equal(hasModelInstalled([], "phi3.5"), false);
    assert.equal(hasModelInstalled(null, "phi3.5"), false);
    assert.equal(hasModelInstalled(undefined, "phi3.5"), false);
  });
});

// ── isAllReady ────────────────────────────────────────────────────────────

describe("isAllReady", () => {
  it("아무것도 없는 새 시스템 → false", () => {
    assert.equal(isAllReady(fixtures.fresh(), DEFAULT_MODEL), false);
  });

  it("모든 게 준비된 시스템 → true", () => {
    assert.equal(isAllReady(fixtures.ready(DEFAULT_MODEL), DEFAULT_MODEL), true);
  });

  it("OpenClaw 게이트웨이가 죽어있으면 false (binary는 있어도)", () => {
    assert.equal(isAllReady(fixtures.ocInstalledOnly(), DEFAULT_MODEL), false);
  });

  it("원하는 모델이 없으면 false", () => {
    assert.equal(
      isAllReady(fixtures.ollamaWrongModel("llama3.2"), "phi3.5"),
      false
    );
  });

  it("diag 자체가 null이면 false (방어적)", () => {
    assert.equal(isAllReady(null, DEFAULT_MODEL), false);
    assert.equal(isAllReady(undefined, DEFAULT_MODEL), false);
  });
});

// ── buildPlan — 핵심: idempotency ─────────────────────────────────────────

describe("buildPlan (idempotency)", () => {
  it("새 시스템: 모든 설치/실행 단계 + CONFIG + 검증이 todo, skipped는 비어있음", () => {
    const { todo, skipped } = buildPlan(fixtures.fresh(), DEFAULT_MODEL);
    assert.deepEqual(skipped, []);
    assert.deepEqual(todo, [
      STEP.INSTALL_OC,
      STEP.START_OC,
      STEP.INSTALL_OLLAMA,
      STEP.START_OLLAMA,
      STEP.PULL_MODEL,
      STEP.CONFIG_OC,
      STEP.PROMPT_TEST,
    ]);
  });

  it("OpenClaw만 설치된 경우: INSTALL_OC가 todo에 들어가지 않고 skipped로 이동 (중복 설치 방지)", () => {
    const { todo, skipped } = buildPlan(
      fixtures.ocInstalledOnly(),
      DEFAULT_MODEL
    );
    assert.ok(
      !todo.includes(STEP.INSTALL_OC),
      "INSTALL_OC가 todo에 다시 들어가면 안 됨"
    );
    assert.ok(skipped.includes(STEP.INSTALL_OC), "skipped로 이동해야 함");
  });

  it("모든 것이 준비된 시스템: 모든 사전 단계가 skipped로 가고 CONFIG_OC + PROMPT_TEST만 todo에 남음", () => {
    const { todo, skipped } = buildPlan(fixtures.ready(DEFAULT_MODEL), DEFAULT_MODEL);
    assert.deepEqual(todo, [STEP.CONFIG_OC, STEP.PROMPT_TEST]);
    assert.deepEqual(skipped, [
      STEP.INSTALL_OC,
      STEP.START_OC,
      STEP.INSTALL_OLLAMA,
      STEP.START_OLLAMA,
      STEP.PULL_MODEL,
    ]);
  });

  it("CONFIG_OC와 PROMPT_TEST는 항상 todo에 포함 (멱등 적용 + 매번 검증)", () => {
    const cases = [
      fixtures.fresh(),
      fixtures.ocInstalledOnly(),
      fixtures.ready(DEFAULT_MODEL),
      fixtures.ollamaWrongModel("llama3.2"),
    ];
    for (const diag of cases) {
      const { todo } = buildPlan(diag, DEFAULT_MODEL);
      assert.ok(
        todo.includes(STEP.CONFIG_OC),
        "CONFIG_OC는 매번 todo에 포함되어야 함"
      );
      assert.ok(
        todo.includes(STEP.PROMPT_TEST),
        "PROMPT_TEST는 매번 todo에 포함되어야 함"
      );
    }
  });

  it("PROMPT_TEST는 항상 마지막 단계 (모든 설치/연결 후에 검증)", () => {
    const { todo } = buildPlan(fixtures.fresh(), DEFAULT_MODEL);
    assert.equal(todo[todo.length - 1], STEP.PROMPT_TEST);
  });

  it("같은 진단으로 두 번 호출해도 결과가 동일 (idempotent — 부작용 없음)", () => {
    const diag = fixtures.ocInstalledOnly();
    const a = buildPlan(diag, DEFAULT_MODEL);
    const b = buildPlan(diag, DEFAULT_MODEL);
    assert.deepEqual(a, b);
  });

  it("원하는 모델이 다른 태그면 PULL_MODEL이 todo에 추가됨", () => {
    const { todo } = buildPlan(
      fixtures.ollamaWrongModel("llama3.2"),
      "phi3.5"
    );
    assert.ok(todo.includes(STEP.PULL_MODEL));
  });
});
