# 김대리 구조 지도 (README에서 분리, 2026-09-06)

> README를 사용자 안내 위주로 줄이면서 옮겨 온 설계 설명이다. 2026-09-06 실측 대조에서
> 아래 항목은 **현재 코드와 다르다**고 확인됐으니 읽을 때 감안한다(개발일지 2026-09-06 참조).
>
> - 식별자: 실제는 `kimdaeri` / `com.kimdaeri.app` (크레이트명만 `office-claw`).
> - `components/credentials`·`components/dashboard`는 없고 `activity`·`chat`·`home`·`layout`이 있다.
> - `/telegram` `/slack` `/discord` 라우터는 제거됐다. `/agent`·`/harness`·`/trace`가 있다.
> - `ipc.rs`의 `#[tauri::command]`는 68개가 아니라 78개.
> - 사이드카 배포본은 PyInstaller 단독이 아니라 **Nuitka `--module` + PyInstaller**(`sidecar-hardened.spec`).
> - Excel Live 응답 모델의 실제 필드는 `ok / action / approval_required / pending_approval / result / reason`.
> - 모바일 승인 UI와 데스크톱 QR 카운트다운은 **이미 구현돼 있다**.
> - 워크플로는 `pr-check.yml`·`release.yml`에 `cross-platform-check.yml`을 더해 3개.

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

Excel Live 명령 레퍼런스는 `docs/excel-live-command-list.txt`.

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
