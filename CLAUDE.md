# Office-Claw 프로젝트 규칙

## 코드 작성 원칙 — 모듈/객체지향

**모든 새 기능은 재사용 가능한 모듈로 작성하고 다른 곳에서 활용한다.** 한 컴포넌트/파일에 로직을 인라인으로 쏟아붓지 않는다.

### 구체적 가이드라인

1. **상태는 모듈이 소유한다.** 도메인 로직과 그 상태는 한 모듈 안에 묶는다. UI는 그 모듈을 구독해서 읽기만 한다.
   - 예: `src/lib/statusManager.js`의 `STATUS_MODULES.openclaw`는 자기 상태(`store.modules.openclaw`)와 액션(`check/install/start`)을 모두 소유한다. Dashboard·StatusBar·Sidebar·LocalAISetupWizard는 같은 모듈을 구독만 한다.

2. **표시(presentation)와 데이터(data)를 분리한다.**
   - 데이터 토큰/타입 → `src/lib/*.js` (예: `statusTokens.js`)
   - 데이터 매핑/액션 → `src/lib/*.js` (예: `statusManager.js`)
   - 공용 UI primitive → `src/components/ui/*.jsx` (예: `status.jsx`의 `StatusDot/StatusBadge/StatusRow/StatusBanner`)
   - 페이지/도메인 UI → `src/components/<domain>/*.jsx` — 위 3개를 조합만 한다

3. **새 도메인이 생기면 모듈 세트를 만든다.**
   - `src/store/<domain>Store.js` — Zustand store
   - `src/lib/<domain>Manager.js` — 액션 (check/start/stop/refresh 등)
   - `src/hooks/use<Domain>*.js` — React hook (필요 시)
   - 그다음에 UI 작성

4. **중복 fetch를 피한다.** 같은 데이터를 여러 컴포넌트가 각각 fetch하지 않는다 — 중앙 store/manager에 모으고 구독한다.

5. **Rust 측도 같은 원칙.** `src-tauri/src/`에 도메인별 모듈 (`openclaw.rs`, `ollama.rs`, `installer.rs`). 각 모듈은 자기 상태(필요 시 `State<Mutex<...>>`)와 그 상태에 대한 함수만 소유. IPC는 `ipc.rs`에서 얇은 wrapper로 expose.

### 안티패턴 (피해야 할 것)

- 컴포넌트 안에 `fetch`/`invoke` 직접 호출 (status fetch 등 도메인 로직)
- 같은 상태를 여러 컴포넌트가 각자 state로 들고 있기
- UI 컴포넌트 안에 비즈니스 로직 (모델명 검증, 톤 결정 등)
- 한 파일에 여러 도메인의 로직 섞기

### 좋은 예시 (이 프로젝트의 기존 패턴)

| 도메인 | Store | Manager/Lib | Rust 모듈 | UI primitive |
|---|---|---|---|---|
| 시스템 상태 | `store/statusStore.js` | `lib/statusManager.js`, `lib/statusTokens.js` | — | `components/ui/status.jsx` |
| 로컬 AI 설정 단계 | — | `lib/localAISetup.js` (buildPlan/isAllReady) | — | `components/guide/LocalAISetupWizard.jsx` (조합만) |
| Tauri IPC | — | `lib/api.js` (모든 invoke wrapper 1곳) | `src-tauri/src/ipc.rs` | — |
| OS 자격증명 | — | api.js의 `rustCredential*` | `src-tauri/src/keyring_svc.rs` | — |
| 감사 로그 | — | api.js의 `rustAudit*` | `src-tauri/src/audit.rs` | — |
| Excel tool-calling | — | (sidecar) `services/excel_tool_schemas.py`(함수 명세) · `excel_tool_agent.py`(루프) · `excel_actions.py`(실행) | — | WorkspacePage 채팅 (조합만) |
| 모바일 릴레이(QR 페어링) | `store/relayStore.js` | `lib/relayManager.js`(액션·상태폴링) · `lib/relayQr.js`(QR 페이로드 계약, 순수) | `ipc.rs`의 `relay_pair`/`relay_status`/`relay_disconnect` | `components/relay/RelayPairing.jsx` (조합만) |
| 모바일 브랜드 테마 | — | (mobile) `lib/theme/brand_palette.dart`(색 토큰, 순수) · `brand_theme.dart`(ThemeData + `AgentStatusColors` 확장) · `agent_status_tokens.dart`(상태→라벨·색) | — | (mobile) `lib/widgets/brand_wordmark.dart` · `agent_status_chip.dart` (조합만) |

새 기능을 추가할 때 이 표에 한 줄이 더 늘어나야 한다.

> **2026-05 Rust 보안 계층 노트**: Keyring · Audit 두 도메인은 Python sidecar의 동명 서비스와 *같은* OS Keychain·파일(`audit.jsonl`, `credentials_registry.json`)을 공유한다. 신규 코드는 Rust 경로(`rustCredential*`, `rustAudit*`)를 우선 사용하되, Python 측은 자체 라우터 안에서 자기 서비스를 계속 쓴다.
>
> **2026-07 LLM 경로 노트**: OpenClaw 게이트웨이 통합은 `feat/ollama-tool-calling`에서 전면 제거됐다. LLM 호출은 Ollama OpenAI 호환 API(`/v1/chat/completions`) + `tools`(function calling) 단일 경로다. Excel 함수 명세는 `excel_tool_schemas.py`가 단일 소스이며, 권한(SAFE/CONFIRM/DENIED)은 `tool_registry.py`가 계속 소유한다.
>
> **2026-07 브랜드 색 노트**: 김대리 색의 단일 소스는 브랜드 SVG의 `fill`·`stop-color`다(`apps/desktop/src/assets/brand-logo-{light,dark}.svg`, `apps/mobile/assets/brand-wordmark.svg`). 모바일은 `BrandPalette.core`(#2DB400) **시드 하나**에서 M3가 라이트/다크를 전부 파생하고, 데스크톱 `index.css`의 `--primary`·`--sidebar-*`는 같은 값을 HSL로 옮긴 것이다(흰 전경 대비 4.5:1을 넘기려 명도만 24%로 낮춤). **코드에서 새 브랜드 색을 짓지 않는다** — 필요하면 SVG를 먼저 고치고 값을 옮긴다. 단, 상태색 중 `thinking`(앰버)·`remoteControlling`(바이올렛)은 "정상 동작 중"과 구분돼야 해서 의도적으로 브랜드 밖 색이다. 워드마크 SVG는 그라디언트의 어두운 끝(#0B3F0A·#015F00)이 다크 지면에서 대비 1.5:1로 사라지므로 `BrandWordmark`가 다크에서 단색(#46C642)으로 눕힌다.
>
> **2026-07 QR 페어링 계약 노트**: QR 페이로드 `{"v":1,"relay","pairing_id","code"}`는 데스크톱 `lib/relayQr.js`와 모바일 `apps/mobile/lib/pairing/pairing_service.dart`가 공유하는 계약이다. 사이드카 `/relay/pair`는 `relay_url`로 주므로 `relay`로 **매핑**해야 한다 — 어긋나면 스캔이 조용히 실패하므로 `lib/relayQr.test.js`가 형태를 고정한다(순수 모듈로 분리한 이유).

## 빌드/실행

> **모노레포 구조 (2026-07 `feat/monorepo-relay`)**: 데스크톱 앱 = `apps/desktop/`(프론트엔드 + `src-tauri/`), Python 사이드카 = `services/sidecar/`, 중계 서버 = `services/relay/`, 공용 계약·코드 = `packages/`(`protocol`·`py-shared`). 경로 매핑: `src/`→`apps/desktop/src/`, `src-tauri/`→`apps/desktop/src-tauri/`, `python-sidecar/`→`services/sidecar/`. 아래 표의 파일 경로도 이 접두사 기준으로 읽는다.

- `cd apps/desktop && npm run tauri:dev` — 전체 앱 (Rust + Vite + Tauri webview). 개발 시 기본.
- `cd apps/desktop && npm run dev` — vite-only. UI 레이아웃만 빠르게 확인할 때.
  주의: `invoke()` 호출은 모두 실패한다 (Tauri runtime 없음 → "cannot read properties of undefined").
- Rust 변경 후에는 `tauri:dev`를 재시작해야 새 IPC 명령이 등록된다.
- (루트에서) `bash scripts/dev.sh` — 사이드카 + Vite + Tauri를 한 번에 기동.

## 커밋/푸시 전 체크 (CI 미러)

`.github/workflows/pr-check.yml`에 정의된 4개 잡(`rust-check`, `python-check`, `frontend-check`, `flutter-check`)을 그대로 미러링한다. **커밋 전 영역별로 해당 명령을 직접 돌려 통과 확인.** 빠뜨리고 푸시하면 GitHub Actions에서 떨어진다.

### Rust (`apps/desktop/src-tauri/`)

```bash
cd apps/desktop/src-tauri
cargo fmt --check                          # 또는 자동 적용: cargo fmt
cargo clippy --all-targets -- -D warnings  # -D warnings = 경고를 에러로 승격
```

- `cargo fmt --check` 가 가장 자주 떨어지는 항목 — fmt 기본 스타일과 다른 코드를 그대로 푸시하면 즉시 실패.
- `cargo clippy --no-deps` 만 돌리면 안 됨 — CI는 `--all-targets -- -D warnings`라서 테스트 코드의 경고까지 잡힘.
- (참고) CI는 `binaries/office-claw-sidecar-*` 더미 파일을 만들고 clippy를 돌린다. 로컬은 PyInstaller 산출물이 있으면 그걸 쓰고, 없으면 동일하게 더미를 만들거나 `cargo check`로 컴파일 가능 여부만 봐도 됨.

### Python (`services/sidecar/`)

```bash
cd services/sidecar
uvx ruff check .                           # lint
uv run pytest -q                           # unit tests
```

- `uv`가 없으면: `brew install uv` (또는 `astral.sh/setup-uv`의 설치 스크립트).
- 의존성 변경 시 `uv sync --frozen --extra dev` 한 번 더 (CI는 lockfile 고정).
- macOS 로컬은 OS Keychain 백엔드가 있어 keyring 호출이 실제 동작 — CI는 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` 환경변수로 no-op 처리한다는 점이 다름. 테스트가 OS Keychain에 부수효과를 남기지 않는지 확인할 것.

### Frontend (`apps/desktop`)

```bash
cd apps/desktop
npm ci                                     # CI와 동일하게 lockfile 고정 설치
npm run lint --if-present                  # lint 스크립트 존재 시
npm run test:unit --if-present             # 현재: node --test src/lib/*.test.js
```

- 빠른 확인이면 `npm run build`만 돌려도 import 경로 깨짐은 잡힘.

### Flutter 모바일 (`apps/mobile`)

```bash
cd apps/mobile
flutter pub get --enforce-lockfile         # CI와 동일하게 lockfile 고정 설치
flutter analyze                            # 정적 분석 (경고도 실패로 잡힘)
flutter test                               # unit tests
```

- CI는 Flutter **3.44.6**으로 고정돼 있다. 로컬 SDK가 다르면 analyze 결과가 갈릴 수 있으니 `flutter --version`으로 맞출 것. 올릴 때는 `pr-check.yml`의 `flutter-version`과 같이 올린다.
- `dart format --set-exit-if-changed`는 **CI에 넣지 않았다** — 기존 파일 다수가 이미 미준수라 켜는 순간 전부 빨개진다. 넣으려면 먼저 `dart format .`으로 전체를 한 번 정리하는 별도 커밋이 필요하다. 그전까지는 새로 만드는 파일만 `dart format <파일>`로 맞춘다.
- 빌드(APK/IPA)는 CI에서 돌리지 않는다. iOS 빌드는 macOS 러너가 필요하고 서명까지 얽혀서 PR 게이트에는 과하다.

### 한 번에 다 — 추천 alias

`.zshrc` / `.bashrc`에:

```bash
alias oc-precheck='cd /Users/skim/Desktop/project/office_claw && \
  (cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings) && \
  (cd services/sidecar && uvx ruff check . && uv run pytest -q) && \
  (cd apps/desktop && npm run test:unit --if-present) && \
  (cd apps/mobile && flutter analyze && flutter test)'
```

PR 만들기 직전 `oc-precheck` 한 번 — 넷 다 통과하면 CI도 통과.

## 한국어

- 코드 주석·UI 문자열·커밋 메시지: 한국어
- 변수/함수/타입명: 영어 (kebab-case 파일명, camelCase JS, snake_case Rust)
