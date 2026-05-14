# OpenClaw 사용법 (프로젝트 내 활용 가이드)

> **대상 독자**: ajou-ai (`office_claw`) 기능을 개발/유지보수하는 사람.
> **OpenClaw 버전 기준**: `2026.5.6` (`openclaw --version`로 확인).
> **공식 문서**: <https://docs.openclaw.ai> · <https://github.com/openclaw/openclaw>
>
> 이 문서는 OpenClaw의 *전체* 기능이 아니라, 우리 프로젝트에 직접 영향을 주는
> 부분만 추려 정리한다. 새 기능 기획·디버깅 시 가장 먼저 펴 보는 레퍼런스가 되는 게 목표다.

---

## 1. OpenClaw란 무엇이고, 우리는 왜 쓰는가

OpenClaw는 **로컬에서 돌아가는 멀티 채널 AI 어시스턴트의 컨트롤 플레인(게이트웨이)**이다. 사용자가 이미 쓰는 채널(Telegram/Slack/Discord/iMessage 등)에서 메시지를 받고, LLM과 *스킬(skill)* 을 호출해 PC 작업을 수행한다.

**ajou-ai에서의 역할**

| 우리 컴포넌트 | 역할 | OpenClaw에 의존하는 부분 |
| --- | --- | --- |
| Tauri (Rust) | OpenClaw 게이트웨이 *프로세스 라이프사이클* 관리 | `src-tauri/src/openclaw.rs` — 게이트웨이 spawn/health/install 검사 |
| Python sidecar | 보안 검사 + LLM/스킬 호출 *오케스트레이션* | `python-sidecar/.../services/openclaw_client.py` — WebSocket 클라이언트 |
| React UI | 설치 안내, 상태 표시, HITL 승인 | `src/components/guide/*`, `OpenClawInstallPrompt.jsx` |

> ajou-ai는 **모든 LLM/스킬 요청을 Python sidecar → OpenClaw 게이트웨이** 순서로 보낸다. 프롬프트 마스킹과 DENIED 차단은 sidecar에서 수행하고, 도구 실행과 채널 전달은 OpenClaw가 담당한다.

---

## 2. 빠르게 손에 익히기

### 2.1 설치 (가장 권장되는 방식)

```bash
npm install -g openclaw@latest      # macOS / Linux / Windows 공통
# 또는: pnpm add -g openclaw@latest
# 또는: bun add -g openclaw@latest
```

> Node 24 권장(최소 22.14+). 설치 후 `which openclaw`로 PATH에 잡히는지 확인.

대안 1줄 설치 스크립트(공식): `curl -fsSL https://openclaw.ai/install.sh | bash`

### 2.2 첫 실행

```bash
openclaw onboard --install-daemon   # 인터랙티브: 모델 키, 채널, 데몬 등록까지 한 번에
openclaw gateway health             # 게이트웨이 응답 확인
openclaw dashboard                  # 브라우저 컨트롤 UI 열기 (선택)
```

`--install-daemon`은 macOS launchd / Linux systemd / Windows schtasks에 사용자 서비스를 등록해 게이트웨이가 부팅 후에도 살아있게 한다.

### 2.3 비인터랙티브 설치 (스크립트 자동화)

ajou-ai의 자동 설치 모달에서는 `npm install -g openclaw@latest`까지만 실행하고, 데몬 등록은 사용자가 필요할 때 따로 진행하도록 둔다. 비인터랙티브 onboard가 필요하면:

```bash
openclaw onboard --non-interactive --accept-risk \
  --flow quickstart \
  --auth-choice anthropic-api-key --anthropic-api-key sk-ant-... \
  --gateway-port 18789 --gateway-auth token --gateway-token "$OPENCLAW_GATEWAY_TOKEN"
```

`--accept-risk`는 `--non-interactive`와 함께 반드시 줘야 한다.

### 2.4 헷갈리기 쉬운 두 명령 — bare `openclaw` vs 게이트웨이

| 명령 | 실제 동작 | 우리 흐름에서의 의미 |
| --- | --- | --- |
| `openclaw` (인자 없이) | **Crestodian** — "ring-zero setup and repair helper". 인터랙티브 TTY로 `set default model openai/gpt-5.2 --yes` 같은 *config 작업*을 LLM과 대화로 처리 | **채팅 세션 아님.** 사용자가 "openclaw 그냥 띄우면 세션 열리는데?"라 보일 수 있지만, 실제로는 설정 마법사. 우리 ajou-ai 메시지 라우팅 대상 ✗ |
| `openclaw gateway --port 18789` | WebSocket 게이트웨이 서버 | 우리 sidecar의 `openclaw_client.py`가 붙는 곳. 이게 살아있어야 채팅·스킬·세션이 동작 |
| `openclaw agent --local -m "..."` | 임베디드 런타임으로 1턴 실행 (게이트웨이 불필요) | 게이트웨이 없이 가는 *유일한* 진짜 옵션. 단 매 호출마다 Node 콜드스타트(~1-2초), 스트리밍 없음, sidecar 재작성 필요. **현재 채택 안 함.** |
| `openclaw tui --local` | 위와 같은 임베디드 런타임 + TUI | CLI 검증용 |

> **결론**: 우리 아키텍처는 게이트웨이를 켜놓고 sidecar가 WebSocket으로 붙는 구조. bare `openclaw`나 `--local`로 대체 불가. 단 디버깅 시 `openclaw agent --local -m "ping"`로 모델 키/설정이 살아있는지 빠르게 검증할 수 있다.

### 2.6 OpenClaw + Ollama 로컬 모델 자동 설정 (현재 기본 경로)

ajou-ai는 **OpenClaw 게이트웨이가 Ollama의 로컬 모델을 호출하는 구조**를 기본 경로로 채택했다. `LocalAISetupWizard.jsx`가 모든 사전 작업을 자동화한다.

**자동화 단계** (모두 멱등 — 이미 충족된 항목은 즉시 skip)

| # | 단계 | 명령 | 우리 코드 |
| --- | --- | --- | --- |
| 1 | OpenClaw 설치 | `npm install -g openclaw@latest` | `@tauri-apps/plugin-shell` Command |
| 2 | OpenClaw 게이트웨이 시작 | `openclaw gateway --port 18789` (자식 프로세스 spawn) | `openclaw_ensure_running` IPC |
| 3 | Ollama 설치 | macOS: `brew install ollama` / 그 외: 다운로드 페이지 안내 | shell scope `brew-install-ollama` |
| 4 | Ollama 데몬 시작 | macOS: `brew services start ollama` / 그 외: 사용자 직접 | shell scope `brew-services-start-ollama` |
| 5 | 모델 다운로드 | `ollama pull <model>` (스트리밍 진행 표시) | shell scope `ollama-pull` (regex 검증) |
| 6 | OpenClaw → Ollama 연결 | `openclaw config set models.providers.ollama.baseUrl http://127.0.0.1:11434` + `agents.defaults.model ollama/<model>` | `openclaw_use_ollama` IPC |

**진단 IPC**

| 명령 | 반환 |
| --- | --- |
| `ollama_status` | `{ installed, version, running, port: 11434, models: [...] }` |
| `openclaw_installed` | `{ installed, version, source }` |
| `openclaw_status` | `{ state: running\|stopped\|error, port, message }` |

**자동 노출 조건** (`LocalAISetupWizard`)

- `onboardingComplete === true`
- `llmConfig.provider === "ollama"` — Claude 사용자에게는 자동 노출 X (수동 트리거 가능)
- 위 6개 단계 중 하나라도 미충족
- 사용자가 같은 세션에 dismiss하지 않음

**수동 트리거**

```js
window.dispatchEvent(new Event("private-claw:open-local-ai-setup"));
// 하위 호환: "private-claw:open-openclaw-install"도 동일하게 동작
```

**capabilities/default.json에 추가된 shell scope**

```json
[
  { "name": "brew-install-ollama", "cmd": "brew", "args": ["install", "ollama"] },
  { "name": "brew-services-start-ollama", "cmd": "brew", "args": ["services", "start", "ollama"] },
  { "name": "ollama-pull", "cmd": "ollama",
    "args": ["pull", { "validator": "^[A-Za-z0-9._:/\\-]+$" }] },
  { "name": "ollama-serve", "cmd": "ollama", "args": ["serve"] }
]
```

### 2.5 `openclaw setup --wizard` — 사용자 설정 온보딩

`openclaw setup --wizard`(또는 등가 명령 `openclaw onboard`)는 OpenClaw 자체의 *사용자 설정* 인터랙티브 마법사다. ajou-ai 자동 설치 모달과는 별개로, 사용자가 OpenClaw를 처음 쓸 때 **모델 키·게이트웨이 인증·채널 등록·스킬 선택**을 한 번에 마쳐 두는 용도.

**다루는 섹션** (`openclaw configure --section <X>`의 X와 동일):

| 섹션 | 무엇을 설정하나 | 우리 ajou-ai와의 관계 |
| --- | --- | --- |
| `workspace` | agent의 작업 디렉토리 (기본 `~/.openclaw/workspace`) | 사용자가 자기 파일을 두는 곳. ajou-ai의 `open_workspace_folder` IPC와 충돌하지 않도록 주의 |
| `model` | LLM 프로바이더 + API 키 + 기본 모델 | ajou-ai 설정의 `llmConfig`와 *이중 관리*. 사이드카는 자체 키를 쓰고, OpenClaw는 자기 키를 쓴다 |
| `web` | 웹 컨트롤 UI 노출 옵션 | 거의 무시. ajou-ai가 자체 UI 제공 |
| `gateway` | 포트, bind, auth 모드 (token/password), 토큰 값 | **18789, token auth가 우리 기본값**. 사용자가 마법사에서 다른 포트로 잡으면 sidecar 연결 실패하므로 가이드 필요 |
| `daemon` | launchd/systemd 등록 + 런타임(`node`/`bun`) | ajou-ai가 자식 프로세스로 spawn하므로 *데몬 등록은 권장하지 않는다* — 두 인스턴스가 같은 포트 다툼 |
| `channels` | Telegram/Slack/Discord/WhatsApp/iMessage 등 22+ 채널 자격 | 현재 ajou-ai sidecar가 직접 운영 중. v3.0+ OpenClaw 위임 후보 |
| `plugins` | 외부 플러그인 등록 | 우리 시나리오 거의 없음 |
| `skills` | bundle된 53개 스킬 중 활성/설치 | `gog`, `notion`, `1password` 등을 켜두면 ajou-ai 채팅에서 호출 가능 |
| `health` | health check 자동 실행 | `openclaw doctor`로 대체 가능 |

**비인터랙티브 호출 (스크립트 자동화)**

```bash
# 가장 단순: workspace + model만 설정하고 위저드 생략
openclaw setup --non-interactive --mode local \
  --workspace ~/Documents/ajou-workspace

# 모델 + 게이트웨이까지 한 번에 (onboard 사용)
openclaw onboard --non-interactive --accept-risk \
  --flow quickstart \
  --auth-choice anthropic-api-key --anthropic-api-key "$ANTHROPIC_API_KEY" \
  --gateway-port 18789 --gateway-auth token --gateway-token "$OPENCLAW_GATEWAY_TOKEN" \
  --workspace ~/Documents/ajou-workspace
```

> `--accept-risk` 는 `--non-interactive` 사용 시 필수. "에이전트는 시스템 권한이 강력하니 위험을 인지했다"는 사용자 동의 토큰.

**ajou-ai에서의 위치 (현재)**

지금은 자동 설치 모달이 `npm install -g openclaw@latest` + `openclaw gateway --port 18789` 까지만 처리하고, **사용자 설정은 손대지 않는다**. 사용자가 `setup --wizard`를 직접 돌려야 하는 경우:
- ajou-ai 가 사용하는 LLM 프로바이더와 OpenClaw의 모델은 *별개*라서 보통 안 돌려도 됨
- 단 OpenClaw 게이트웨이 토큰 인증을 켜야 하거나, OpenClaw의 스킬을 직접 쓰려는 경우 필요

**향후 옵션**: 사용자에게 "터미널에서 `openclaw setup --wizard` 실행해보세요" 배너를 띄우거나, ajou-ai 내부 폼으로 `--non-interactive` 플래그를 채워 호출하는 방식으로 통합 가능. 인터랙티브 TUI는 Tauri 안에서 잘 안 돌아가므로 *반드시* 비인터랙티브 모드를 거쳐야 한다.

---

## 3. 게이트웨이(Gateway)

### 3.1 우리 프로젝트에서의 위치

- **기본 포트**: `18789` (`OpenClawState::default().port` 참조)
- **프로토콜**: WebSocket (`ws://127.0.0.1:18789`)
- **dev 모드 동작**(`src-tauri/src/openclaw.rs`):
  1. 18789 포트에 이미 떠 있으면 그대로 사용
  2. 안 떠 있으면 `openclaw gateway --port 18789` 직접 실행 시도
  3. 실패 시 `npx --yes openclaw gateway --port 18789` 폴백
- **prod 모드**: 항상 자식 프로세스로 게이트웨이를 띄우고, 앱 종료 시 `Drop`에서 `kill`.

### 3.2 자주 쓰는 CLI

```bash
openclaw gateway --port 18789                  # 포어그라운드로 실행
openclaw gateway --auth token --token <SECRET> # 토큰 인증
openclaw gateway --auth password --password <P>
openclaw gateway --bind loopback               # 127.0.0.1 only (기본)
openclaw gateway --bind tailnet                # Tailscale 노출
openclaw gateway --force                       # 같은 포트 점유 프로세스 강제 종료 후 시작
openclaw gateway health --json                 # health 체크 (CI/스크립트)
openclaw gateway install --runtime node        # 데몬 등록 (launchd/systemd/schtasks)
openclaw gateway diagnostics                   # 지원 진단 번들 export
```

### 3.3 인증 모드 — ajou-ai의 선택

| 모드 | 언제 쓰나 | 우리 코드 |
| --- | --- | --- |
| `none` | 절대 쓰지 말 것 (로컬 외부 노출 위험) | — |
| `token` | **dev 기본**. 사이드카가 토큰을 들고 게이트웨이로 연결 | `~/Library/Application Support/office_claw/openclaw_token.json` 캐시 |
| `password` | 사용자가 사람-친화적 보안 원할 때 | UI 미구현 |
| `trusted-proxy` | 외부 reverse-proxy 앞에 둘 때 | 미사용 |

토큰 캐시 경로 (`openclaw_client.py`):

| OS | 경로 |
| --- | --- |
| macOS | `~/Library/Application Support/office_claw/openclaw_token.json` |
| Windows | `%LOCALAPPDATA%/office_claw/openclaw_token.json` |
| Linux | `~/.local/share/office_claw/openclaw_token.json` |

### 3.4 health 검사 패턴

OpenClaw 게이트웨이는 **HTTP `/health`가 없다**. TCP 연결 가능 여부로만 판정한다(`is_gateway_ready`). 그래서 "포트는 열려있지만 WebSocket이 응답하지 않는" 상태를 잡으려면 `openclaw gateway health` CLI를 쓰거나, 실제 WebSocket 핸드셰이크까지 해야 한다.

---

## 4. WebSocket 프레임 프로토콜 (sidecar 관점)

`openclaw_client.py`가 사용하는 프레임 타입을 정리한다. 새 기능을 sidecar에 붙일 때 가장 자주 보는 표.

### 4.1 세션 (sessions)

| 보내는 프레임 | 받는 프레임 | 용도 |
| --- | --- | --- |
| `sessions.create` `{ model? }` | `sessions.created` `{ sessionId }` | 새 세션 만들기 |
| `sessions.send` `{ sessionId, message, requestId }` | `sessions.message`(스트리밍) → `sessions.done` | 메시지 전송 + 응답 스트림 |
| `sessions.list` | `sessions.list` `{ sessions: [...] }` | 활성 세션 목록 |

응답 라우팅은 `requestId` 우선, 없으면 `sessionId`로 큐 매핑(`_session_queues`).

### 4.2 스킬 (skills)

| 보내는 프레임 | 받는 프레임 | 용도 |
| --- | --- | --- |
| `skills.install` `{ requestId, skillName }` | `skills.install.result` | ClawHub에서 스킬 설치 |
| `skills.list` `{ sessionId }` | `skills.list` | 현재 세션이 사용 가능한 도구 목록 |
| `skills.catalog` | `skills.catalog` | 설치 가능한 카탈로그 |

타임아웃 권장값: 설치는 ≥30초, 일반 호출은 10초.

### 4.3 폴백 정책

`openclaw_client.py`는 게이트웨이 실패 시 **graceful fallback** — Ollama 직접 호출 또는 Claude API 직접 호출로 떨어진다(`messenger/base.py`의 분기 참고). 새 기능을 추가할 때 fallback 경로를 함께 고려할 것.

---

## 5. 채널(channels) — Telegram / Slack / Discord / Gmail

### 5.1 우리 프로젝트의 현재 매핑

| 우리가 노출한 기능 | 우리 IPC | OpenClaw 대응 |
| --- | --- | --- |
| 텔레그램 봇 시작/중지 | `telegram_start/stop/status` (sidecar 직접 운영) | OpenClaw `channels add --channel telegram --bot-token ...` 와 별도 — 현재는 우리 sidecar가 직접 botpoll |
| Slack 봇 | `slack_start/stop/status` (sidecar 직접) | `channels add --channel slack --bot-token --app-token` |
| Discord 봇 | `discord_start/stop/status` (sidecar 직접) | `channels add --channel discord --bot-token` |
| Gmail | `gmail_*` (sidecar Google API 직접 호출) | OpenClaw `gog` 스킬로 이관 가능 (`openclaw skills info gog`) |

> **현재 상태**: 메신저 봇은 sidecar가 직접 운영. OpenClaw의 채널 매니저는 *옵션 B* 로만 사용. Gmail은 v3.0에서 OpenClaw의 `gog`(Google Workspace CLI) 스킬로 이관 예정 (`SetupGuide.jsx`의 GmailGuide 안내 참조).

### 5.2 OpenClaw에 채널을 위임하면 얻는 것

- 사용자 인증 흐름 일원화 (`openclaw channels login --channel whatsapp` 등 OAuth pairing)
- 22+ 채널 동시 운영 (WhatsApp, Signal, Matrix, iMessage, Mattermost, Feishu, LINE 등)
- `agent --channel slack --reply-to ...`로 답신 라우팅 통일

이관 시 주의: ajou-ai의 보안 마스킹/DENIED 룰을 그대로 적용하려면 **반드시 sidecar 경유**여야 한다. 채널을 OpenClaw에 직접 붙이면 sidecar 우회가 가능해지므로, OpenClaw → sidecar → OpenClaw 회귀 호출 구조를 짜거나, sidecar가 OpenClaw의 webhook proxy 역할을 해야 한다.

### 5.3 자주 쓰는 채널 CLI

```bash
openclaw channels list                              # 등록된 계정/채널 보기
openclaw channels add --channel telegram --bot-token <T>
openclaw channels add --channel slack --bot-token <B> --app-token <A>
openclaw channels add --channel discord --bot-token <T>
openclaw channels capabilities --channel slack      # 가능한 의도/스코프
openclaw channels logs                              # 최근 채널 로그 (디버깅)
openclaw channels remove --channel telegram --account <id>
```

---

## 6. 스킬(skills) — 우리 시나리오에 매핑

### 6.1 우리가 곧 쓸 스킬 후보

`openclaw skills list`(53개 중 일부) 에서 ajou-ai 로드맵과 직결되는 것:

| 스킬 | 한 줄 요약 | 우리 연결점 |
| --- | --- | --- |
| `gog` | Google Workspace CLI (Gmail/Calendar/Drive/Sheets/Docs) | Gmail/Calendar 통합 — v3.0 이관 대상 |
| `slack` | Slack 메시지/리액션/멤버 정보 | 향후 Slack 봇 → OpenClaw 위임 시 |
| `discord` | Discord 채널/메시지 | 동일 |
| `notion` | Notion 페이지 관리 | 워크스페이스 노트 동기화 |
| `apple-notes`, `apple-reminders` | macOS 네이티브 메모/알림 | macOS 사용자 대상 quick action |
| `imsg` | iMessage/SMS | 답신 채널 옵션 |
| `1password` | 1Password CLI | 자격증명 자동 주입 (Phase 5 보안 보강) |
| `coding-agent` | Codex/Claude Code/OpenCode 위임 | 자동화 작업 위임 |
| `clawhub` | 스킬 설치/관리 자체 | 사용자가 직접 스킬 추가하도록 노출 가능 |
| `browser-automation` | 브라우저 자동화 | 웹 작업 위임 (예: 메일 확인 → 답신 작성) |

> 스킬은 대부분 별도 CLI 바이너리(`gog`, `memo`, `remindctl` 등)를 요구한다. `openclaw skills check`로 어떤 의존성이 부족한지 한번에 확인 가능.

### 6.2 자주 쓰는 스킬 CLI

```bash
openclaw skills list                # 전체 목록 (ready / needs setup 표시)
openclaw skills check               # 의존성 체크
openclaw skills info <name>         # 상세 (SKILL.md 경로, 필요한 바이너리)
openclaw skills search <query>      # ClawHub 검색
openclaw skills install <name>      # 설치
openclaw skills update              # 업데이트
```

### 6.3 우리 IPC 매핑

| Tauri command | 호출 경로 | OpenClaw 프레임 |
| --- | --- | --- |
| `skills_installed` | sidecar `/skills/installed` | `skills.list` (세션별) |
| `skills_install` | sidecar `/skills/install` | `skills.install` |
| `skills_catalog` | sidecar `/skills/catalog` | `skills.catalog` |
| `agent_chat` | sidecar `/agent/chat` | `sessions.create` + `sessions.send` |
| `agent_sessions` | sidecar `/agent/sessions` | `sessions.list` |

---

## 7. 에이전트 호출 (`openclaw agent`)

CLI에서 직접 한 턴을 돌리는 가장 빠른 방법:

```bash
openclaw agent -m "지난주 매출 요약해줘"
openclaw agent --agent ops -m "Summarize logs" --thinking medium
openclaw agent --session-id 1234 -m "어제 메일 확인해줘"
openclaw agent --to +15555550123 -m "ping"          # 세션을 전화번호로 키잉
openclaw agent --channel slack --deliver -m "..."  # 답을 Slack으로 송출
openclaw agent --json -m "..."                      # JSON 응답 (스크립트용)
openclaw agent --local -m "..."                    # 게이트웨이 없이 임베디드 실행
```

`--thinking` 레벨: `off|minimal|low|medium|high|xhigh|adaptive|max` — Claude/OpenAI의 thinking 토큰 활용. ajou-ai에서 사용자가 "꼼꼼히 검토" 옵션을 선택하면 `medium`이상으로 매핑하면 좋다.

---

## 8. 설정 파일 & 상태 디렉토리

| 위치 | 내용 |
| --- | --- |
| `~/.openclaw/openclaw.json` | 메인 설정 (`openclaw config file`로 확인) |
| `~/.openclaw/plugins/` | 플러그인/스킬 설치 위치 |
| `~/.openclaw-dev/` | `--dev` 프로파일용 격리 상태 |
| `~/.openclaw-<name>/` | `--profile <name>`용 격리 상태 |

설정을 코드/스크립트로 만질 때:

```bash
openclaw config file                                  # 설정 경로
openclaw config get gateway.port                      # 단일 값
openclaw config set gateway.port 19001 --strict-json  # 단일 값 set
openclaw config patch --file ./patch.json5            # 객체 머지
openclaw config schema                                # JSON schema 출력 (validate용)
openclaw config validate                              # 현재 설정 유효성 검사
```

> ajou-ai 빌드 후 첫 실행 시 사용자의 설정 파일을 *건드리지 않는다*. 우리는 게이트웨이를 우리 프로세스로 띄울 뿐, 사용자가 별도로 글로벌 설치한 OpenClaw를 통째로 쓰는 모델.

---

## 9. 보안 / 승인 (HITL)

### 9.1 우리 흐름

1. 사용자가 메시지 보냄 (Telegram/Slack/Discord/앱 UI)
2. **sidecar가 마스킹 + DENIED 검사** (이 단계는 OpenClaw 외부)
3. 위험한 명령(파일 삭제, 외부 송신 등)은 `security_get_pending_approvals`에 큐잉 → 텔레그램 봇 또는 `ApprovalDialog`로 사용자 승인 요청
4. 승인되면 sidecar가 `sessions.send`로 OpenClaw에 전달
5. OpenClaw가 도구 실행 후 응답 스트림

### 9.2 OpenClaw 측 approvals (참고)

OpenClaw 자체에도 도구 실행 승인 기능이 있다 — 우리가 1차 검사를 sidecar에서 하므로 **2차 방어선**으로 활용 가능:

```bash
openclaw approvals get                           # 현재 스냅샷
openclaw approvals allowlist --agent default     # per-agent 허용 명령
openclaw approvals set --file ./approvals.json
```

allowlist를 좁게 잡아두면, sidecar 우회로 도달한 호출도 OpenClaw 단에서 한 번 더 막을 수 있다.

---

## 10. 디버깅 체크리스트

### 10.1 게이트웨이가 안 뜬다

1. `openclaw --version` — 바이너리 자체가 PATH에 잡히는가?
2. `lsof -i :18789` — 포트 점유 프로세스 확인. `openclaw gateway --force`로 재시작 가능
3. `openclaw gateway --verbose` — 인증/바인드 모드 로그 확인
4. `~/.openclaw/openclaw.json` 의 `gateway.mode` 가 `local` 인가? 아니면 `--allow-unconfigured` 필요
5. 로그 위치: `openclaw gateway diagnostics` 로 한 번에 번들 export

### 10.2 sidecar에서 게이트웨이 연결이 끊긴다

1. `openclaw_client.py` 의 재연결 로그 확인 — `[openclaw] 연결 실패 (시도 N/M)`
2. 토큰 캐시(`openclaw_token.json`) 만료 — 삭제 후 재생성
3. 게이트웨이 재시작 시 `sessions.list`로 살아남은 세션 확인 → 죽었으면 새로 만들기

### 10.3 스킬이 "needs setup"

`openclaw skills info <name>` 의 **Requirements: Binaries** 항목 확인. 거의 다 별도 CLI(`gog`, `memo`, `wacli`, ...)를 brew/npm으로 설치해야 한다.

### 10.4 ajou-ai가 자동 설치 화면을 계속 띄운다

> 2026-05-07 패치: 흐름이 "게이트웨이 18789 응답 여부"를 1차 트리거로 한다.
>
> - **Trigger**: `openclaw_status`가 `running`이 아니면 prompt 노출
> - **분기**:
>   - 바이너리 미설치 → `npm install -g openclaw@latest` → 자동으로 `openclaw gateway --port 18789` spawn → ready 대기 → 온라인
>   - 바이너리 있음, 게이트웨이만 꺼짐 → 설치 단계 건너뛰고 바로 spawn → ready 대기 → 온라인
> - 모든 spawn은 Tauri `openclaw_ensure_running` 명령(idempotent)이 처리. 같은 포트에 게이트웨이가 이미 살아있으면 즉시 OK 반환하므로 중복 spawn 위험 없음.
> - 자식 프로세스 lifecycle은 앱과 동기화 — 앱 종료 시 `OpenClawState::drop`이 child를 kill. 영구 데몬 등록을 원하면 `openclaw daemon install`을 별도로 안내.
>
> 관련 코드:
>   - Rust: `src-tauri/src/openclaw.rs::spawn_openclaw`, `is_openclaw_installed`
>   - IPC: `openclaw_status`, `openclaw_installed`, `openclaw_ensure_running`
>   - UI: `src/components/guide/OpenClawInstallPrompt.jsx`, `SetupGuide.jsx::AutoInstallModal`

---

## 11. 참고 링크

- 시작 가이드: <https://docs.openclaw.ai/start/getting-started>
- 온보딩 위저드: <https://docs.openclaw.ai/start/wizard>
- Docker 설치: <https://docs.openclaw.ai/install/docker>
- Nix 설치: <https://github.com/openclaw/nix-openclaw>
- DeepWiki(자동 생성 문서): <https://deepwiki.com/openclaw/openclaw>
- Discord 커뮤니티: <https://discord.gg/clawd>
- npm 패키지: <https://www.npmjs.com/package/openclaw>
- 라이선스: MIT

---

## 12. 변경 로그

| 날짜 | 작성자 | 변경 |
| --- | --- | --- |
| 2026-05-07 | claude | 최초 작성. 자동 설치 감지 분리(`openclaw_installed`) 패치와 함께 정리. |
| 2026-05-07 | claude | 자동 설치 흐름을 "(필요 시) 설치 → 자동 spawn → ready 대기 → 온라인"으로 통합. `openclaw_ensure_running` IPC 추가. |
| 2026-05-07 | claude | §2.4 bare `openclaw`(Crestodian) vs gateway vs `--local` 비교, §2.5 `setup --wizard` 섹션 9개 매핑 + 비인터랙티브 호출 예시 추가. |
| 2026-05-07 | claude | §2.6 신설. `LocalAISetupWizard` 통합 — OpenClaw + Ollama 6단계 자동화. `ollama` 모듈, `ollama_status`/`openclaw_use_ollama` IPC, brew/ollama-pull capabilities 추가. |
