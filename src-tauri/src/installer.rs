//! 설치 명령 실행 — macOS GUI PATH 문제 우회 + 실시간 로그 스트리밍.
//!
//! ## 왜 별도 모듈인가
//!
//! Tauri GUI 앱은 launchd가 주는 최소 PATH(`/usr/bin:/bin:/usr/sbin:/sbin`)만
//! 상속받는다. `npm`/`brew`/`ollama`는 `/usr/local/bin`, `/opt/homebrew/bin`,
//! `~/.nvm/...` 등에 있어 GUI PATH에 잡히지 않는다.
//!
//! `@tauri-apps/plugin-shell`의 `Command.create("npm", ...)`는 이 PATH를 그대로
//! 사용하기 때문에 `npm: not found`로 즉시 실패한다. 같은 프로젝트의
//! `openclaw::is_openclaw_installed` / `spawn_openclaw` / `configure_openclaw_ollama`는
//! 이미 `$SHELL -lc "..."` 폴백을 구현해 이 문제를 회피하고 있다.
//!
//! 이 모듈은 **설치 명령(npm/brew/ollama)에도 같은 패턴을 적용**해서:
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

/// Tauri command: Node.js 설치 — OpenClaw(npm 글로벌 패키지)의 선행 조건.
///
/// - Windows: `winget install -e --id OpenJS.NodeJS.LTS ...` (무인 설치, LTS 채널이
///   가장 안정적). `--silent --disable-interactivity`로 설치 UI/프롬프트가 떠서
///   스트리밍이 멈추는 것을 막는다.
/// - macOS: `brew install node`
#[tauri::command]
pub async fn install_node(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "windows")]
    {
        let cmd = "winget install -e --id OpenJS.NodeJS.LTS --silent --disable-interactivity --accept-package-agreements --accept-source-agreements";
        let result = run_shell_streaming(app, &state, "install-node", cmd, cmd)?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(target_os = "macos")]
    {
        let result = run_shell_streaming(
            app,
            &state,
            "install-node",
            "brew install node",
            "brew install node",
        )?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (app, state);
        Err("자동 설치는 현재 macOS/Windows만 지원됩니다. https://nodejs.org 에서 직접 설치해 주세요.".to_string())
    }
}

/// Tauri command: OpenClaw 전역 설치 — `npm install -g openclaw@latest`
///
/// manual_command에는 sudo를 포함하지 않는다 — 프론트에서 result.eacces 플래그를
/// 보고 EACCES 케이스에만 "sudo " 프리픽스를 붙여 표시한다.
#[tauri::command]
pub async fn install_openclaw(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    let result = run_shell_streaming(
        app,
        &state,
        "install-oc",
        "npm install -g openclaw@latest",
        "npm install -g openclaw@latest",
    )?;
    serde_json::to_value(result).map_err(|e| e.to_string())
}

/// Tauri command: Ollama 설치.
/// - macOS: `brew install ollama`
/// - Windows: `winget install -e --id Ollama.Ollama ...`
#[tauri::command]
pub async fn install_ollama(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "macos")]
    {
        let result = run_shell_streaming(
            app,
            &state,
            "install-ollama",
            "brew install ollama",
            "brew install ollama",
        )?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(target_os = "windows")]
    {
        // --silent --disable-interactivity: 설치 UI/프롬프트가 떠서 스트리밍이
        // 멈추는 것을 막는다 (무인 설치 안정성).
        let cmd = "winget install -e --id Ollama.Ollama --silent --disable-interactivity --accept-package-agreements --accept-source-agreements";
        let result = run_shell_streaming(app, &state, "install-ollama", cmd, cmd)?;
        serde_json::to_value(result).map_err(|e| e.to_string())
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (app, state);
        Err("자동 설치는 현재 macOS/Windows만 지원됩니다. https://ollama.com/download 에서 직접 설치해 주세요.".to_string())
    }
}

/// Tauri command: Ollama 데몬 시작.
/// - macOS: `brew services start ollama`
/// - Windows: Ollama 앱 프로세스 시작
#[tauri::command]
pub async fn start_ollama(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "macos")]
    {
        let result = run_shell_streaming(
            app,
            &state,
            "start-ollama",
            "brew services start ollama",
            "brew services start ollama",
        )?;
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
#[tauri::command]
pub async fn pull_ollama_model(
    app: AppHandle,
    state: State<'_, Mutex<InstallerState>>,
    model: String,
) -> Result<serde_json::Value, String> {
    // 모델명에 shell metachar 끼어들어가지 않도록 사전 검증
    crate::ollama::validate_model_name(&model)?;

    let cmd = format!("ollama pull {}", model);
    let manual = format!("ollama pull {}", model);
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
