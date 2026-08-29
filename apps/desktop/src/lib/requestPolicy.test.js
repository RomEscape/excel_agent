/**
 * 요청 정책 단위 테스트.
 *
 * 실행: `node --test src/lib/requestPolicy.test.js`
 * package.json의 `npm run test:unit`에 묶여 있다.
 *
 * 여기서 지키려는 것은 하나다 — **워크북을 편집하는 명령은 타임아웃 뒤에 다시
 * 보내지 않는다.** 우리 타임아웃은 진행 중인 요청을 취소하지 못하므로, 다시 보내면
 * 같은 편집이 두 번 실행된다.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_REQUEST_TIMEOUT_MS,
  EXCEL_QUEUE_CEILING_MS,
  EXCEL_REQUEST_TIMEOUT_MS,
  IPC_CEILING_MS,
  SLOW_NOTICE_MS,
  isNeverDelivered,
  isUnknownOutcome,
  runWithPolicy,
  shouldResend,
  withTimeout,
} from "./requestPolicy.js";

const timeoutError = new Error("엑셀 명령 timeout after 210000ms");
const refusedError = new Error("error sending request: connection refused");

describe("타임아웃 계층", () => {
  it("바깥이 안쪽보다 길다", () => {
    // 이 순서가 뒤집히면 UI가 먼저 포기하고, 서버는 계속 편집한다.
    assert.ok(
      EXCEL_QUEUE_CEILING_MS < IPC_CEILING_MS,
      "Rust는 Python COM 큐보다 오래 기다려야 한다"
    );
    assert.ok(
      IPC_CEILING_MS < EXCEL_REQUEST_TIMEOUT_MS,
      "프론트는 Rust보다 오래 기다려야 한다"
    );
  });

  it("느리다는 알림은 포기보다 먼저 뜬다", () => {
    assert.ok(SLOW_NOTICE_MS < EXCEL_REQUEST_TIMEOUT_MS);
  });
});

describe("오류 분류", () => {
  it("연결 거부는 서버가 일을 시작하지 않았음이 확실하다", () => {
    assert.equal(isNeverDelivered(refusedError), true);
    assert.equal(isUnknownOutcome(refusedError), false);
  });

  it("타임아웃은 서버가 일을 했는지 알 수 없다", () => {
    assert.equal(isUnknownOutcome(timeoutError), true);
    assert.equal(isNeverDelivered(timeoutError), false);
  });

  it("모르는 오류는 어느 쪽도 아니다", () => {
    const other = new Error("지원하지 않는 action");
    assert.equal(isNeverDelivered(other), false);
    assert.equal(isUnknownOutcome(other), false);
  });
});

describe("재전송 판정", () => {
  it("편집 명령은 타임아웃에 다시 보내지 않는다", () => {
    // 핵심 회귀. 예전에는 여기서 true를 돌려주며 같은 편집을 두 번 실행했다.
    assert.equal(shouldResend(timeoutError, { repeatable: false }), false);
  });

  it("편집 명령이라도 서버에 닿지 못했으면 다시 보낸다", () => {
    assert.equal(shouldResend(refusedError, { repeatable: false }), true);
  });

  it("반복해도 되는 요청은 타임아웃에도 다시 보낸다", () => {
    assert.equal(shouldResend(timeoutError, { repeatable: true }), true);
  });

  it("분류되지 않은 오류는 다시 보내지 않는다", () => {
    const other = new Error("지원하지 않는 action");
    assert.equal(shouldResend(other, { repeatable: true }), false);
  });
});

describe("runWithPolicy", () => {
  it("편집 명령이 타임아웃하면 한 번만 실행된다", async () => {
    let calls = 0;
    const hang = () => {
      calls += 1;
      return new Promise(() => {}); // 끝나지 않는다 — 서버가 아직 일하는 중
    };

    await assert.rejects(
      runWithPolicy(hang, { label: "엑셀 명령", timeoutMs: 20, repeatable: false }),
      /timeout/
    );
    assert.equal(calls, 1, "편집 명령을 다시 보내면 안 된다");
  });

  it("반복해도 되는 요청은 타임아웃 뒤 한 번 더 보낸다", async () => {
    let calls = 0;
    const hang = () => {
      calls += 1;
      return new Promise(() => {});
    };

    await assert.rejects(
      runWithPolicy(hang, { label: "AI 대화", timeoutMs: 20, repeatable: true, backoffMs: 1 }),
      /timeout/
    );
    assert.equal(calls, 2);
  });

  it("연결 거부는 편집 명령도 다시 보낸다", async () => {
    let calls = 0;
    const refuseOnce = () => {
      calls += 1;
      if (calls === 1) return Promise.reject(refusedError);
      return Promise.resolve("ok");
    };

    const result = await runWithPolicy(refuseOnce, {
      label: "엑셀 명령",
      timeoutMs: 500,
      repeatable: false,
      backoffMs: 1,
    });
    assert.equal(result, "ok");
    assert.equal(calls, 2);
  });

  it("성공하면 다시 보내지 않는다", async () => {
    let calls = 0;
    const ok = () => {
      calls += 1;
      return Promise.resolve("done");
    };

    assert.equal(await runWithPolicy(ok, { label: "엑셀 명령", timeoutMs: 500 }), "done");
    assert.equal(calls, 1);
  });

  it("기본값은 안전한 쪽이다 — 반복 가능 여부를 안 적으면 재전송하지 않는다", async () => {
    let calls = 0;
    const hang = () => {
      calls += 1;
      return new Promise(() => {});
    };

    await assert.rejects(runWithPolicy(hang, { label: "무언가", timeoutMs: 20 }), /timeout/);
    assert.equal(calls, 1);
  });
});

describe("withTimeout", () => {
  it("오래 걸리면 알리되 포기하지는 않는다", async () => {
    let notified = 0;
    const slow = () => new Promise((resolve) => setTimeout(() => resolve("늦었지만 성공"), 40));

    const result = await withTimeout(slow, {
      timeoutMs: 500,
      label: "엑셀 명령",
      slowAfterMs: 10,
      onSlow: () => {
        notified += 1;
      },
    });

    assert.equal(result, "늦었지만 성공");
    assert.equal(notified, 1, "느리다는 알림은 떠야 한다");
  });

  it("제때 끝나면 알리지 않는다", async () => {
    let notified = 0;
    await withTimeout(() => Promise.resolve("빠름"), {
      timeoutMs: 500,
      label: "엑셀 명령",
      slowAfterMs: 50,
      onSlow: () => {
        notified += 1;
      },
    });
    // 알림 타이머가 남아 돌면 끝난 요청에 대고 "아직 하는 중"이라고 말하게 된다.
    await new Promise((r) => setTimeout(r, 80));
    assert.equal(notified, 0);
  });

  it("느림 알림 시점이 상한보다 늦으면 아예 걸지 않는다", async () => {
    let notified = 0;
    await assert.rejects(
      withTimeout(() => new Promise(() => {}), {
        timeoutMs: 20,
        label: "엑셀 명령",
        slowAfterMs: 100,
        onSlow: () => {
          notified += 1;
        },
      }),
      /timeout/
    );
    await new Promise((r) => setTimeout(r, 140));
    assert.equal(notified, 0);
  });
});

describe("기본 상한", () => {
  it("대화는 편집보다 짧게 기다린다", () => {
    assert.ok(CHAT_REQUEST_TIMEOUT_MS < EXCEL_REQUEST_TIMEOUT_MS);
  });
});
