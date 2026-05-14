# PyInstaller Build Guide - Office Claw Sidecar

Python FastAPI 사이드카를 독립 실행 파일로 빌드하여 Tauri Sidecar로 배포하는 가이드.

## 사전 요구사항

```bash
cd python-sidecar
pip install -r requirements.txt
pip install pyinstaller
```

## 빌드 방법

### 자동 빌드 (권장)

```bash
cd python-sidecar
python build_sidecar.py
```

이 스크립트는:
1. PyInstaller로 독립 실행 파일 생성
2. `src-tauri/binaries/`에 Tauri 네이밍 규칙에 맞게 복사

### 수동 빌드

```bash
cd python-sidecar
pyinstaller --clean --noconfirm office_claw_sidecar.spec
```

## 출력 파일 네이밍

Tauri는 `externalBin`에 선언된 이름에 플랫폼별 타겟 트리플을 자동 추가합니다:

| 플랫폼 | 출력 파일명 |
|--------|-----------|
| Windows x64 | `office-claw-sidecar-x86_64-pc-windows-msvc.exe` |
| macOS Intel | `office-claw-sidecar-x86_64-apple-darwin` |
| macOS Apple Silicon | `office-claw-sidecar-aarch64-apple-darwin` |
| Linux x64 | `office-claw-sidecar-x86_64-unknown-linux-gnu` |

## Hidden Imports 설명

PyInstaller는 정적 분석으로 의존성을 찾으므로, 동적 임포트를 사용하는 라이브러리는 명시적으로 선언해야 합니다:

### keyring 백엔드
```python
'keyring.backends.Windows',      # Windows Credential Manager
'keyring.backends.macOS',        # macOS Keychain
'keyring.backends.SecretService', # Linux D-Bus Secret Service
```
**누락 시 증상**: `keyring.errors.NoKeyringError` - 런타임에서 키링 백엔드를 찾지 못함

### uvicorn 내부 모듈
```python
'uvicorn.logging',
'uvicorn.loops.auto',
'uvicorn.loops.asyncio',
'uvicorn.protocols.http.auto',
'uvicorn.protocols.http.h11_impl',
```
**누락 시 증상**: 사이드카 시작 시 `ModuleNotFoundError`로 크래시

## 플랫폼별 주의사항

### Windows
- **안티바이러스 오탐**: PyInstaller 바이너리는 종종 Windows Defender에 의해 차단됩니다.
  - 해결: 코드 사이닝 인증서로 서명하거나, 개발 시 제외 규칙 추가
- **콘솔 창**: `console=False`로 설정되어 있어 콘솔 창이 표시되지 않음
- **크기**: 약 50-80MB (UPX 압축 적용 시)

### macOS
- **Gatekeeper**: 서명되지 않은 바이너리는 차단됨
  - 개발 시: `xattr -cr dist/office-claw-sidecar-*`
  - 배포 시: `codesign --sign "Developer ID" dist/office-claw-sidecar-*`
- **Keychain 접근**: 최초 실행 시 Keychain 접근 프롬프트 표시
  - 코드사이닝하면 반복 프롬프트 방지 가능

### Linux
- **Secret Service**: GNOME Keyring 또는 KWallet이 필요
  - 없는 환경에서는 `keyring`이 PlaintextKeyring으로 폴백 (경고 표시)
- **의존 라이브러리**: `libsecret`가 시스템에 설치되어 있어야 함
  ```bash
  # Ubuntu/Debian
  sudo apt install libsecret-1-0
  # Fedora
  sudo dnf install libsecret
  ```

## 콜드 스타트 최적화

현재 원파일(one-file) 모드를 사용하며, 첫 실행 시 임시 디렉토리에 압축 해제하므로 3-5초 지연이 있습니다.

시작 시간이 중요한 경우 원디렉토리(one-dir) 모드로 전환할 수 있습니다:
1. `.spec` 파일에서 `EXE`의 `a.binaries, a.datas`를 제거
2. `COLLECT` 블록 추가
3. `tauri.conf.json`에서 `externalBin` 대신 `resources`로 전체 디렉토리 번들

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `NoKeyringError` | keyring 백엔드 hidden import 누락 | `.spec`의 `hiddenimports` 확인 |
| 사이드카 시작 후 즉시 종료 | uvicorn 모듈 누락 | uvicorn hidden imports 확인 |
| Windows Defender 차단 | PyInstaller 바이너리 오탐 | 코드 사이닝 또는 제외 규칙 |
| macOS "개발자를 확인할 수 없음" | Gatekeeper | `xattr -cr` 또는 코드사이닝 |
| 리눅스 keyring 오류 | libsecret 미설치 | `apt install libsecret-1-0` |
