# 빌드 & 배포 가이드

kimdaeri(김대리) 데스크톱 앱의 **개발용 실행 환경 셋업**(Dev)과 **배포용 단일 설치파일 생성**(Release)을 다룬다. 플랫폼별 차이, 특히 **윈도우 네이티브** 셋업을 포함한다.

> 모노레포 경로: 데스크톱 앱 = `apps/desktop/`(프론트 + `src-tauri/`), 사이드카 = `services/sidecar/`. (자세한 구조는 `CLAUDE.md`)

---

## 핵심 개념 — 빌드 툴체인 ≠ .exe에 들어가는 것

이 구분을 놓치면 "왜 사용자한테 Rust/Python까지 깔라고 해야 하지?" 하는 혼동이 생긴다.

| 구분 | 예시 | 최종 사용자 설치파일에 포함? |
|---|---|---|
| **빌드 툴체인** (개발자 머신 전용) | Node, Rust, MSVC C++ 빌드툴, uv, PyInstaller | ❌ 안 들어감 |
| **런타임 의존성** (설치파일에 번들) | PyInstaller 사이드카, WebView2, 프론트엔드(임베드) | ✅ 들어감 |

- 컴파일된 `.exe`는 기계어라서 **컴파일러(MSVC/Rust)가 그 안에 들어갈 이유가 없다.**
- 최종 사용자는 Python·Rust·Node·MSVC를 **설치할 필요가 없다.** 설치파일 하나면 끝.

### `tauri dev` vs `tauri build`

| | `tauri dev` (개발) | `tauri build` (배포) |
|---|---|---|
| 용도 | 개발·디버깅, hot-reload | 사용자 배포용 단일 설치파일 |
| 사이드카 | Python **venv**로 실행 (`services/sidecar/.venv`) | PyInstaller **.exe 번들**(`externalBin`) |
| 산출물 | 없음 (그냥 실행) | `.msi` / `.exe`(NSIS) / `.dmg` |

### 타겟 트리플별 사이드카 파일명 규칙

Tauri `externalBin`은 **현재 타겟 트리플**에 맞는 파일이 있어야 한다 (`tauri.conf.json`의 `binaries/office-claw-sidecar`).

| 플랫폼 | 파일명 |
|---|---|
| Windows | `binaries/office-claw-sidecar-x86_64-pc-windows-msvc.exe` |
| macOS (Apple Silicon) | `binaries/office-claw-sidecar-aarch64-apple-darwin` |
| macOS (Intel) | `binaries/office-claw-sidecar-x86_64-apple-darwin` |
| Linux | `binaries/office-claw-sidecar-x86_64-unknown-linux-gnu` |

> `binaries/office-claw-sidecar-*`는 `.gitignore`에 걸려 있어 커밋되지 않는다(각 빌드에서 새로 생성).

---

## 개발용 (Dev)

### 공통

- `cd apps/desktop && npm run tauri:dev` — 전체 앱. 개발 시 기본.
- (루트) `bash scripts/dev.sh` — 사이드카 + Vite + Tauri 한 번에 기동.
- **dev 모드 사이드카는 `services/sidecar/.venv`로 뜬다** (`src-tauri/src/sidecar.rs`의 `spawn_dev_sidecar_process`):
  - 포트 `19532` / auth-token `dev-token` 고정
  - `.venv/Scripts/python.exe`(윈도우) · `.venv/bin/python`(macOS/Linux)를 찾음
  - 앱 시작 시 19532가 이미 떠 있으면 그걸 쓰고(예: `dev.sh`), 없으면 venv로 자동 기동

### 윈도우 네이티브 dev 셋업 (한 번만)

> **왜 네이티브인가:** Tauri는 OS 웹뷰에 컴파일된다 — 윈도우 = **WebView2**, WSL/리눅스 = WebKitGTK. WSL에서 띄운 화면은 실제 윈도우 사용자가 보는 화면이 아니고, OS 통합(Windows Credential Manager, Excel COM, `C:\` 경로)도 WSL에선 동작하지 않는다. **WSL이 아니라 윈도우 터미널에서 빌드/실행할 것.**

1. **툴체인**: Node, Rust(`rustup`, 기본 타겟 `x86_64-pc-windows-msvc`), Git, WebView2 런타임(Win10 21H2+ / Win11엔 기본 존재).
2. **MSVC C++ 빌드툴** — Rust 링크에 필수(없으면 `cargo build`가 링크 단계에서 실패):
   - GUI: "Visual Studio Installer" → *Build Tools 2022* → **Modify** → **"C++를 사용한 데스크톱 개발"** 체크 → Install
   - CLI(관리자): `& "C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe" modify --installPath "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools" --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet --norestart --wait`
3. **리포를 윈도우 네이티브 경로에** 둔다 (예: `C:\dev\officeclaw`). `\\wsl$`(9p)에서 빌드하면 느리고 불안정.
4. **JS 의존성**: `cd apps\desktop && npm install` — 반드시 **윈도우 npm**으로(플랫폼 네이티브 바이너리: esbuild/rollup/@tauri-apps/cli). WSL의 리눅스 npm으로 `/mnt/c`에 깔면 안 됨.
5. **사이드카 venv**: `cd services\sidecar && uv sync` — uv가 필요한 Python을 자동 provision하므로 시스템 Python이 없어도 됨. `.venv\Scripts\python.exe` 생성.
   - uv 미설치 시: `irm https://astral.sh/uv/install.ps1 | iex` (사용자 폴더, 관리자 불필요)
6. **externalBin placeholder** — `tauri dev`도 사이드카 파일이 **"존재"해야** 빌드가 통과한다(dev에선 실행은 안 하고 존재만 검사). 실 배포용이 아니면 빈 파일이면 됨:
   - cmd: `type nul > apps\desktop\src-tauri\binaries\office-claw-sidecar-x86_64-pc-windows-msvc.exe`
   - PowerShell: `New-Item apps\desktop\src-tauri\binaries\office-claw-sidecar-x86_64-pc-windows-msvc.exe -ItemType File`
7. **실행**: `cd apps\desktop && npm run tauri:dev` → 네이티브 윈도우 앱 창.

### 자주 겪는 함정

- **`resource path binaries\office-claw-sidecar-...exe doesn't exist`** → 6번 placeholder 누락. 빈 파일 생성.
- **사이드카가 안 붙음 / Microsoft Store 창이 뜸** → 윈도우 `python`이 WindowsApps 스텁(가짜)이라 실 Python·venv가 없는 상태. 5번 `uv sync`로 venv 생성 후 `tauri:dev` 재시작.
- **`tauri dev` 재시작 필요** — Rust 변경 후엔 재시작해야 새 IPC 명령이 등록됨. 사이드카 venv를 새로 만든 경우도 앱을 껐다 켜야 자동 기동됨.

---

## 배포용 (Release)

### 단일 설치파일에 뭐가 들어가나

- **Rust 앱**(프론트엔드 HTML/JS 임베드) + **PyInstaller 사이드카**(`externalBin`으로 번들) + **WebView2 부트스트래퍼**(`tauri.conf.json`의 `webviewInstallMode: downloadBootstrapper` → 설치 시 없으면 자동 다운로드).
- 최종 사용자: **설치파일 하나** → Python·Rust·Node·MSVC 전부 불필요.

### CI 태그 릴리스 (정석 — 권장)

`.github/workflows/release.yml`이 이미 구성돼 있다. **버전 태그(`v*`)를 push하면** 자동 실행:

```bash
git tag v0.1.0
git push origin v0.1.0
```

> **산출물은 이 저장소가 아니라 `sadStoneTurtle/kdr_release`로 나간다.** 소스 저장소는 개발 이력이고, 사용자가 받는 설치파일만 배포 저장소에 둔다.

#### 1단계 `build` — 플랫폼별 빌드

매트릭스는 **Apple Silicon macOS + Windows x64 두 개뿐**이다(Intel Mac은 의도적 제외 — 러너 청구 분이 10배).

1. `uv sync` + `uv pip install pyinstaller`
2. **PyInstaller `--onefile`** 로 사이드카 빌드 → `binaries/office-claw-sidecar-{target}[.exe]` 로 복사
3. **번들 기동 스모크** — `office-claw-sidecar --smoke-test`
4. `npm ci` + `npm run build` (프론트)
5. `tauri-action`이 `tauri build` (**릴리스는 만들지 않는다** — `tagName`을 주지 않으면 빌드만 하고 `artifactPaths`를 낸다)
6. 자산 이름을 고정 규칙으로 바꿔 workflow artifact로 업로드

#### 2단계 `publish` — 배포 저장소에 Draft 생성

`needs: build`로 **한 번만** 돈다. 매트릭스 잡 2개가 각자 릴리스를 만들려 들면 경합이 나서 릴리스가 둘 생기거나 한쪽이 실패하므로, 생성은 단일 잡이 전담한다. 한쪽 빌드가 깨지면 여기까지 오지 않아 **반쪽짜리 릴리스가 생기지 않는다.**

- `gh release create --repo sadStoneTurtle/kdr_release --draft` → **검토 후 직접 Publish**
- `-rc`/`-beta`/`-alpha` 포함 태그는 `--prerelease`

#### 필요한 시크릿

| 시크릿 | 용도 | 없으면 |
|---|---|---|
| `KDR_RELEASE_TOKEN` | 배포 저장소에 릴리스 생성 (contents:write PAT) | publish 잡이 명시적 에러로 실패 |
| `TAURI_PRIVATE_KEY` / `TAURI_KEY_PASSWORD` | 업데이터 서명 | 서명 없이 빌드 (아래 참고) |

> 기본 `GITHUB_TOKEN`으로는 안 된다 — 그 토큰의 권한은 워크플로가 도는 저장소에만 미친다.

#### 자산 이름

랜딩 페이지가 `releases/latest/download/<고정이름>`을 영구 URL로 쓰므로 **이름에 버전이 들어가면 안 된다**. 예전에는 `tauri-action`의 `releaseAssetNamePattern`이 이 일을 했는데, 업로드를 직접 하게 되면서 그 옵션이 안 먹으므로 `build` 잡의 `자산 이름 정규화` 단계가 확장자 기준으로 다시 붙인다.

| 플랫폼 | 자산 |
|---|---|
| macOS (Apple Silicon) | `kimdaeri-macos-aarch64.dmg` |
| Windows x64 | `kimdaeri-windows-x86_64-setup.exe` · `kimdaeri-windows-x86_64.msi` |

`productName`이 한글(`김대리`)이라 tauri의 기본 산출물명은 `김대리_0.1.0_aarch64.dmg`처럼 나온다 — URL 인코딩이 지저분해지므로 이 단계에서 ASCII로 눕힌다. `productName` 자체는 `.app` 이름·창 제목이라 바꾸지 않는다.

#### 자동 업데이트는 아직 동작하지 않는다

`tauri.conf.json`의 업데이터 엔드포인트는 배포 저장소를 가리키도록 고쳐뒀지만, **`pubkey`가 비어 있고 `latest.json`을 만드는 단계가 없다.** 켜려면 셋 다 필요하다:

1. `tauri signer generate`로 키쌍 생성 → 공개키를 `plugins.updater.pubkey`에, 비밀키를 `TAURI_PRIVATE_KEY` 시크릿에
2. `publish` 잡이 `latest.json`(버전·서명·플랫폼별 URL)을 만들어 함께 업로드
3. macOS는 `.app.tar.gz` + `.sig`가 자산에 포함돼야 한다 (이름 정규화 단계가 이미 다룬다)

### 로컬에서 설치파일 뽑기 (선택)

CI 없이 직접 만들려면 (윈도우 예시):
1. 사이드카 번들:
   ```
   cd services\sidecar
   uv sync
   uv pip install pyinstaller
   uv run pyinstaller --onefile --name office-claw-sidecar --hidden-import office_claw_sidecar --hidden-import uvicorn --hidden-import fastapi office_claw_sidecar\__main__.py
   dist\office-claw-sidecar.exe --smoke-test
   copy dist\office-claw-sidecar.exe ..\..\apps\desktop\src-tauri\binaries\office-claw-sidecar-x86_64-pc-windows-msvc.exe
   ```
   > `--hidden-import`는 **PyInstaller 정적 분석이 놓치는 것만** 적는다. xlwings의 플랫폼 백엔드(macOS `appscript`/`aem`, Windows `pythoncom`·`pywintypes`·`win32com`)와 keyring 백엔드는 PyInstaller 번들 훅이 이미 처리한다. 없는 모듈을 적으면 `ERROR: Hidden import 'x' not found`가 찍혀 진짜 실패를 가린다.
2. 설치파일 빌드:
   ```
   cd apps\desktop
   npm run tauri build
   ```
   → `src-tauri\target\release\bundle\` 에 `.msi`/`.exe` 생성.

### 크로스플랫폼 검증

`pr-check.yml`의 `python-check`는 **ubuntu에서만** 돈다 — 사이드카의 실제 타깃인 Windows·macOS 어느 쪽도 아니다. 그 구멍은 `.github/workflows/cross-platform-check.yml`이 메운다.

- **PR (경로 필터: `services/sidecar/**`·`packages/**`)** — Windows·macOS에서 `pytest` + **소스 기동 스모크**
- **`workflow_dispatch`** — 위에 더해 PyInstaller 번들까지 만들어 스모크

기동 스모크(`--smoke-test`)가 확인하는 것: FastAPI 앱 구성(import 사슬 전체) · `xlwings` · **플랫폼 백엔드**(macOS `appscript` / Windows `pythoncom`) · keyring 백엔드가 Null/Fail이 아닌지. 포트는 열지 않는다.

**플랫폼 백엔드는 우리 코드가 직접 import하지 않으므로 `pytest`로는 절대 안 걸린다** — 의존성 마커가 잘못되거나 PyInstaller가 놓치면 사용자가 엑셀 명령을 내리는 순간에야 드러난다.

### 알려진 이슈

- 자동 업데이트가 아직 동작하지 않는다(`pubkey` 미설정 + `latest.json` 미생성) — 위 "자동 업데이트는 아직 동작하지 않는다" 참고.

## 배포본 하드닝 (리버스 엔지니어링 대응)

전제부터 못박는다 — **사용자 기기에서 도는 코드는 원리상 100% 복원 가능하다.**
목표는 "못 보게"가 아니라 "볼 비용을 올리기"다. 아래는 그 비용이 사실상 0이던
지점들을 메운 것이고, 각 항목은 실제 산출물로 검증했다.

### 층별 노출도 (측정값)

| 층 | 조치 전 | 조치 후 |
|---|---|---|
| Python 사이드카 | PyInstaller CArchive → `PYZ.pyz`를 풀면 우리 모듈 56개 + **docstring 원문** + 함수/변수명이 전부 나옴 | docstring 제거(PYZ 19.2MB → 13.7MB). **모듈·함수 이름은 여전히 노출** |
| React 번들 | minify만. 청크 파일명이 `ActivityPage-*.js`처럼 화면 구성을 그대로 드러냄 | 해시 전용 이름. 소스맵 비활성 명시 |
| Rust | 심볼 테이블·소스 경로 문자열 잔존 | `strip` + `lto="fat"` |

### 조치 1 — Rust 심볼 제거 (`src-tauri/Cargo.toml`)

`[profile.release]`에 `strip`·`lto`·`codegen-units`. 근거는 그 파일 주석 참조.

macOS 실빌드 검증(3분 39초, 바이너리 10.6MB):

- `nm`이 내놓는 심볼 403개가 **전부 `U`(시스템 import)** — 우리 함수 이름은 0건.
- 남는 건 패닉 위치의 **파일 이름**(`src/ollama.rs` 등 5개)뿐이고, 그마저
  의존성 패닉 위치 ~130개(`src/ahocorasick.rs`, `src/verify_cert.rs` …) 사이에
  섞여 있다. **`panic = "abort"`로는 이게 안 지워진다** — unwind 테이블을 없앨
  뿐이다. 지우려면 nightly의 `-Z location-detail=none`이 필요하다.

릴리스 빌드 시간이 늘지만 PR 게이트(`pr-check.yml`)는 debug 프로파일이라 영향 없다.

### 조치 2 — PyInstaller `--optimize 2` (`release.yml`)

`python -OO` 상당이라 **docstring과 assert가 바이트코드 단계에서 사라진다.**
PyInstaller onefile은 `.pyc`를 담는 포장지일 뿐이라 아카이브를 풀면 모듈이 그대로
나오는데, 그중 가장 읽기 쉬운 부분(설계 의도를 적어둔 한국어 docstring)이 여기
전부 들어가 있었다.

부작용 두 가지를 **먼저 확인하고 넣었다**:

- `assert`가 사라지므로 assert로 런타임 불변식을 강제하면 안 된다 →
  `office_claw_sidecar/` 전체에 런타임 assert **0건**(테스트 코드는 번들에 안 들어감).
- FastAPI가 라우터 docstring을 OpenAPI 설명으로 쓴다 → `/docs` 문구만 비고,
  사용자에게 보이는 화면이 아니다. `__doc__`을 직접 읽는 코드도 **0건**.

macOS 실빌드로 검증: `--smoke-test`가 `xlwings 0.36.6 / appscript OK /
keyring backend: Keyring / OK`로 통과한다. **이 플래그를 만졌으면 스모크를 반드시
다시 돌릴 것** — `-OO`가 깨는 종류의 사고는 pytest로는 안 잡힌다(위 크로스플랫폼 노트와 같은 이유).

### 조치 3 — 프론트 번들 (`vite.config.js`)

`sourcemap: false`를 **명시**하고(기본값이지만 디버깅하다 켜놓고 릴리스하는 사고를
막는다), `chunkFileNames`를 해시 전용으로 바꿔 컴포넌트 이름을 뺐다.

### 조치 4 — 사이드카를 네이티브로 (Nuitka `--module` + PyInstaller spec)

위 셋을 다 해도 **PyInstaller는 `.pyc`를 담고 있어 디컴파일이 가능하다.** 실제로
CArchive → PYZ를 풀면 우리 모듈 42개가 그대로 나온다. 이걸 막는 건 파이썬을
네이티브로 컴파일하는 것뿐이다.

**전체를 Nuitka로 컴파일하는 안은 재보고 안 골랐다.** 세 방식을 같은 소스로 빌드해
실측한 결과다(Apple Silicon, 유휴 상태):

| | PyInstaller `-OO` | Nuitka 전체 `--onefile` | **하이브리드(채택)** |
|---|---|---|---|
| 사이드카 빌드 | 47초 | **490초** | 55초 (Nuitka 25 + PyInstaller 30) |
| 산출물 크기 | 41.0MB | 47.9MB | 38.3MB |
| 기동 시간(웜) | 4.84초 | **6.6~6.9초** | 5.1~5.2초 |
| PYZ 안 우리 모듈 | **42개** | (PYZ 없음) | **0개** |
| 우리 코드 디컴파일 | 가능 | 불가 | 불가 |

전체 컴파일이 비싼 이유는 단순하다 — **컴파일 대상 1766개 모듈 중 우리 코드는
47개(2%)뿐이다.** 나머지 98%는 pandas·numpy·openpyxl 같은 오픈소스라 보호할 이유가
없는데, 그것 때문에 빌드가 10배가 되고 기동이 2초 느려진다. macOS 러너 청구가
10배인 걸 감안하면 릴리스마다 실제 비용이다.

그래서 **`office_claw_sidecar` 패키지 하나만** Nuitka `--module`로 확장 모듈
(`.so`/`.pyd`, 2.6MB)로 만들고, PyInstaller는 `sidecar-hardened.spec`으로 돈다.
핵심은 **분석은 소스로, 번들은 `.so`로**다 — 소스로 분석해야 fastapi·uvicorn·
xlwings 같은 전이 의존을 빠짐없이 찾고(`.so`만 주면 PyInstaller가 그 안을 못 봐서
전부 손으로 `--hidden-import`를 적어야 한다), 번들에는 `.pyc` 대신 `.so`가 들어간다.

검증(macOS, CI와 같은 명령. 빌드 52초 / 산출물 37MB):

- `--smoke-test` 통과 — `frozen=True / xlwings 0.36.6 / appscript OK /
  keyring backend: Keyring / OK`. 네이티브 컴파일 후에도 플랫폼 백엔드가 붙는다.
- CArchive에 `office_claw_sidecar.cpython-312-darwin.so` **1개**, PYZ 1726개 모듈 중
  **우리 모듈 0개**.
- 기동 후 `/health` 정상, 자격증명 저장→조회→삭제 왕복 정상(= keyring OS 백엔드가
  살아 있다).
- 바이너리에서 시스템 프롬프트·docstring 문자열 **0건**.

**남는 노출**: `.so` 안에도 모듈 이름과 함수 이름은 문자열/심볼로 남는다(Nuitka가
import 기계장치에 쓴다). 사라지는 건 **로직**이다 — 디컴파일러가 걸 대상이 없다.
이름만으로는 알고리즘이 복원되지 않으므로 여기까지를 목표로 잡았다.

> **macOS는 CI에서도 확인됐다** — GitHub 러너에서 `Cross-platform check`(bundle)로
> 하이브리드 빌드 전체가 돌고 번들 스모크가 `frozen=True / appscript OK /
> keyring backend: Keyring / OK`로 통과한다.
>
> **Windows 경로는 아직 macOS만큼 실측하지 못했다.** Nuitka의 Windows 빌드는
> MSVC를 타고 `.pyd`를 내놓는데, 손에 macOS밖에 없어 직접 돌려보지 못했다.
>
> **태그를 밀기 전에 `Cross-platform check`를 `workflow_dispatch`로 한 번 돌릴 것**
> (`bundle: true`). 그 잡이 Windows·macOS 양쪽에서 이 문서와 **같은 2단계 명령**으로
> 번들을 만들고 `--smoke-test`까지 돌린다 — 그러라고 `release.yml`과 명령을 맞춰
> 뒀으니, 한쪽만 고치면 그 검증이 무의미해진다.
>
> 그걸 건너뛰어도 반쪽 배포는 나지 않는다 — Windows 빌드가 깨지면 `build` 잡이
> 죽고, 릴리스를 만드는 `publish`는 `needs: build`라 아예 실행되지 않는다.

### 버린 것 — `office_claw_sidecar.spec`

모노레포 재편(`d32dd44`) 때 들어온 spec이 하나 있었는데 **아무도 안 쓰는 죽은
파일**이었다(CI는 CLI 형태로 PyInstaller를 돌렸다). 게다가 지금 돌리면 깨진다 —
엔트리가 `main.py`(`__main__.py`여야 한다. 이유는 그 파일 docstring 참조),
`block_cipher`/`a.zipped_data`는 PyInstaller 6에서 없어진 API, hiddenimports에
이미 제거된 `telegram`·`googleapiclient`·`google_auth_oauthlib`가 남아 있다.
진짜 spec 옆에 깨진 동명이인을 두면 다음 사람이 그걸 고치려 든다.

거기 있던 `console=False`(윈도우 콘솔 창 방지)는 **가져오지 않았다.** `sidecar.rs`가
사이드카의 stdout/stderr를 읽어 로그로 남기는데(`CommandEvent::Stdout`) windowed
빌드는 그 출력을 잃는다. 콘솔 창은 `tauri-plugin-shell`이 이미 막는다.

### 하지 않기로 한 것

- **JS 난독화기**(javascript-obfuscator 등): 런타임 성능·디버깅 비용 대비 얻는 게
  거의 없다. minify + 파일명 정리로 충분하다.
- **로직을 서버로 이전**: 일반적으로는 최선책이지만 이 제품은 "로컬에서 돈다"가
  셀링 포인트라 해당 없다.
- **`panic = "abort"`**: 얻는 게 바이너리 크기뿐이고(위 참고: 파일 이름은 그대로
  남는다) unwind 동작이 바뀐다. 하드닝 목적으로는 값어치가 없다.
