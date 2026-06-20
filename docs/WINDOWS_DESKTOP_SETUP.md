# Windows에서 데스크톱 앱 실행하기

## 지금 상태 구분

| 구성요소 | 확인 방법 | 실패 시 증상 |
|----------|-----------|--------------|
| Ollama | `ollama list` | 모델 없음 |
| OpenClaw 게이트웨이 | `openclaw gateway health` → OK | 채팅 불가 |
| Python sidecar | `http://127.0.0.1:19532/health` | 앱 연결 실패 |
| **데스크톱 앱 (Tauri)** | `npm run tauri:dev` | `vite`/`cargo` 없음 오류 |

`start-local-stack.ps1`까지 성공했다면 **앞 세 개는 이미 동작 중**입니다.  
`vite`/`cargo` 오류는 **데스크톱 앱 빌드 도구만 없는 것**입니다.

## 1. 프론트 의존성 (한 번)

```powershell
cd "프로젝트\officeclaw"
npm ci
```

## 2. Rust 설치 (데스크톱 앱에 필수, 한 번)

PowerShell(관리자 권장):

```powershell
winget install Rustlang.Rustup
```

설치 후 **터미널을 새로 열고**:

```powershell
rustc --version
cargo --version
```

## 3. 실행 순서

**터미널 A** — 게이트웨이(이미 PowerShell에 띄워 둔 경우 그 창 유지, 아니면):

```powershell
schtasks /Run /TN "OpenClaw Gateway"
.\scripts\start-local-stack.ps1
```

**터미널 B** — 데스크톱 앱:

```powershell
cd "프로젝트\officeclaw"
npm run tauri:dev
```

`ajou-ai` 창이 뜨면 성공입니다.

## 4. Rust 없이 UI만 미리보기 (제한적)

```powershell
npm run dev
```

브라우저 `http://localhost:1420` — Tauri 기능(invoke)은 동작하지 않습니다.

## 5. 검증

```powershell
.\scripts\verify-local-stack.ps1
```

`전체 통과`면 백엔드는 정상입니다.
