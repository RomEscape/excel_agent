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
| Excel tool-calling | — | (sidecar) `services/excel_tool_schemas.py`(함수 명세) · `excel_tool_agent.py`(루프) · `excel_actions.py`(실행) | — | ChatPage (조합만) |
| 에이전트 채팅 | `store/chatStore.js`(세션 목록·진행 상태) + appStore의 `agentMessages`·`activeSessionId` | `lib/chatManager.js`(전송/세션/승인 액션) · `lib/chatSessions.js`(오늘·어제 그룹핑, 순수) · `lib/excelRangeContext.js`(범위 참조 블록, 순수) | — | `components/ui/chat.jsx`(버블·컴포저·칩) |
| 엑셀 결과 표현 | — | `lib/excelResult.js`(action+result → 표시 모델, 순수) | — | `components/ui/result-card.jsx`(표·막대·통계 카드) |
| 모바일 릴레이(QR 페어링) | `store/relayStore.js` | `lib/relayManager.js`(액션·상태폴링) · `lib/relayQr.js`(QR 페이로드 계약, 순수) | `ipc.rs`의 `relay_pair`/`relay_status`/`relay_disconnect` | `components/relay/RelayPairing.jsx` (조합만) |
| 모바일 브랜드 테마 | — | (mobile) `lib/theme/brand_palette.dart`(색 토큰, 순수) · `brand_theme.dart`(ThemeData + `AgentStatusColors` 확장) · `agent_status_tokens.dart`(상태→라벨·색) | — | (mobile) `lib/widgets/brand_wordmark.dart` · `agent_status_chip.dart` (조합만) |
| relay 페어링 보안 | — | (relay) `oc_relay/pairing.py`(code 발급·TTL·바인딩) · `oc_relay/rate_limit.py`(시도 제한, 순수) | — | — (`app.py`가 두 모듈을 결합만) |
| 모바일 relay 주소 정책 | — | (mobile) `lib/transport/relay_url.dart`(스킴 검증·정규화 + 평문 차단, 순수) | — | — (`relay_transport.dart`·`pairing_service.dart`가 구독만) |

새 기능을 추가할 때 이 표에 한 줄이 더 늘어나야 한다.

> **2026-05 Rust 보안 계층 노트**: Keyring · Audit 두 도메인은 Python sidecar의 동명 서비스와 *같은* OS Keychain·파일(`audit.jsonl`, `credentials_registry.json`)을 공유한다. 신규 코드는 Rust 경로(`rustCredential*`, `rustAudit*`)를 우선 사용하되, Python 측은 자체 라우터 안에서 자기 서비스를 계속 쓴다.
>
> **2026-07 LLM 경로 노트**: OpenClaw 게이트웨이 통합은 `feat/ollama-tool-calling`에서 전면 제거됐다. LLM 호출은 Ollama OpenAI 호환 API(`/v1/chat/completions`) + `tools`(function calling) 단일 경로다. Excel 함수 명세는 `excel_tool_schemas.py`가 단일 소스이며, 권한(SAFE/CONFIRM/DENIED)은 `tool_registry.py`가 계속 소유한다.
>
> **2026-07 브랜드 색 노트**: 김대리 색의 단일 소스는 브랜드 SVG의 `fill`·`stop-color`다(`apps/desktop/src/assets/brand-logo-{light,dark}.svg`, `apps/mobile/assets/brand-wordmark.svg`). 모바일은 `BrandPalette.core`(#2DB400) **시드 하나**에서 M3가 라이트/다크를 전부 파생하고, 데스크톱 `index.css`의 `--primary`·`--sidebar-*`는 같은 값을 HSL로 옮긴 것이다(흰 전경 대비 4.5:1을 넘기려 명도만 24%로 낮춤). **코드에서 새 브랜드 색을 짓지 않는다** — 필요하면 SVG를 먼저 고치고 값을 옮긴다. 단, 상태색 중 `thinking`(앰버)·`remoteControlling`(바이올렛)은 "정상 동작 중"과 구분돼야 해서 의도적으로 브랜드 밖 색이다. 워드마크 SVG는 그라디언트의 어두운 끝(#0B3F0A·#015F00)이 다크 지면에서 대비 1.5:1로 사라지므로 `BrandWordmark`가 다크에서 단색(#46C642)으로 눕힌다.
>
> **2026-07 QR 페어링 계약 노트**: QR 페이로드 `{"v":1,"relay","pairing_id","code"}`는 데스크톱 `lib/relayQr.js`와 모바일 `apps/mobile/lib/pairing/pairing_service.dart`가 공유하는 계약이다. 사이드카 `/relay/pair`는 `relay_url`로 주므로 `relay`로 **매핑**해야 한다 — 어긋나면 스캔이 조용히 실패하므로 `lib/relayQr.test.js`가 형태를 고정한다(순수 모듈로 분리한 이유).
>
> **2026-08 페어링 code 방어 노트**: 페어링 code의 방어는 **TTL(120초) · rate-limit(IP당 10회/60초) · 엔트로피(8 hex = 2^32)** 세 가지가 곱해져야 성립한다. 하나씩은 부족하다 — TTL만 있으면 초당 1만 회 공격에 창당 약 7% 확률로 뚫리고, rate-limit만 있으면 미소비 code가 쌓여 "아무거나 하나만 맞히면 되는" 상태가 된다. **셋 중 하나를 줄이려면 나머지를 키워야 한다.** rate-limit 키는 클라이언트 IP이고, `X-Forwarded-For`는 위조 가능하므로 기본 비신뢰다 — 리버스 프록시가 들어오는 XFF를 **덮어쓰도록** 설정한 경우에만 `RELAY_TRUST_PROXY=1`로 켠다. 전역 잠금(전체 실패 N회 → 엔드포인트 차단)은 공격자가 정상 사용자의 페어링을 막는 DoS 수단이 되므로 의도적으로 넣지 않았다.
>
> TTL 도입으로 QR은 120초 후 만료된다. `/pair/start`가 `expires_in`을 함께 주므로 **데스크톱 UI는 이 값으로 카운트다운·재발급을 붙여야 한다** — 안 붙이면 사용자는 이유 없는 페어링 실패만 본다(현재 `RelayPairing.jsx` 미구현, 후속 과제).
>
> **2026-08 데스크톱 채팅 우선 레이아웃 노트**: 앱의 주 작업면은 채팅이다(`currentPage` 기본값 `"chat"`). 왼쪽은 `ConversationSidebar`(사용자 칩 → 새 대화 → 오늘/어제 그룹 대화 목록)이고, **채팅·대시보드·워크스페이스·대화 모니터링·설정 진입은 그 사이드바 푸터에 있다** — 디자인 원본(`8a6513e`의 `DesktopAppShell.jsx`)은 채팅 표면만 그려서 내비게이션이 아예 없었기 때문에 진입 경로가 사라지지 않도록 푸터에 붙인 것이다. 지우지 말 것.
>
> 푸터의 **`채팅` 항목은 없으면 안 된다**. 대화 목록 클릭은 *다른* 대화로 가고 `새 대화`는 화면을 비우므로, 이 항목이 빠지면 대시보드에 갔다가 *보던 대화로* 돌아올 경로가 Cmd+K뿐이다.
>
> 접힘(Cmd/Ctrl+B)은 **통째 숨김이 아니라 64px 아이콘 레일**이다 — 대화 목록만 숨고 내비게이션은 남는다. 통째로 없애면 접는 순간 모든 페이지 진입 경로를 잃고 돌아올 길이 StatusBar 구석 버튼뿐이라 사용자가 길을 잃는다. 접힘 모양은 `ConversationSidebar`가 직접 소유하므로 `Layout`에서 조건부 언마운트하지 않는다.
>
> 그 Figma export는 7570px 캔버스에 5개 화면을 절대좌표로 늘어놓은 **정적 목업**이라 컴포넌트로 쓸 수 없었다(props·state 없음, 문자열 하드코딩). 시각 규격만 가져오고 색은 전부 테마 토큰으로 옮겼다 — 목업의 `lime-600/100/800`은 브랜드 초록의 Figma 근사치이므로 **그대로 쓰면 안 된다**(브랜드 색 노트 참조). 목업의 `● ● ● 김대리 AI` 타이틀바도 옮기지 않았다: Figma가 "데스크톱 창"임을 나타내려 그린 장식이고 실제 Tauri 창에는 OS 타이틀바가 이미 있다.
>
> 인라인 결과 카드(표·막대)는 `lib/excelResult.js`가 `action`+`result`를 표시 모델로 번역해서 만든다. 기존 `formatExcelLiveResult()`는 데이터를 한 줄 문장으로 눌러버려 `read_range`의 `values`가 버려졌었다 — 그래서 문자열 대신 구조화 모델을 돌려주되 `summary` 필드로 종전 문장을 그대로 유지한다(세션 영속화·메신저 전송이 그 문자열에 의존). **카드가 붙는 결과를 추가하려면 `toResultView()`에 case를 넣고 `result-card.jsx`에 렌더를 더한다.**
>
> **2026-08 모바일 평문 차단 노트**: 모바일의 TLS 강제는 **매니페스트/plist가 아니라 Dart 코드**(`apps/mobile/lib/transport/relay_url.dart`)가 책임진다. 안드로이드 `usesCleartextTraffic`·네트워크 보안 설정과 iOS ATS는 *플랫폼이 소유한* 소켓에만 걸리는데, 이 앱의 통신은 `package:http`와 `web_socket_channel` 둘 다 Dart 소유 소켓이라 적용되지 않는다(Flutter 공식: "If the socket is owned by Dart/Flutter, no policy will be enforced" — flutter/flutter#106678은 not planned로 닫힘). 그래서 `kAllowInsecureRelayByDefault = !kReleaseMode`로 debug·profile만 평문을 허용하고, relay 주소는 QR·수동입력 어느 경로든 `normalizeRelayBaseUrl`을 통과시킨다. **새 네트워크 경로를 추가하면 이 함수를 반드시 태울 것** — 안 태우면 릴리스에서 평문이 그대로 나간다.
>
> 안드로이드 `INTERNET` 권한은 별개다. 이건 플랫폼이 UID 레벨에서 막으므로 Dart 소켓도 걸린다 — Flutter 템플릿이 `src/debug`·`src/profile`에만 넣어주기 때문에 `src/main`에 직접 선언해야 릴리스 APK가 네트워크를 쓴다. iOS는 실기기에서 LAN 주소로 붙을 때 iOS 14+ 로컬 네트워크 권한 팝업이 뜨므로 `NSLocalNetworkUsageDescription`이 필요하다.

## 빌드/실행

> **모노레포 구조 (2026-07 `feat/monorepo-relay`)**: 데스크톱 앱 = `apps/desktop/`(프론트엔드 + `src-tauri/`), Python 사이드카 = `services/sidecar/`, 중계 서버 = `services/relay/`, 공용 계약·코드 = `packages/`(`protocol`·`py-shared`). 경로 매핑: `src/`→`apps/desktop/src/`, `src-tauri/`→`apps/desktop/src-tauri/`, `python-sidecar/`→`services/sidecar/`. 아래 표의 파일 경로도 이 접두사 기준으로 읽는다.

- `cd apps/desktop && npm run tauri:dev` — 전체 앱 (Rust + Vite + Tauri webview). 개발 시 기본.
- `cd apps/desktop && npm run dev` — vite-only. UI 레이아웃만 빠르게 확인할 때.
  주의: `invoke()` 호출은 모두 실패한다 (Tauri runtime 없음 → "cannot read properties of undefined").
- Rust 변경 후에는 `tauri:dev`를 재시작해야 새 IPC 명령이 등록된다.
- (루트에서) `bash scripts/dev.sh` — 사이드카 + Vite + Tauri를 한 번에 기동.

> **윈도우 네이티브 빌드 / 배포**: 상세 절차는 [`docs/build-and-release.md`](docs/build-and-release.md) (개발용/배포용 구분). 반복되는 함정 2가지 — (1) `tauri dev`도 `externalBin`(`binaries/office-claw-sidecar-<target>[.exe]`) 파일이 **존재**해야 빌드 통과(없으면 빈 placeholder 생성). (2) dev 모드 사이드카는 `services/sidecar/.venv`로 뜬다 → 윈도우는 `uv sync`로 venv 생성 필요(WindowsApps `python`은 가짜 스텁). 배포용 단일 설치파일은 `tauri build`/릴리스 CI가 PyInstaller 사이드카·WebView2를 번들하며 — **빌드 툴체인(Rust/MSVC/Node)은 `.exe`에 안 들어간다.**

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
