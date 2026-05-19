# Rust 보안 계층 + OpenClaw 통합 트랙

> **작성**: 2026-05-19 · **갱신**: 2026-05-20
> **이 PR(#3)이 다루는 범위**: Rust 보안 계층(Keyring + Audit) 추가만.
> **별도 트랙**: 메신저 → OpenClaw 게이트웨이 통합은 `feat/openclaw-cli-wrapper`에서 진행.

## 0. 배경 — 실제 사용자 시나리오

제품의 핵심 흐름은 **모바일 사용자가 텔레그램·Discord·Slack 봇으로 대화 → 메시지가 OpenClaw 게이트웨이로 전달 → OpenClaw가 로컬 Ollama 모델을 LLM 엔진으로 사용해 응답**하는 것이다.

따라서 Tauri 데스크탑 앱이 Ollama에 *직접* 채팅 호출을 보낼 일은 없다. 데스크탑 앱의 책임은:

1. **설치/상태 관리** — Ollama·OpenClaw 바이너리 설치 안내, 모델 pull, 데몬 상태 표시 (이미 `installer.rs`, `ollama.rs`, `openclaw.rs`가 담당)
2. **사이드카 라이프사이클** — Python sidecar + OpenClaw 게이트웨이 자식 프로세스 관리 (`sidecar.rs`, `openclaw.rs`)
3. **보안 wrapper** — OS Keychain·감사로그 직결 (이번 PR로 추가)
4. **UI** — 설정/대시보드/설치 마법사 (React)

## 1. 이번 PR이 추가하는 것 — Rust 보안 계층

| 모듈 | 역할 | Python과의 관계 |
|---|---|---|
| `src-tauri/src/keyring_svc.rs` | OS Keychain set/get/delete/list (`keyring` crate 3.x) | `services/keyring_service.py`와 **같은** `SERVICE_NAMESPACE="office_claw"` + 동일한 `credentials_registry.json` 사용. 양쪽이 같은 데이터를 봄. |
| `src-tauri/src/audit.rs` | JSONL append-only 로그 + 마스킹 통계/차단 로그 쿼리 | `services/audit_service.py`와 **같은** `audit.jsonl` 파일에 append. 양쪽 동시 사용 가능 (POSIX O_APPEND atomic). |
| IPC 9개 (`rust_credential_*`, `rust_audit_*`) | 위 두 모듈의 얇은 Tauri wrapper | Python `routers/credentials.py`·`routers/audit.py`는 그대로 유지 — 신규 코드만 Rust 경로를 우선 사용. |

### 왜 이중 경로인가

- Python sidecar의 기존 라우터·서비스가 자기 KeyringService/AuditService를 계속 쓰는 것이 *깨지지 않음* (저장소가 같으므로 데이터 일관성 유지)
- 신규 기능은 Rust 경로(`rustCredential*` / `rustAudit*`)를 호출하면 sidecar 가용성과 독립적으로 동작 — Tauri만 떠 있으면 작동
- 점진 이전이 가능 — Phase 3 이후의 별도 PR에서 Python 라우터를 하나씩 Rust로 옮겨가도 OS Keychain·audit 파일은 그대로

### 검증 체크리스트

- [x] `cargo check` / `cargo clippy --no-deps` 무경고
- [x] `cargo test --lib` — 10개 그린 (ollama 5 + audit 3 + keyring_svc 2)
- [x] `npm run build` 통과
- [ ] DevTools: `rust_credential_set/get/delete/list` 라운드트립 (사용자 검증 필요)
- [ ] DevTools: `rust_audit_log` + `rust_audit_recent` (사용자 검증 필요)
- [ ] Python·Rust 동시 쓰기 sanity — Rust로 쓴 entry가 Python `getAuditLogs`에 보이는지 (사용자 검증 필요)

## 2. 별도 트랙 — OpenClaw 통합 (이 PR 범위 외)

현재 Python `services/openclaw_client.py`는 게이트웨이 v2026.5.6의 실제 핸드셰이크 프로토콜(server-initiated `connect.challenge` envelope, `connect.params.auth.token`)과 불일치한다. 즉 *코드는 있지만 작동한 적 없는 상태*. 메신저 봇 → OpenClaw 경로가 실제로 동작하려면 패치가 필요하다.

### 두 가지 접근

| 옵션 | 방식 | 작업량 | 메신저 시나리오 적합성 |
|---|---|---|---|
| A. WS 직결 + 프로토콜 패치 | 실제 v2026.5.6 핸드셰이크를 직접 구현 | 1~2시간 | 과스펙 (메신저는 어차피 비동기) |
| **C. CLI 서브프로세스 wrapper** | `openclaw call <method>`를 spawn해 JSON 결과 파싱 | 30~60분 | **적합** — 호출당 ~500ms 오버헤드는 LLM 생성시간(수 초)에 묻힘 |

→ 옵션 **C**로 진행. 별도 브랜치 `feat/openclaw-cli-wrapper`에서 다음 PR로 다룸.

### 다음 PR의 범위

- Rust 측: `tokio::process::Command`로 `openclaw call <method> --json <payload>`를 비동기 호출하는 wrapper 모듈
- Python 측: `services/openclaw_client.py`를 동일한 CLI 호출 방식으로 교체
- IPC/api.js: 메신저 봇 라우터가 호출할 thin layer
- e2e: 텔레그램 봇 → 메시지 → OpenClaw → 로컬 Ollama 모델 → 응답까지 한 바퀴

## 3. 비-목표 (의도적으로 안 하는 것)

- **Tauri ↔ Ollama 직결 채팅** — 사용자 시나리오에 없음 (Ollama는 OpenClaw 엔진으로만 사용)
- **Excel/Word/PDF 처리 Rust 이전** — Python 생태계(pandas, openpyxl, python-docx, reportlab) 우위. 보류
- **Google API / Gmail / Telegram 클라이언트 Rust 이전** — OAuth 자체 구현 비용 큼, Python 그대로
- **PII 마스킹 Rust 이전** — Presidio/spaCy 같은 Rust 등가물 없음. Python에서 유지
- **LangChain/LangGraph 도입** — 우리는 OpenClaw를 wrap하는 보안 레이어. 자체 에이전트 루프 구축 필요 없음
