/**
 * 요청 하나를 얼마나 기다릴지, 실패했을 때 다시 보내도 되는지를 정한다.
 *
 * 이 두 가지를 한 곳에 모으는 이유는 서로 얽혀 있기 때문이다. 타임아웃을 서버보다
 * 짧게 잡아 놓고 타임아웃에 재시도하면, 서버가 아직 편집 중인 워크북에 같은 편집을
 * 한 번 더 보낸다. 둘 중 하나만 봐서는 이 조합이 위험한지 알 수 없다.
 */

// ── 타임아웃 계층 ──────────────────────────────────────────────────────────
//
// 안쪽이 짧고 바깥이 길어야 한다. 바깥이 먼저 포기하면 UI는 "실패"라고 말하는데
// 서버는 계속 편집한다. 사용자가 보는 것과 파일에 남는 것이 어긋난다.
//
//   Python COM 큐 180초  <  Rust IPC 200초  <  여기 210초
//
// Python 값은 `EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS`로 바꿀 수 있다. 그걸 올리면
// 위 두 개도 같이 올려야 한다.

/** Python COM 큐 상한. 여기서 쓰지는 않고, 아래 값들의 근거로 적어 둔다. */
export const EXCEL_QUEUE_CEILING_MS = 180_000;

/** Rust IPC(reqwest) 상한. Python보다 길다. */
export const IPC_CEILING_MS = 200_000;

/** Excel 명령을 기다리는 상한. Rust보다 길어서 Rust의 오류 메시지가 그대로 올라온다. */
export const EXCEL_REQUEST_TIMEOUT_MS = 210_000;

/** 워크북을 건드리지 않는 요청(대화 등)의 상한. 실패해도 다시 보내면 그만이다. */
export const CHAT_REQUEST_TIMEOUT_MS = 45_000;

/**
 * 이만큼 지나면 "아직 하고 있다"고 알린다. 포기하는 시점이 아니다.
 *
 * 예전에는 이 시점에 요청을 실패 처리하고 재시도했다. 오래 걸리는 것과 실패한 것은
 * 다른 일이라, 지금은 알리기만 한다.
 */
export const SLOW_NOTICE_MS = 45_000;

// ── 재시도 판정 ────────────────────────────────────────────────────────────

/**
 * 요청이 서버에 닿지 못했음이 확실한 오류.
 *
 * 이때만 그대로 다시 보내도 안전하다. 서버가 아무것도 시작하지 않았기 때문이다.
 */
const NEVER_DELIVERED = [
  "connection refused",
  "econnrefused",
  "error sending request",
  "failed to fetch",
  // 사이드카가 아직 준비되지 않았을 때 게이트웨이가 내는 것들. 요청은 전달되지 않았다.
  "http 503",
  "gateway token mismatch",
];

/**
 * 응답을 못 받았을 뿐, 서버가 일을 했는지 안 했는지 알 수 없는 오류.
 *
 * 타임아웃이 대표적이다. 우리 타임아웃은 진행 중인 요청을 **취소하지 못한다** —
 * 기다리기를 그만둘 뿐이다. 그래서 타임아웃 뒤의 재전송은 같은 편집을 두 번 하는
 * 길이 된다.
 */
const UNKNOWN_OUTCOME = ["timeout", "timed out", "aborted", "요청 타임아웃", "응답 읽기 실패"];

function messageOf(err) {
  return String(err?.message ?? err ?? "").toLowerCase();
}

/** 서버가 일을 시작하지 않았음이 확실한가. */
export function isNeverDelivered(err) {
  const msg = messageOf(err);
  return NEVER_DELIVERED.some((needle) => msg.includes(needle));
}

/** 서버가 일을 했는지 알 수 없는가. */
export function isUnknownOutcome(err) {
  const msg = messageOf(err);
  return UNKNOWN_OUTCOME.some((needle) => msg.includes(needle));
}

/**
 * 이 오류를 보고 요청을 그대로 다시 보내도 되는가.
 *
 * @param err 발생한 오류
 * @param repeatable 같은 요청을 두 번 실행해도 결과가 같은가. 워크북을 편집하는
 *   명령은 false다 — 두 번 실행되면 값이 두 번 들어가거나 행이 두 벌 생긴다.
 */
export function shouldResend(err, { repeatable }) {
  if (isNeverDelivered(err)) return true;
  // 결과를 모르는 실패는 반복해도 되는 요청에서만 다시 보낸다.
  return Boolean(repeatable) && isUnknownOutcome(err);
}

// ── 실행 ──────────────────────────────────────────────────────────────────

/**
 * 시간 상한을 걸고 실행한다.
 *
 * 주의: 상한을 넘겨도 진행 중인 작업은 **계속 돈다.** 취소할 방법이 없다. 이 함수는
 * 기다리기를 그만둘 뿐이다.
 */
export function withTimeout(fn, { timeoutMs, label, onSlow, slowAfterMs } = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      settled = true;
      reject(new Error(`${label} timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    const slowTimer =
      onSlow && slowAfterMs && slowAfterMs < timeoutMs
        ? setTimeout(() => {
            if (!settled) onSlow();
          }, slowAfterMs)
        : null;

    const finish = () => {
      settled = true;
      clearTimeout(timer);
      if (slowTimer) clearTimeout(slowTimer);
    };

    Promise.resolve()
      .then(() => fn())
      .then(
        (value) => {
          finish();
          resolve(value);
        },
        (err) => {
          finish();
          reject(err);
        }
      );
  });
}

/**
 * 정책에 따라 실행하고, 안전할 때만 한 번 다시 보낸다.
 *
 * @param fn 실행할 함수
 * @param options.repeatable 두 번 실행해도 되는 요청인가 (기본 false — 안전한 쪽)
 */
export async function runWithPolicy(
  fn,
  { label = "요청", timeoutMs = CHAT_REQUEST_TIMEOUT_MS, repeatable = false, backoffMs = 900, onSlow, onRetry } = {}
) {
  const once = () => withTimeout(fn, { timeoutMs, label, onSlow, slowAfterMs: SLOW_NOTICE_MS });
  try {
    return await once();
  } catch (err) {
    if (!shouldResend(err, { repeatable })) throw err;
    if (onRetry) onRetry(label);
    await new Promise((r) => setTimeout(r, backoffMs));
    return once();
  }
}
