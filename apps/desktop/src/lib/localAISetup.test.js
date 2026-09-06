/**
 * LocalAISetup 순수 로직 단위 테스트 (Ollama 단독 스택).
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
  /** Ollama 미설치 새 시스템 */
  fresh: () => ({
    oll: { installed: false, running: false, models: [] },
  }),
  /** 모든 게 준비된 시스템 (Ollama 실행 + 모델까지) */
  ready: (model) => ({
    oll: {
      installed: true,
      running: true,
      // 2026-09-06: 플래너(ax7bplanner-v3)까지 있어야 '준비됨'이다.
      models: [{ name: `${model}:latest` }, { name: "ax7bplanner-v3:latest" }],
    },
  }),
  /** Ollama 설치됐지만 데몬이 꺼진 상태 */
  installedStopped: () => ({
    oll: { installed: true, running: false, models: [] },
  }),
  /** Ollama 실행 중이지만 원하는 모델이 없는 상태 */
  ollamaWrongModel: (otherModel) => ({
    oll: {
      installed: true,
      running: true,
      models: [{ name: `${otherModel}:latest` }],
    },
  }),
};

// ── DEFAULT_MODEL / RECOMMENDED_MODELS ────────────────────────────────────

describe("default model", () => {
  it("에이닷 로컬 스택(skt/A.X-4.0-Light:latest)을 기본값으로 한다", () => {
    assert.equal(DEFAULT_MODEL, "skt/A.X-4.0-Light:latest");
  });

  it("RECOMMENDED_MODELS 첫 항목이 DEFAULT_MODEL과 일치한다 (사용자에게 첫 노출 = 권장)", () => {
    assert.equal(RECOMMENDED_MODELS[0].id, DEFAULT_MODEL);
  });

  it("에이닷 기본 모델 설명에 한국어/안정성 취지가 명시되어야 한다", () => {
    const adot = RECOMMENDED_MODELS.find((m) => m.id === "skt/A.X-4.0-Light:latest");
    assert.ok(adot, "skt/A.X-4.0-Light:latest가 추천 목록에 있어야 함");
    assert.match(adot.note, /에이닷|한국어|안정성/);
  });
});

// ── hasModelInstalled ─────────────────────────────────────────────────────

describe("hasModelInstalled", () => {
  it("Ollama 태그(`qwen3:8b:latest`)가 startsWith 매칭으로 인식된다", () => {
    assert.equal(
      hasModelInstalled([{ name: "qwen3:8b:latest" }], "qwen3:8b"),
      true
    );
  });

  it("다른 모델만 있을 때는 false", () => {
    assert.equal(
      hasModelInstalled([{ name: "qwen3:4b:latest" }], "qwen3:8b"),
      false
    );
  });

  it("빈 목록 / null / undefined를 안전하게 처리한다", () => {
    assert.equal(hasModelInstalled([], "qwen3:8b"), false);
    assert.equal(hasModelInstalled(null, "qwen3:8b"), false);
    assert.equal(hasModelInstalled(undefined, "qwen3:8b"), false);
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

  it("Ollama 데몬이 꺼져있으면 false (binary는 있어도)", () => {
    assert.equal(isAllReady(fixtures.installedStopped(), DEFAULT_MODEL), false);
  });

  it("원하는 모델이 없으면 false", () => {
    assert.equal(
      isAllReady(fixtures.ollamaWrongModel("qwen3:4b"), "qwen3:8b"),
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
  it("새 시스템: 설치/실행/모델 단계 + 검증이 todo, skipped는 비어있음", () => {
    const { todo, skipped } = buildPlan(fixtures.fresh(), DEFAULT_MODEL);
    assert.deepEqual(skipped, []);
    assert.deepEqual(todo, [
      STEP.INSTALL_OLLAMA,
      STEP.START_OLLAMA,
      STEP.PULL_MODEL,
      STEP.PROMPT_TEST,
    ]);
  });

  it("Ollama만 설치된 경우: INSTALL_OLLAMA가 skipped로 이동 (중복 설치 방지)", () => {
    const { todo, skipped } = buildPlan(fixtures.installedStopped(), DEFAULT_MODEL);
    assert.ok(
      !todo.includes(STEP.INSTALL_OLLAMA),
      "INSTALL_OLLAMA가 todo에 다시 들어가면 안 됨"
    );
    assert.ok(skipped.includes(STEP.INSTALL_OLLAMA), "skipped로 이동해야 함");
  });

  it("모든 것이 준비된 시스템: 사전 단계가 모두 skipped, PROMPT_TEST만 todo", () => {
    const { todo, skipped } = buildPlan(fixtures.ready(DEFAULT_MODEL), DEFAULT_MODEL);
    assert.deepEqual(todo, [STEP.PROMPT_TEST]);
    assert.deepEqual(skipped, [
      STEP.INSTALL_OLLAMA,
      STEP.START_OLLAMA,
      STEP.PULL_MODEL,
    ]);
  });

  it("PROMPT_TEST는 매번 todo에 포함되고 항상 마지막 단계다", () => {
    const cases = [
      fixtures.fresh(),
      fixtures.installedStopped(),
      fixtures.ready(DEFAULT_MODEL),
      fixtures.ollamaWrongModel("qwen3:8b"),
    ];
    for (const diag of cases) {
      const { todo } = buildPlan(diag, DEFAULT_MODEL);
      assert.ok(todo.includes(STEP.PROMPT_TEST), "PROMPT_TEST는 매번 포함");
      assert.equal(todo[todo.length - 1], STEP.PROMPT_TEST, "PROMPT_TEST는 마지막");
    }
  });

  it("같은 진단으로 두 번 호출해도 결과가 동일 (idempotent — 부작용 없음)", () => {
    const diag = fixtures.installedStopped();
    const a = buildPlan(diag, DEFAULT_MODEL);
    const b = buildPlan(diag, DEFAULT_MODEL);
    assert.deepEqual(a, b);
  });

  it("원하는 모델이 다른 태그면 PULL_MODEL이 todo에 추가됨", () => {
    const { todo } = buildPlan(
      fixtures.ollamaWrongModel("qwen3:4b"),
      "qwen3:8b"
    );
    assert.ok(todo.includes(STEP.PULL_MODEL));
  });

  it("Ollama 미설치 시 INSTALL_OLLAMA가 todo 맨 앞에 온다", () => {
    const { todo } = buildPlan(fixtures.fresh(), DEFAULT_MODEL);
    assert.equal(todo[0], STEP.INSTALL_OLLAMA);
  });
});
