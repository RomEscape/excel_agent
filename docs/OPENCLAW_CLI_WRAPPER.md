# OpenClaw CLI 서브프로세스 wrapper

> **상태**: Rust 측 1차 구현 완료 (2026-05-20). Python 측 마이그레이션은 후속 PR.
> **모듈**: `src-tauri/src/openclaw_cli.rs`, `src/lib/api.js`의 `openclawCli*`.

## 배경

OpenClaw 게이트웨이 v2026.5.6의 핸드셰이크가 server-initiated `connect.challenge` envelope + `connect.params.auth.token` 기반으로 바뀌면서, Python `services/openclaw_client.py`(직접 WebSocket으로 연결하는 옛 구현)가 작동하지 않게 됐다. 동일한 이슈를 우리 Rust 코드에서 재현하는 대신, **OpenClaw 자체 CLI(`openclaw gateway call`, `openclaw agent`)에 프로토콜 처리를 위임**하는 방식을 채택.

## 트레이드오프

| | WS 직결 (옵션 A — 채택 안 함) | **CLI 서브프로세스 (옵션 C — 채택)** |
|---|---|---|
| 통신 | 우리가 직접 WebSocket 핸드셰이크 + 챌린지 응답 구현 | `openclaw` 바이너리 spawn |
| 호출당 오버헤드 | ~10ms IPC | ~500ms~1s spawn |
| 프로토콜 변경 내성 | 깨짐 (마이너 버전마다 추적 필요) | 강함 (OpenClaw가 자기 프로토콜 책임짐) |
| 스트리밍 | 가능 | 어려움 — `--expect-final` 또는 stdout 라인 파싱 필요 |
| 디버깅 | WS 패킷 캡처 필요 | CLI stdout/stderr만 보면 됨 |

메신저 봇 흐름은 사용자 한 입력당 LLM 생성이 3~15초 걸리므로 500ms~1s 오버헤드는 체감되지 않는다.

## Rust API

```rust
use crate::openclaw_cli::{gateway_call, agent_turn, AgentTurnRequest, CallOpts};

// 간단한 게이트웨이 메서드 — health / system-presence / cron.* 등
let info = gateway_call("health", None, &CallOpts::default()).await?;

// 에이전트 한 턴 — 메신저 봇 메시지 처리
let resp = agent_turn(&AgentTurnRequest {
    message: "이번 주 회의 정리해줘".into(),
    agent: Some("main".into()),     // 또는 session_id / to (E.164) 중 하나 필수
    channel: Some("telegram".into()),
    model: Some("ollama/phi3.5".into()),
    deliver: true,                  // 게이트웨이가 응답을 채널로 자동 전송
    ..Default::default()
}).await?;
```

## Frontend API

`src/lib/api.js`:

```js
import { openclawCliCall, openclawCliAgent } from "@/lib/api";

const health = await openclawCliCall("health");
const resp = await openclawCliAgent({
  message: msg,
  agent: "main",
  channel: "telegram",
  model: "ollama/phi3.5",
  deliver: true,
});
```

## 보안 — 셸 인젝션 방어

- 메서드명은 영문/숫자/`.`/`-`/`_`만 허용 (`validate_method`)
- 옵션 값들은 `\0`·개행 금지 (`validate_simple_arg`)
- 메시지 본문(`-m`)은 `tokio::process::Command::arg`가 자동으로 escape — 별도 검증 안 함
- `--params` JSON은 직렬화된 문자열이라 OK

## 사용자 셋업 (wrapper 책임 외)

이 wrapper는 그저 CLI를 부른다. 다음은 별도 사전 작업:

1. **`openclaw configure`** — 인터랙티브 셋업. 모델 프로바이더(Ollama API 등) 등록.
2. **`OLLAMA_API_KEY=anything`** 환경변수 — Ollama 프로바이더 인식용 더미 값. v2026.5.6 요구사항.
3. **device pairing** — 게이트웨이가 새 클라이언트의 scope를 처음 허용할 때 필요.
4. **게이트웨이 토큰** — 게이트웨이를 `--auth token --token <T>`로 띄웠으면 `OPENCLAW_GATEWAY_TOKEN=<T>` 환경변수 또는 `opts.token`으로 전달.

## Python 측 마이그레이션 (후속 PR)

현재 `python-sidecar/services/openclaw_client.py`는 미작동 WebSocket 코드. 다음 PR에서:

1. 새 모듈 `services/openclaw_cli_client.py` — `asyncio.create_subprocess_exec`로 `openclaw gateway call` / `openclaw agent` 호출
2. `routers/agent.py`·`routers/skills.py`의 호출부를 새 모듈로 swap
3. `agent.py`는 현재 streaming generator(`async for frame in client.send_message`)를 쓰고 있어 한 번에 1 응답 받는 방식으로 흐름을 단순화 필요. tool 실행 + 승인 흐름이 영향받음 — 보수적으로 재설계.
4. 마이그레이션 끝나면 옛 `openclaw_client.py` 폐기, `websockets` 의존성도 제거.

## 검증

자동:
- `cargo test --lib openclaw_cli` — 7개 단위 테스트 (셸 메타문자 거부, JSON 추출 등)
- `cargo clippy --no-deps` 무경고

수동 (DevTools Console, Tauri 앱 실행 상태에서):

```js
// 게이트웨이 health
const r = await window.__TAURI__.core.invoke("openclaw_cli_call", { method: "health" });
console.log(r);

// 에이전트 한 턴 (사전: openclaw configure + OLLAMA_API_KEY 환경변수)
const a = await window.__TAURI__.core.invoke("openclaw_cli_agent", {
  req: {
    message: "ping",
    agent: "main",
    channel: null,
    deliver: false,
    model: "ollama/phi3.5",
    session_id: null,
    to: null,
    opts: { token: null, password: null, url: null, timeout_ms: 60000, expect_final: true },
  },
});
console.log(a);
```
