# 김대리 (officeclaw)

**개인정보 보호 중심의 로컬 AI 업무 에이전트.** AI 추론(Ollama 로컬 LLM)도, 파일(Excel)도 사용자 PC를 떠나지 않는다.

- **데스크톱** — Tauri 앱 + Python FastAPI 사이드카가 xlwings COM으로 Excel을 직접 제어. LLM은 Ollama의 OpenAI 호환 API(`/v1/chat/completions`) + `tools`(function calling) 단일 경로.
- **모바일** — Flutter 앱이 **content-blind 릴레이**를 통해 내 데스크톱 에이전트를 원격 조종. 릴레이는 라우팅 헤더만 읽고 내용은 해석·저장하지 않는다.
- **권한 게이트** — Excel 함수 16종은 SAFE / CONFIRM / DENIED로 분류. 변경 작업은 반드시 사용자 승인을 거친다.

표시명은 **김대리**, 실행 바이너리·번들 식별자는 `officeclaw` / `com.officeclaw.app`.

---

## 모노레포 구조

```
office_claw/
├── apps/
│   ├── desktop/            # React(Vite+Tailwind) + src-tauri/ (Rust/Tauri)
│   └── mobile/             # Flutter 앱 (Riverpod, QR 페어링, 스트리밍 채팅)
├── services/
│   ├── sidecar/            # Python FastAPI 사이드카 (AI·Excel·보안 로직)
│   └── relay/              # content-blind WebSocket 중계 서버 (oc-relay)
├── packages/
│   ├── protocol/           # 와이어 프로토콜 SSOT (Pydantic → JSON Schema)
│   └── py-shared/          # sidecar↔relay 공용 순수 파이썬 (codec/replay/auth)
├── scripts/                # 개발·검증 스크립트
├── docs/                   # Excel Live 명령 리스트
└── .github/workflows/      # pr-check.yml (CI) + release.yml (태그 빌드)
```

각 패키지의 상세는 하위 README를 본다 — `services/relay/README.md`, `packages/protocol/README.md`, `packages/py-shared/README.md`.

---

## 계층별 역할

### `apps/desktop/src/` — 프론트엔드

React + Zustand. URL 라우팅 없이 `appStore.currentPage`로 화면 전환.

| 경로 | 역할 |
|---|---|
| `lib/api.js` | **모든 Tauri `invoke()` 단일 진입점** |
| `store/` | `appStore`(전역 UI·LLM 설정) · `statusStore`(Ollama·사이드카) · `relayStore`(모바일 연동) |
| `lib/statusManager.js`·`statusTokens.js` | 시스템 모듈 check/install/start 액션 + 상태 토큰 |
| `lib/relayManager.js`·`relayQr.js` | 페어링 액션·상태 폴링 + QR 페이로드 계약(순수 모듈, 테스트로 형태 고정) |
| `lib/localAISetupCore.js`·`localStack/` | LocalAISetupWizard 단계·플랜, 로컬 AI 스택 프리셋 |
| `components/ui/` | 공용 primitive (Button·Dialog·StatusDot·Toast 등) |
| `components/<domain>/` | workspace · relay · settings · security · audit · permissions · credentials · onboarding · guide · dashboard · conversations · cmdk · updater |

### `apps/desktop/src-tauri/src/` — Rust/Tauri

프로세스 관리·보안·OS 통합. `ipc.rs`가 68개 `#[tauri::command]`를 사이드카 HTTP 프록시로 노출한다.

`main.rs` · `lib.rs`(Builder·상태 등록) · `ipc.rs` · `sidecar.rs`(spawn·health 폴링·Bearer 토큰) · `ollama.rs` · `installer.rs`(설치 로그 스트리밍) · `shell.rs`(로그인 셸 러너) · `tray.rs` · `keyring_svc.rs` · `audit.rs`

> 흐름: Frontend `invoke()` → `ipc.rs` → `sidecar.rs` HTTP 프록시 → 사이드카 (Bearer 인증)
> Keyring·Audit은 Python 동명 서비스와 **같은** OS Keychain·파일을 공유한다. 신규 코드는 Rust 경로(`rustCredential*`, `rustAudit*`) 우선.

### `services/sidecar/` — Python FastAPI 사이드카

실제 AI·데이터 처리. PyInstaller로 단일 실행파일을 만들어 `apps/desktop/src-tauri/binaries/`에 배치한다.

등록 라우터 (전부 Bearer 인증):

| prefix | 용도 |
|---|---|
| (루트) `/health` | 헬스체크 |
| `/excel-live` | 자연어 채팅 + Excel COM 제어 (`/status` `/command` `/action` `/approval`) |
| `/relay` | 모바일 페어링 (`/pair` `/status` `/disconnect`) |
| `/llm` | LLM 설정 |
| `/workspace` | 파일 관리 |
| `/credentials` | OS Keyring |
| `/audit` · `/security` · `/permissions` | 감사 로그 · 마스킹/차단 · 도구 권한 |
| `/telegram` `/slack` `/discord` | 메신저 연동 |
| `/chat` · `/backup` · `/settings` · `/maintenance` | 세션 영속화 · 백업 · 설정 |

핵심 서비스: `excel_tool_schemas.py`(함수 명세 SSOT) · `excel_tool_agent.py`(tool-calling 루프) · `excel_actions.py`(실행 디스패처) · `tool_registry.py`(권한) · `excel_live_service.py`(xlwings COM) · `relay_client.py`(아웃바운드 WS) · `keyring_service.py` · `audit_service.py` · `masking_service.py`

### `services/relay/` — 중계 서버

데스크톱·모바일이 **둘 다 아웃바운드로** 접속하고, relay가 `pairing_id`로 두 소켓을 짝지어 프레임만 브리지한다(NAT 통과, 인바운드 포트 불필요).

`GET /health` · `POST /pair/start` · `POST /pair/complete` · `WS /ws/desktop` · `WS /ws/mobile`

### `packages/` — 공용 계약

- `protocol` — `Envelope`(평문 라우팅 헤더) + `Frame` discriminated union(`chat_user_msg` / `token_delta` / `stream_end` / `agent_status` / `approval_request` / `approval_response` / `ack` / `ping` / `pong` / `error`). Pydantic이 SSOT이고 `schema/*.json`은 생성물.
- `py-shared` — `codec`(Envelope↔WS 텍스트, `parse_routing`은 relay 전용) · `replay`(seq·재전송 버퍼) · `auth`(페어링 토큰 HMAC).

---

## Excel tool-calling 흐름

```
사용자 자연어 ("매출 열 다 더해줘")
  → WorkspacePage → excel_live_command IPC → 사이드카 /excel-live/command
  → excel_tool_agent: Ollama /v1/chat/completions (tools=excel_tool_schemas)
  → LLM이 tool_calls 반환: calculate_column_stat(column="매출", stat="sum")
  → tool_registry 권한 확인
      SAFE    → 즉시 실행 → 결과를 tool 메시지로 재주입 → LLM 최종 한국어 답변
      CONFIRM → 승인 대기 → 사용자 승인 후 /excel-live/approval에서 실행 → 결과 재주입 → 루프 재개
  → { action, result, assistant_text, executed_actions, approval_required, ... }
```

- 함수 명세 단일 소스는 `excel_tool_schemas.py` — 함수별 **Pydantic 모델 + docstring**에서 OpenAI `tools` JSON Schema를 자동 생성한다. LLM이 만든 인자도 같은 모델로 실행 전 검증되고, 검증 실패 메시지는 LLM에 재주입돼 자가 교정된다.
- 등록 함수 16종
  - **SAFE** — `list_workbooks` `select_workbook` `read_range` `save_workbook` `calculate_column_stat` `group_by_aggregate`
  - **CONFIRM (편집·서식)** — `write_range` `highlight_by_condition` `apply_border` `set_formula`
  - **CONFIRM (데이터 변환)** — `filter_rows` `sort_rows` `dedupe_rows` `drop_column` `rename_column` `add_column`
- 데이터 변환은 used range를 읽어 메모리에서 변환 후 같은 자리에 다시 쓰는 **라이브 write-back** — 삭제된 행/열이 화면에서 즉시 비워진다(파일 재생성 없음).
- 도구가 필요 없는 일반 대화도 같은 엔드포인트에서 `assistant_text`로 답변된다(별도 게이트웨이 없음).
- 채팅의 `범위 참조 삽입` 버튼은 현재 Excel 선택 범위를 `[[EXCEL_RANGE:A1:C3]]` + TSV 미리보기로 넣는다. 사용자가 범위를 말하지 않으면 실행 계층이 현재 선택 범위를 쓴다.

**스모크 테스트 예시**

| 입력 | 호출되는 함수 |
|---|---|
| `열린 통합문서 목록 보여줘` | `list_workbooks` |
| `A1:C10 조회해줘` | `read_range` |
| `매출 열 다 더해줘` | `calculate_column_stat(stat='sum')` |
| `지역별 매출 합계 알려줘` | `group_by_aggregate` |
| `C3에 120 입력해줘` | `write_range` (승인) |
| `A열에서 50 이상인 셀만 노란색 배경` | `highlight_by_condition` (승인) |
| `J1에 수식 =SUM(A1:A10) 적용` | `set_formula` (승인) |
| `매출 내림차순 정렬` / `중복 행 지워줘` | `sort_rows` / `dedupe_rows` (승인) |

전체 67개 검증 입력은 `TEST_INPUT_COMMANDS_EXCEL_LIVE.txt`, Excel Live 명령 레퍼런스는 `docs/excel-live-command-list.txt`.

---

## 모바일 원격 제어 흐름

```
데스크톱: /relay/pair → {pairing_id, code, relay_url} → QR 렌더 (RelayPairing)
모바일:   QR 스캔 → /pair/complete{code} 로 1:1 바인딩 → WS /ws/mobile 연결
채팅:     ChatUserMsg → relay → 사이드카 relay_client → tool-calling 루프
          → TokenDelta 스트리밍 / StreamEnd / AgentStatus 를 모바일로 push
```

QR 페이로드 `{"v":1,"relay","pairing_id","code"}`는 데스크톱 `lib/relayQr.js`와 모바일 `lib/pairing/pairing_service.dart`가 공유하는 계약이다. 사이드카는 `relay_url`로 주므로 `relay`로 **매핑**해야 하며, 어긋나면 스캔이 조용히 실패하므로 `relayQr.test.js`가 형태를 고정한다.

relay 주소 정책은 모바일 `lib/transport/relay_url.dart`가 단일 소스다 — Dart 소유 소켓이라 Android 매니페스트·iOS ATS로는 평문을 막을 수 없어서, 릴리스 빌드의 `wss` 강제를 이 순수 모듈이 담당한다(debug/profile은 로컬 http 허용).

페어링 code는 **TTL 120초 · IP당 10회/60초 rate-limit · 8 hex 엔트로피** 세 겹으로 방어한다(`oc_relay/pairing.py`·`rate_limit.py`). 셋이 곱해져야 성립하므로 하나를 줄이려면 나머지를 키워야 한다.

**현재 상태 / 한계** — QR 페어링 + 스트리밍 채팅까지 실기기 검증 완료. 모바일 쪽 `ApprovalRequest` 처리(원격 승인 UI)는 프레임만 정의돼 있고 아직 미구현이다. 데스크톱 QR도 TTL 만료 카운트다운·재발급 UI가 아직 없다(`/pair/start`의 `expires_in` 미사용). E2E 암호화, 재연결 재개(`seq`+`Ack`+`ReplayBuffer` 연동), 수평 확장(Redis 백플레인)도 프로덕션 전 강화 항목이다(`services/relay/README.md`).

---

## 빠른 시작

### 배포본 (비개발자, Windows 권장)

1. GitHub Releases에서 최신 패키지 다운로드 → 실행
2. 첫 실행 시 `LocalAISetupWizard`가 Ollama 설치(winget)·기동·모델 pull(`qwen3:4b` / `qwen3:8b`)을 자동 점검
3. 워크스페이스에서 Excel 파일을 열고 채팅으로 바로 작업

릴리스는 `v*` 태그 push 시 macOS(arm64·x64) + Windows(x64)로 빌드된다 — Tauri 앱 + 번들된 사이드카 바이너리. 윈도우 네이티브 빌드·배포 절차는 [`docs/build-and-release.md`](docs/build-and-release.md).

### 개발

```bash
# 0. (권장) 한 번에 기동 — 사이드카(:19532) + Vite(:1420) + Tauri
bash scripts/dev.sh

# ── 또는 수동으로 ──
# 1. 의존성
cd apps/desktop && npm ci && cd -
cd services/sidecar && uv sync --extra dev && cd -

# 2. 사이드카 바이너리 빌드 (최초 1회, 사이드카 코드 변경 시 재실행)
cd services/sidecar && uv run --extra dev python build_sidecar.py && cd -

# 3. 앱 실행
cd apps/desktop && npm run tauri:dev
```

- UI만 빠르게 볼 때는 `cd apps/desktop && npm run dev` (Tauri 없음 → `invoke()` 전부 실패).
- Rust 변경 후에는 `tauri:dev`를 재시작해야 새 IPC 명령이 등록된다.
- pip 환경이면 루트 `requirements.txt`로도 사이드카 런타임 의존성을 설치할 수 있다.

```bash
# 중계 서버 (모바일 연동 테스트 시)
cd services/relay && uv sync --extra dev && uv run python -m oc_relay   # PORT 기본 8787

# 모바일
cd apps/mobile && flutter pub get && flutter run
```

실기기 테스트에서는 데스크톱 페어링 화면의 relay 주소를 `127.0.0.1` 대신 LAN IP로 바꿔야 한다.

---

## 커밋/푸시 전 체크 (CI 미러)

`.github/workflows/pr-check.yml`의 4개 잡을 그대로 미러링한다. `lefthook`이 pre-commit(lint)·pre-push(test)로 자동 실행하지만, PR 전 한 번 직접 돌리는 걸 권한다.

```bash
# Rust
cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings

# Python 사이드카
cd services/sidecar && uvx ruff check . && uv run pytest -q

# Frontend
cd apps/desktop && npm run test:unit --if-present

# Flutter 모바일 (CI는 3.44.6 고정)
cd apps/mobile && flutter pub get --enforce-lockfile && flutter analyze && flutter test
```

- `cargo fmt --check`가 가장 자주 떨어진다. `--all-targets -- -D warnings`라 테스트 코드 경고까지 잡힌다.
- CI는 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`으로 keyring을 no-op 처리한다(macOS 로컬은 실제 Keychain 사용) — 테스트가 OS Keychain에 부수효과를 남기지 않는지 확인할 것.
- CI에 없는 검증은 수동으로: `cd services/relay && uv run pytest -q`, `scripts/verify-local-stack.ps1`(로컬 스택 스모크), `scripts/verify-excel-live-e2e.mjs`(Windows + Excel 필요).
- 프로토콜 스키마를 고쳤으면 `cd packages/protocol/python && uv run python ../scripts/export_schema.py` 재생성 후 diff가 비어야 한다.

---

## 핵심 설계 원칙

1. **상태는 모듈이 소유** — 도메인 로직·상태는 `lib/` 모듈 안에, UI는 store 구독만
2. **표시와 데이터 분리** — `lib/*.js`(데이터) → `components/ui/*.jsx`(primitive) → 도메인 UI(조합)
3. **중복 fetch 없음** — 같은 데이터는 중앙 store/manager 1곳에서
4. **IPC 단일 진입점** — 모든 Tauri invoke는 `lib/api.js` 경유
5. **계약은 SSOT 하나** — 프로토콜은 `packages/protocol`, Excel 함수 명세는 `excel_tool_schemas.py`, 권한은 `tool_registry.py`
6. **브랜드 색은 SVG가 원본** — `assets/brand-logo-*.svg` / `assets/brand-wordmark.svg`의 값을 코드로 옮길 뿐, 새 색을 코드에서 짓지 않는다

상세 규칙·컨벤션은 `CLAUDE.md`. 제품 tier·수익화 기획은 `docs/product/`(로컬 전용, 버전관리 제외).
