//! 설치 명령 실행 — macOS GUI PATH 문제 우회 + 실시간 로그 스트리밍.
//!
//! ## 왜 별도 모듈인가
//!
//! Tauri GUI 앱은 launchd가 주는 최소 PATH(`/usr/bin:/bin:/usr/sbin:/sbin`)만
//! 상속받는다. `brew`/`ollama`는 `/usr/local/bin`, `/opt/homebrew/bin` 등에 있어
//! GUI PATH에 잡히지 않는다.
//!
//! `@tauri-apps/plugin-shell`의 `Command.create("brew", ...)`는 이 PATH를 그대로
//! 사용하기 때문에 `brew: not found`로 즉시 실패한다. `ollama::is_ollama_installed`는
//! 이미 `$SHELL -lc "..."` 폴백을 구현해 이 문제를 회피하고 있다.
//!
//! 이 모듈은 **설치 명령(brew/winget/ollama)에도 같은 패턴을 적용**해서:
//!   1. 사용자 로그인 셸($SHELL)로 `-lc` 호출 → ~/.zshrc / ~/.bashrc / /etc/profile
//!      을 모두 거쳐 nvm/asdf/Homebrew PATH가 잡힌다
//!   2. stdout/stderr 라인을 Tauri 이벤트(`installer:log`)로 스트리밍 → 프론트에서
//!      실시간 로그 표시
//!   3. stderr 마지막 N줄을 캡처해서 결과에 포함 → 실패 시 사용자에게 의미 있는
//!      메시지 노출 (단순 "종료 코드 13"이 아니라 npm ERR! 본문)
//!   4. `EACCES`/`permission denied` 감지 → eacces:true 플래그로 프론트에 전달
//!      → sudo 안내 UI로 분기
//!
//! ## 취소
//!
//! 진행 중인 설치 자식 PID를 InstallerState에 저장. `cancel_install`은 그 PID에
//! `kill -TERM` (subprocess)를 보낸다. PID는 Copy 타입이라 Mutex<Child>의 ownership
//! 문제(wait는 &mut Child 필요)를 피할 수 있다.

use serde::Serialize;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, State};

/// stderr 꼬리 캡처 라인 수 — 실패 시 사용자에게 보여줄 컨텍스트
const STDERR_TAIL_LINES: usize = 20;

/// 진행 중인 설치 자식 프로세스의 PID 저장소.
///
/// Child 자체 대신 PID만 저장하는 이유:
///   - Child::wait()는 &mut Child 필요 → Mutex<Child>에 락을 잡고 wait하면
///     cancel command가 락을 못 얻음
///   - PID는 Copy 타입이라 lock을 짧게 잡고 읽기만 하면 됨 → cancel과 race 없음
#[derive(Default)]
pub struct InstallerState {
    pub current_pid: Option<u32>,
}

/// 실시간 로그 이벤트 payload — 프론트가 `installer:log` 리스너로 받는다.
#[derive(Serialize, Clone)]
pub struct InstallLogEvent {
    /// LocalAISetupWizard의 STEP 값 (예: "install-oc")
    pub step: String,
    /// "stdout" | "stderr" | "info"
    pub kind: String,
    pub text: String,
}

/// 설치 결과 — 프론트가 invoke 반환값으로 받는다.
#[derive(Serialize)]
pub struct InstallResult {
    /// 성공 여부 (exit code == 0)
    pub ok: bool,
    /// 프로세스 종료 코드
    pub code: Option<i32>,
    /// stderr 마지막 N줄 (실패 컨텍스트 표시용)
    pub stderr_tail: Vec<String>,
    /// EACCES/permission denied 감지 플래그 — 프론트에서 sudo 안내 UI로 분기
    pub eacces: bool,
    /// 사용자에게 보여줄 짧은 요약 메시지
    pub message: String,
    /// 사용자가 직접 실행하라고 안내할 수 있는 수동 명령 (실패 시 복사 제공용)
    pub manual_command: String,
}

/// 헬퍼: 이벤트 emit (실패는 조용히 무시 — frontend가 이미 unmount됐을 수 있음)
fn emit_log(app: &AppHandle, step: &str, kind: &str, text: String) {
    let _ = app.emit(
        "installer:log",
        InstallLogEvent {
            step: step.to_string(),
            kind: kind.to_string(),
            text,
        },
    );
}

/// 코어 실행기 — 플랫폼별 로그인 셸을 사용해 명령을 실행하고 stdout/stderr를
/// 라인 단위 이벤트로 발행한다.
///
/// async지만 내부는 blocking IO — Tauri command 자체가 별도 tokio task에서
/// 실행되므로 한 install 동안 한 worker thread를 점유한다. desktop 앱에서
/// 동시 작업이 적으므로 spawn_blocking 없이 직접 실행.
fn run_shell_streaming(
    app: AppHandle,
    state: &State<'_, Mutex<InstallerState>>,
    step_id: &str,
    shell_cmd: &str,
    manual_command: &str,
) -> Result<InstallResult, String> {
    // 사용자가 보기 부담스러운 내부 셸 명령 echo는 띄우지 않는다.
    // 친화적 안내 한 줄로 시작 — 이후 실제 stdout/stderr 라인이 따라옴.
    emit_log(&app, step_id, "info", "작업을 시작합니다...".to_string());

    #[cfg(target_os = "windows")]
    let mut child = Command::new("powershell")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            shell_cmd,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_e| "작업을 시작하지 못했어요. 잠시 후 다시 시도해주세요.".to_string())?;

    #[cfg(not(target_os = "windows"))]
    let mut child = {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
        Command::new(&shell)
            .args(["-lc", shell_cmd])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_e| "작업을 시작하지 못했어요. 잠시 후 다시 시도해주세요.".to_string())?
    };

    // PID 등록 — cancel_install이 이 PID에 kill 시그널을 보낼 수 있도록.
    let pid = child.id();
    {
        let mut s = state.lock().map_err(|e| e.to_string())?;
        s.current_pid = Some(pid);
    }

    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");

    // stdout 스트리밍 스레드
    let app_out = app.clone();
    let step_out = step_id.to_string();
    let stdout_thread = std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            emit_log(&app_out, &step_out, "stdout", line);
        }
    });

    // stderr 스트리밍 + 꼬리 캡처 스레드
    let app_err = app.clone();
    let step_err = step_id.to_string();
    let stderr_thread = std::thread::spawn(move || -> Vec<String> {
        let mut tail: Vec<String> = Vec::with_capacity(STDERR_TAIL_LINES + 4);
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            emit_log(&app_err, &step_err, "stderr", line.clone());
            tail.push(line);
            if tail.len() > STDERR_TAIL_LINES {
                let drop_count = tail.len() - STDERR_TAIL_LINES;
                tail.drain(0..drop_count);
            }
        }
        tail
    });

    // child wait — lock을 잡지 않으므로 cancel과 race 없음.
    let status = child
        .wait()
        .map_err(|e| format!("프로세스 wait 실패: {}", e))?;

    // PID 클리어 — 이후 cancel은 no-op
    {
        let mut s = state.lock().map_err(|e| e.to_string())?;
        if s.current_pid == Some(pid) {
            s.current_pid = None;
        }
    }

    let _ = stdout_thread.join();
    let stderr_tail = stderr_thread.join().unwrap_or_default();

    let code = status.code();
    let ok = status.success();
    let combined_stderr = stderr_tail.join("\n").to_lowercase();
    let eacces = combined_stderr.contains("eacces")
        || combined_stderr.contains("permission denied")
        || combined_stderr.contains("operation not permitted");

    // 사용자에게 보일 짧은 요약 메시지 — 기술 정보(종료 코드 등)는 별도 필드(`code`)로
    // 전달하고, message는 일반 사용자가 한눈에 이해할 수 있게 작성.
    let message = if ok {
        "완료".to_string()
    } else if eacces {
        "관리자 권한이 필요해요".to_string()
    } else {
        "작업에 실패했어요".to_string()
    };

    Ok(InstallResult {
        ok,
        code,
        stderr_tail,
        eacces,
        message,
        manual_command: manual_command.to_string(),
    })
}

/// macOS 공식 Ollama 배포본 (Ollama.app) — ollama.com이 브라우저에 주는 것과 같은 파일.
#[cfg(target_os = "macos")]
const OLLAMA_MACOS_ZIP: &str = "https://ollama.com/download/Ollama-darwin.zip";

/// Windows 공식 Ollama 설치 프로그램 (Inno Setup 6). winget이 없을 때만 쓴다.
#[cfg(target_os = "windows")]
const OLLAMA_WINDOWS_SETUP: &str = "https://ollama.com/download/OllamaSetup.exe";

/// Tauri command: Ollama 설치.
///
/// ## 패키지 매니저를 전제하지 않는다
///
/// 예전에는 macOS에서 `brew install ollama`를 돌렸다. 그런데 **Homebrew 자체가
/// 사용자가 따로 설치해야 하는 물건**이라, 초기 상태의 Mac에서는 `brew: command
/// not found`로 죽는다. 우리 사용자는 비개발자를 상정하므로 brew가 있을 것이라
/// 기대할 수 없다. 게다가 brew로 깐 것은 CLI 포뮬러라, 공식 앱(Ollama.app)을
/// 이미 쓰던 사용자에게는 **두 번째 사본**이 생긴다.
///
/// 그래서 macOS는 ollama.com이 브라우저에 주는 것과 **같은 배포본**(약 181MB)을
/// 직접 받아 `/Applications`에 설치한다. 의존성이 없다.
///
/// Windows는 반대로 `winget`이 OS에 기본 탑재라(Win10 1709+·Win11) 전제해도
/// 안전하고, 1.5GB짜리 설치 프로그램의 다운로드·재시도를 winget이 대신 해준다.
/// 그래서 winget을 우선 쓰고, 없는 구형 Windows에서만 공식 설치 프로그램을 받는다.
#[tauri::command]
pub async fn install_ollama(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "macos")]
    {
        // `ditto`를 쓰는 이유: macOS 네이티브 아카이버라 코드 서명과 확장 속성을
        // 보존한다. `unzip`으로 풀면 서명이 깨져 Gatekeeper가 앱을 거부할 수 있다.
        //
        // 진행률: curl의 --progress-bar는 캐리지 리턴만 쓰므로 줄 단위로 읽는
        // 우리 스트리밍 로그에 한 줄도 안 나오다가 끝에 몰려 나온다. 181MB를
        // 아무 표시 없이 기다리게 두지 않으려고, 받는 동안 파일 크기를 직접 찍는다.
        let cmd = format!(
            r#"set -e
APP_DIR="/Applications"
if [ ! -w "$APP_DIR" ]; then
  APP_DIR="$HOME/Applications"
  echo "/Applications에 쓸 수 없어 $APP_DIR 에 설치합니다."
fi
mkdir -p "$APP_DIR"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "로컬 AI 엔진을 내려받는 중입니다 (약 181MB)..."
curl -fL -s -o "$TMP/Ollama.zip" "{url}" &
CURL_PID=$!
while kill -0 $CURL_PID 2>/dev/null; do
  sleep 3
  SZ=$(stat -f%z "$TMP/Ollama.zip" 2>/dev/null || echo 0)
  echo "내려받는 중... $((SZ / 1048576))MB"
done
wait $CURL_PID

echo "압축을 푸는 중입니다..."
ditto -x -k "$TMP/Ollama.zip" "$TMP/out"
if [ ! -d "$TMP/out/Ollama.app" ]; then
  echo "내려받은 파일에서 프로그램을 찾지 못했습니다." >&2
  exit 1
fi

echo "설치하는 중입니다..."
rm -rf "$APP_DIR/Ollama.app"
ditto "$TMP/out/Ollama.app" "$APP_DIR/Ollama.app"
# curl로 받은 파일에는 보통 quarantine이 붙지 않지만, 붙어 있으면 첫 실행이 막힌다.
xattr -dr com.apple.quarantine "$APP_DIR/Ollama.app" 2>/dev/null || true
echo "설치 완료: $APP_DIR/Ollama.app""#,
            url = OLLAMA_MACOS_ZIP,
        );
        let result = run_shell_streaming(
            app,
            &state,
            "install-ollama",
            &cmd,
            "https://ollama.com/download 에서 내려받아 설치",
        )?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(target_os = "windows")]
    {
        // --silent --disable-interactivity: 설치 UI/프롬프트가 떠서 스트리밍이
        // 멈추는 것을 막는다 (무인 설치 안정성).
        //
        // winget이 없는 구형 Windows에서는 공식 설치 프로그램을 직접 받는다.
        // Inno Setup 6이므로 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART가 맞는
        // 무인 설치 스위치다 (설치 프로그램 실물로 확인).
        let cmd = format!(
            concat!(
                "if (Get-Command winget -ErrorAction SilentlyContinue) {{ ",
                "  winget install -e --id Ollama.Ollama --silent --disable-interactivity ",
                "    --accept-package-agreements --accept-source-agreements ",
                "}} else {{ ",
                "  Write-Output '로컬 AI 엔진 설치 프로그램을 내려받습니다 (약 1.5GB)...'; ",
                // Windows PowerShell 5.1의 Invoke-WebRequest는 진행률 막대를 그리느라
                // 대용량 다운로드가 수십 배 느려진다. 1.5GB에서는 치명적이라 끈다.
                "  $ProgressPreference = 'SilentlyContinue'; ",
                "  $tmp = Join-Path $env:TEMP 'OllamaSetup.exe'; ",
                "  Invoke-WebRequest -Uri '{url}' -OutFile $tmp -UseBasicParsing; ",
                "  Write-Output ('내려받기 완료: ' + [math]::Round((Get-Item $tmp).Length / 1MB) + 'MB'); ",
                "  Write-Output '설치하는 중입니다...'; ",
                "  Start-Process -FilePath $tmp -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait; ",
                "  Remove-Item $tmp -ErrorAction SilentlyContinue; ",
                "  Write-Output '설치 완료' ",
                "}}"
            ),
            url = OLLAMA_WINDOWS_SETUP,
        );
        let result = run_shell_streaming(
            app,
            &state,
            "install-ollama",
            &cmd,
            "winget install -e --id Ollama.Ollama",
        )?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (app, state);
        Err("자동 설치는 현재 macOS/Windows만 지원됩니다. https://ollama.com/download 에서 직접 설치해 주세요.".to_string())
    }
}

/// Tauri command: Ollama 데몬 시작.
///
/// ## 탐지와 시작의 기준을 맞춘다
///
/// 예전 macOS 경로는 `brew services start ollama`였는데, 이건 **brew 포뮬러로
/// 깐 경우에만** 동작한다. 공식 앱(Ollama.app)으로 설치한 사용자는 탐지는
/// 성공하고(`/usr/local/bin/ollama` 심볼릭 링크가 PATH에 잡힌다) 시작만
/// 실패한다 — 그것도 `Error: Formula 'ollama' is not installed.` 라는, 이미
/// Ollama를 설치한 사용자에게는 뜻이 통하지 않는 메시지로.
///
/// 그래서 `macos_ollama_app`/`macos_ollama_exe`가 찾아낸 **실물 경로**로 띄운다.
/// 앱이 있으면 `open`(메뉴막대 아이콘과 자동 시작까지 딸려 온다), 앱은 없고
/// 실행 파일만 있으면 `serve`를 직접 띄운다.
#[tauri::command]
pub async fn start_ollama(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "macos")]
    {
        let cmd = match crate::ollama::macos_ollama_app() {
            Some(app_path) => format!("open -a \"{}\"", app_path.display()),
            None => match crate::ollama::macos_ollama_exe() {
                // nohup + & : 이 셸이 끝나도 데몬이 살아남아야 한다.
                Some(exe) => format!(
                    "nohup \"{}\" serve >/dev/null 2>&1 & echo '로컬 AI 엔진을 시작했습니다.'",
                    exe.display()
                ),
                None => {
                    return serde_json::to_value(InstallResult {
                        ok: false,
                        code: None,
                        stderr_tail: Vec::new(),
                        eacces: false,
                        message:
                            "이 Mac에서 로컬 AI 엔진을 찾지 못했어요. 먼저 설치 단계를 실행해 주세요."
                                .to_string(),
                        manual_command: "open -a Ollama".to_string(),
                    })
                    .map_err(|e| e.to_string());
                }
            },
        };
        let result = run_shell_streaming(app, &state, "start-ollama", &cmd, "open -a Ollama")?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(target_os = "windows")]
    {
        let start_cmd = "$paths = @(\"$env:LOCALAPPDATA\\Programs\\Ollama\\Ollama.exe\", \"$env:ProgramFiles\\Ollama\\Ollama.exe\"); \
$exe = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1; \
if ($exe) { Start-Process -FilePath $exe } else { Start-Process -FilePath \"ollama\" -ArgumentList \"serve\" }";
        let result = run_shell_streaming(
            app,
            &state,
            "start-ollama",
            start_cmd,
            "Start-Process \"$env:LOCALAPPDATA\\Programs\\Ollama\\Ollama.exe\"",
        )?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (app, state);
        Err("자동 시작은 현재 macOS/Windows만 지원됩니다.".to_string())
    }
}

/// Tauri command: Ollama 모델 다운로드 — `ollama pull <model>`
///
/// `model`은 ollama 모델명/태그 — 보안을 위해 `validate_model_name`으로 검사.
///
/// ## 탐지와 실행의 기준을 맞춘다
///
/// `is_ollama_installed`는 표준 설치 경로의 `ollama.exe`(PATH에 없어도)와 **데몬 응답**을
/// 근거로 "설치됨"을 판정한다. 반면 여기서 맨 이름 `ollama`를 실행하면 PATH에만 의존하므로
/// "설치됨으로 보이는데 다운로드는 실패"하는 불일치가 생긴다. 실제로 Windows는 설치 직후
/// 이미 실행 중인 프로세스에 PATH 갱신이 반영되지 않아 이 상황이 흔하다.
///
/// 그래서 (1) 탐지와 같은 경로 목록으로 절대경로 실행을 먼저 시도하고,
/// (2) 실행 파일이 어디에도 없는데 데몬만 응답하면(WSL·원격 데몬) 셸 오류 대신
/// 무엇을 해야 하는지 알려주는 결과를 돌려준다.
#[tauri::command]
pub async fn pull_ollama_model(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
    model: String,
) -> Result<serde_json::Value, String> {
    // 모델명에 shell metachar 끼어들어가지 않도록 사전 검증
    crate::ollama::validate_model_name(&model)?;

    let manual = format!("ollama pull {}", model);

    #[cfg(target_os = "windows")]
    let cmd = match crate::ollama::windows_ollama_exe() {
        // 경로에 공백이 있을 수 있어 따옴표 + 호출 연산자(&)로 실행한다.
        Some(exe) => format!("& \"{}\" pull {}", exe.display(), model),
        None => {
            if crate::ollama::is_ollama_running().await {
                return serde_json::to_value(InstallResult {
                    ok: false,
                    code: None,
                    stderr_tail: Vec::new(),
                    eacces: false,
                    message: format!(
                        "로컬 AI 엔진은 응답하지만 이 PC에서 실행 파일을 찾지 못했어요. \
                         WSL이나 다른 기기의 데몬을 쓰고 있을 수 있습니다 — 그 환경에서 \
                         `ollama pull {}`을 직접 실행해 주세요.",
                        model
                    ),
                    manual_command: manual.clone(),
                })
                .map_err(|e| e.to_string());
            }
            manual.clone()
        }
    };

    // macOS도 Windows와 같은 이유로 절대경로를 먼저 쓴다. 공식 앱은 첫 실행 때
    // 사용자가 승인해야 `/usr/local/bin/ollama` 링크를 만들므로, 승인 전에는
    // 탐지(앱 번들 확인)는 성공하는데 맨 이름 `ollama`는 PATH에 없어 실패한다.
    #[cfg(target_os = "macos")]
    let cmd = match crate::ollama::macos_ollama_exe() {
        Some(exe) => format!("\"{}\" pull {}", exe.display(), model),
        None => {
            if crate::ollama::is_ollama_running().await {
                return serde_json::to_value(InstallResult {
                    ok: false,
                    code: None,
                    stderr_tail: Vec::new(),
                    eacces: false,
                    message: format!(
                        "로컬 AI 엔진은 응답하지만 이 Mac에서 실행 파일을 찾지 못했어요. \
                         다른 기기의 데몬을 쓰고 있을 수 있습니다 — 그 환경에서 \
                         `ollama pull {}`을 직접 실행해 주세요.",
                        model
                    ),
                    manual_command: manual.clone(),
                })
                .map_err(|e| e.to_string());
            }
            manual.clone()
        }
    };

    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let cmd = manual.clone();

    let result = run_shell_streaming(app, &state, "pull-model", &cmd, &manual)?;
    serde_json::to_value(result).map_err(|e| e.to_string())
}

/// Tauri command: 진행 중인 설치 취소.
///
/// 저장된 PID에 SIGTERM을 보낸다. PID가 없으면 NO-OP.
/// `/bin/kill -TERM` subprocess를 사용해 libc 의존 없이 처리.
#[tauri::command]
pub fn cancel_install(state: State<'_, Mutex<InstallerState>>) -> Result<(), String> {
    let pid = {
        let s = state.lock().map_err(|e| e.to_string())?;
        s.current_pid
    };
    if let Some(p) = pid {
        // SIGTERM은 npm/brew의 자식 프로세스에도 전파됨 (프로세스 그룹).
        // 결과: install이 깔끔하게 중단되고 wait이 비정상 종료 코드로 리턴됨.
        #[cfg(unix)]
        {
            let _ = Command::new("kill")
                .args(["-TERM", &p.to_string()])
                .status();
        }
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/PID", &p.to_string(), "/T", "/F"])
                .status();
        }
    }
    Ok(())
}
